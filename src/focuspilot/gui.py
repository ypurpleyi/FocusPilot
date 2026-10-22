"""Simple always-visible Windows panel. Qt GUI is intentionally optional for unit tests."""

from datetime import datetime
import argparse
import sys

from PySide6.QtCore import Qt, QDateTime, QTimer
from PySide6.QtGui import QAction, QColor, QPalette, QSessionManager
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyle,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

from .core import reminder_to_send, sort_tasks, urgency
from .storage import TaskStore, default_db_path


class AddTaskDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("新增待办")
        self.setMinimumWidth(340)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("任务内容"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("例如：整理项目报告")
        layout.addWidget(self.title_edit)
        self.has_due = QCheckBox("设置截止时间")
        self.has_due.setChecked(True)
        layout.addWidget(self.has_due)
        self.due_edit = QDateTimeEdit(QDateTime.currentDateTime().addDays(1))
        self.due_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.due_edit.setCalendarPopup(True)
        self.has_due.toggled.connect(self.due_edit.setEnabled)
        layout.addWidget(self.due_edit)
        layout.addWidget(QLabel("重要程度"))
        self.importance = QComboBox()
        self.importance.addItem("低", 1)
        self.importance.addItem("中", 2)
        self.importance.addItem("高", 3)
        self.importance.setCurrentIndex(1)
        layout.addWidget(self.importance)
        layout.addWidget(QLabel("预计耗时（分钟）"))
        self.estimate = QSpinBox()
        self.estimate.setRange(1, 1440)
        self.estimate.setValue(30)
        layout.addWidget(self.estimate)
        self.error = QLabel()
        self.error.setStyleSheet("color: #b42318;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        if not self.title_edit.text().strip():
            self.error.setText("任务标题不能为空")
            self.title_edit.setFocus()
            return
        super().accept()


class FocusWindow(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store if store is not None else TaskStore()
        self.quitting = False
        self.capture_dialog = None
        self.hotkey = None
        self.startup_manager = None
        self._startup_enabled = False
        self._cleaned_up = False
        self.setWindowTitle("FocusPilot · 今日待办")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(390, 580)
        self.setStyleSheet("""
            QMainWindow { background: #f7f8fc; }
            QLabel { color: #1c2333; }
            QPushButton { color: #1c2333; padding: 7px 9px; border-radius: 5px; background: #e8edfb; }
            QPushButton:hover { background: #d7e3ff; }
            QFrame#taskCard { background: white; border: 1px solid #e2e6ef; border-radius: 9px; }
        """)
        root = QWidget()
        self.setCentralWidget(root)
        container = QVBoxLayout(root)
        header = QLabel("🎯 FocusPilot")
        header.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px 0;")
        container.addWidget(header)
        self.summary = QLabel()
        container.addWidget(self.summary)
        actions = QHBoxLayout()
        add_button = QPushButton("＋ 添加任务")
        add_button.clicked.connect(self.add_task)
        actions.addWidget(add_button)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh)
        actions.addWidget(refresh_button)
        container.addLayout(actions)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_container = QWidget()
        self.task_layout = QVBoxLayout(self.list_container)
        self.task_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list_container)
        container.addWidget(self.scroll)
        self.startup_check = QCheckBox("登录 Windows 后在托盘启动（可选）")
        self.startup_check.setEnabled(False)
        self.startup_check.toggled.connect(self.set_startup_enabled)
        container.addWidget(self.startup_check)
        self.desktop_status = QLabel()
        self.desktop_status.setWordWrap(True)
        container.addWidget(self.desktop_status)
        self.bottom = QLabel("提醒将自动运行；关闭窗口后仍保留在系统托盘。")
        self.bottom.setWordWrap(True)
        self.bottom.setStyleSheet("color: #64748b; font-size: 11px;")
        container.addWidget(self.bottom)

        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        self.tray.setToolTip("FocusPilot 任务提醒")
        menu = QMenu()
        show_action = QAction("显示待办", self)
        show_action.triggered.connect(self.show_panel)
        menu.addAction(show_action)
        quit_action = QAction("退出 FocusPilot", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.exit_app)
        self.addAction(quit_action)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.on_tray_activated)
        self.tray.messageClicked.connect(self.show_panel)
        self.tray.show()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(30_000)
        self.refresh()
        self.position_top_right()
        QTimer.singleShot(1000, self.tick)

    def position_top_right(self):
        screen = QApplication.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            self.move(available.right() - self.width() - 15, available.top() + 35)

    def show_panel(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_panel()

    def exit_app(self):
        self.prepare_shutdown()
        QApplication.instance().quit()

    def prepare_shutdown(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        self.quitting = True
        self.timer.stop()
        self.tray.hide()
        if self.hotkey is not None:
            self.hotkey.close()
        if self.capture_dialog is not None:
            self.capture_dialog.reject()

    def enable_desktop(self, app, startup_manager=None, hotkey=None):
        from .desktop import NativeHotkeyFilter
        from .windows import StartupManager

        try:
            self.startup_manager = startup_manager if startup_manager is not None else StartupManager()
            self._startup_enabled = self.startup_manager.is_enabled()
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(self._startup_enabled)
            self.startup_check.blockSignals(False)
            self.startup_check.setEnabled(self.startup_manager.supported)
        except (OSError, ValueError) as error:
            self.startup_check.setEnabled(False)
            self.startup_check.setToolTip(f"无法读取登录启动配置：{error}")
        self.hotkey = hotkey if hotkey is not None else NativeHotkeyFilter(app, self.quick_capture)
        if self.hotkey.start():
            self.desktop_status.setText("Ctrl+Alt+N 快速录入 · Ctrl+Q 退出")
        else:
            self.desktop_status.setText(self.hotkey.registration.error)

    def set_startup_enabled(self, enabled):
        if self.startup_manager is None:
            return
        try:
            self.startup_manager.set_enabled(enabled)
            self._startup_enabled = enabled
        except (OSError, ValueError) as error:
            self.startup_check.blockSignals(True)
            self.startup_check.setChecked(self._startup_enabled)
            self.startup_check.blockSignals(False)
            QMessageBox.warning(self, "无法设置登录自启", str(error))

    def quick_capture(self):
        if not self.quitting:
            self.show_panel()
            self.add_task()

    def closeEvent(self, event):
        if (not self.quitting and not QApplication.instance().isSavingSession()
                and QSystemTrayIcon.isSystemTrayAvailable() and self.tray.isVisible()):
            self.hide()
            event.ignore()
        else:
            event.accept()
            self.exit_app()

    def add_task(self):
        if self.quitting:
            return
        if self.capture_dialog is not None:
            self.capture_dialog.raise_()
            self.capture_dialog.activateWindow()
            return
        dialog = AddTaskDialog(self)
        self.capture_dialog = dialog
        try:
            result = dialog.exec()
        finally:
            self.capture_dialog = None
            dialog.deleteLater()
        if result != QDialog.DialogCode.Accepted:
            return
        try:
            due_at = (
                dialog.due_edit.dateTime().toString("yyyy-MM-ddTHH:mm:ss")
                if dialog.has_due.isChecked() else None
            )
            self.store.create(
                dialog.title_edit.text(), due_at,
                int(dialog.importance.currentData()), dialog.estimate.value(),
            )
        except ValueError as e:
            QMessageBox.warning(self, "无法创建任务", str(e))
            return
        self.refresh()
        self.tick()

    def complete_task(self, task_id: str):
        self.store.complete(task_id)
        self.refresh()

    def snooze_task(self, task_id: str):
        self.store.snooze(task_id, minutes=10)
        self.refresh()

    def _clear_cards(self):
        while self.task_layout.count():
            item = self.task_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()

    def refresh(self):
        now = datetime.now()
        tasks = sort_tasks(self.store.list_tasks(), now)
        self._clear_cards()
        self.summary.setText(f"待完成 {len(tasks)} 项 · 紧急 {sum(urgency(t, now) == 0 for t in tasks)} 项")
        if not tasks:
            self.task_layout.addWidget(QLabel("暂无待办。可以先添加一件最重要的事。"))
            return
        for task in tasks:
            level = urgency(task, now)
            badge = ("🔴 紧急", "🟠 优先", "🔵 普通")[level]
            due_text = task.due_at[:16].replace("T", " ") if task.due_at else "无截止时间"
            card = QFrame()
            card.setObjectName("taskCard")
            layout = QVBoxLayout(card)
            label = QLabel(f"{badge}  {task.title}")
            label.setWordWrap(True)
            label.setStyleSheet("font-size: 13px; font-weight: 600;")
            layout.addWidget(label)
            sub = QLabel(f"DDL：{due_text}  ·  预计 {task.estimated_minutes} 分钟")
            sub.setStyleSheet("color: #64748b; font-size: 11px;")
            layout.addWidget(sub)
            if task.snoozed_until and datetime.fromisoformat(task.snoozed_until) > now:
                layout.addWidget(QLabel(f"提醒已推迟至 {task.snoozed_until[11:16]}"))
            elif task.reminder_stage in ("soon", "overdue"):
                layout.addWidget(QLabel("已逾期，需要重新安排" if task.due_at and datetime.fromisoformat(task.due_at) <= now
                                        else "截止时间不足 30 分钟"))
            row = QHBoxLayout()
            done = QPushButton("✓ 完成")
            done.clicked.connect(lambda _, tid=task.id: self.complete_task(tid))
            row.addWidget(done)
            snooze = QPushButton("稍后10分钟")
            snooze.clicked.connect(lambda _, tid=task.id: self.snooze_task(tid))
            row.addWidget(snooze)
            layout.addLayout(row)
            self.task_layout.addWidget(card)

    def tick(self):
        if self.quitting:
            return
        now = datetime.now()
        changes = []
        for task in self.store.list_tasks():
            kind = reminder_to_send(task, now)
            if not kind:
                continue
            self.store.mark_reminded(task.id, kind)
            changes.append((task, kind))
        if changes:
            self.refresh()
            lines = [f"{task.title}：{'已逾期' if kind == 'overdue' else '截止时间不足 30 分钟'}"
                     for task, kind in changes[:3]]
            if len(changes) > 3:
                lines.append(f"另有 {len(changes) - 3} 项，请打开面板查看")
            if self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(f"FocusPilot · {len(changes)} 项任务提醒", "\n".join(lines))
            # The visual panel remains the fallback when OS notifications are suppressed.
            if any(kind == "overdue" for _, kind in changes):
                self.show_panel()
        else:
            self.refresh()


def configure_app(app):
    """Use a complete light palette instead of mixing dark OS colors with light cards."""
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in {
        QPalette.ColorRole.Window: "#f7f8fc",
        QPalette.ColorRole.WindowText: "#1c2333",
        QPalette.ColorRole.Base: "#ffffff",
        QPalette.ColorRole.AlternateBase: "#edf1f8",
        QPalette.ColorRole.Text: "#1c2333",
        QPalette.ColorRole.Button: "#e8edfb",
        QPalette.ColorRole.ButtonText: "#1c2333",
        QPalette.ColorRole.Highlight: "#3267d6",
        QPalette.ColorRole.HighlightedText: "#ffffff",
        QPalette.ColorRole.ToolTipBase: "#ffffff",
        QPalette.ColorRole.ToolTipText: "#1c2333",
        QPalette.ColorRole.PlaceholderText: "#64748b",
    }.items():
        palette.setColor(role, QColor(color))
    app.setPalette(palette)


def main(argv=None):
    from .desktop import InstanceGuard

    parser = argparse.ArgumentParser(description="FocusPilot desktop task panel")
    parser.add_argument("--background", action="store_true", help="Start in tray when available")
    options = parser.parse_args(argv)
    app = QApplication([sys.argv[0]])
    configure_app(app)
    app.setQuitOnLastWindowClosed(False)
    guard = InstanceGuard(default_db_path())
    try:
        if not guard.acquire():
            return 0
    except OSError as error:
        QMessageBox.critical(None, "无法启动 FocusPilot", str(error))
        return 1
    panel = FocusWindow()
    panel.enable_desktop(app)
    guard.activated.connect(panel.show_panel)
    app.aboutToQuit.connect(panel.prepare_shutdown)
    app.aboutToQuit.connect(guard.close)

    def end_session(manager):
        manager.setRestartHint(QSessionManager.RestartHint.RestartNever)
        panel.prepare_shutdown()

    app.commitDataRequest.connect(end_session)
    if not options.background or not QSystemTrayIcon.isSystemTrayAvailable():
        panel.show()
    try:
        return app.exec()
    finally:
        panel.prepare_shutdown()
        guard.close()
