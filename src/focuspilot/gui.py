"""Simple always-visible Windows panel. Qt GUI is intentionally optional for unit tests."""

from datetime import datetime
import argparse
import sys
import sqlite3
import math

from PySide6.QtCore import Qt, QDateTime, QTime, QTimer
from PySide6.QtGui import QAction, QColor, QPalette, QSessionManager
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog,
    QDialogButtonBox, QFrame, QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMenu, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyle,
    QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget,
)

from .core import reminder_to_send, sort_tasks, urgency
from .storage import TaskStore, default_db_path
from .reminders import ReminderSettings, cooldown_remaining, delivery_policy, quiet_reason

FOCUS_BACKGROUND = "#e5f3e7"


def _feature_option(text, callback):
    """Build a compact lavender action button with a double-line frame."""
    outer = QFrame()
    outer.setObjectName("featureOption")
    outer_layout = QVBoxLayout(outer)
    outer_layout.setContentsMargins(3, 3, 3, 3)
    outer_layout.setSpacing(0)

    inner = QFrame()
    inner.setObjectName("featureOptionInner")
    inner_layout = QVBoxLayout(inner)
    inner_layout.setContentsMargins(1, 1, 1, 1)
    inner_layout.setSpacing(0)

    button = QPushButton(text)
    button.setObjectName("featureOptionButton")
    button.clicked.connect(callback)
    inner_layout.addWidget(button)
    outer_layout.addWidget(inner)

    shadow = QGraphicsDropShadowEffect(outer)
    shadow.setBlurRadius(9)
    shadow.setOffset(0, 2)
    shadow.setColor(QColor(132, 105, 164, 65))
    outer.setGraphicsEffect(shadow)
    return outer


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


class ReminderSettingsDialog(QDialog):
    def __init__(self, settings, save_callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle("提醒设置")
        self.setMinimumWidth(430)
        self.save_callback = save_callback
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("普通任务提醒方式"))
        self.mode = QComboBox()
        for label, value in [("仅面板（不主动打扰）", "panel"), ("标准（横幅，逾期恢复面板）", "standard"),
                             ("强提醒（横幅、恢复面板、提示音）", "strong")]:
            self.mode.addItem(label, value)
        self.mode.setCurrentIndex(self.mode.findData(settings.mode))
        layout.addWidget(self.mode)
        self.work_enabled = QCheckBox("仅在工作时段提醒普通任务（支持跨午夜）")
        self.work_enabled.setChecked(settings.work_enabled)
        layout.addWidget(self.work_enabled)
        hours = QHBoxLayout()
        self.work_start = QTimeEdit(QTime.fromString(settings.work_start, "HH:mm"))
        self.work_end = QTimeEdit(QTime.fromString(settings.work_end, "HH:mm"))
        for edit in (self.work_start, self.work_end):
            edit.setDisplayFormat("HH:mm")
            edit.setEnabled(settings.work_enabled)
            self.work_enabled.toggled.connect(edit.setEnabled)
        hours.addWidget(self.work_start)
        hours.addWidget(QLabel("至"))
        hours.addWidget(self.work_end)
        layout.addLayout(hours)
        self.quiet_enabled = QCheckBox("免打扰至指定时间")
        active_quiet = bool(settings.quiet_until and datetime.fromisoformat(settings.quiet_until) > datetime.now())
        self.quiet_enabled.setChecked(active_quiet)
        layout.addWidget(self.quiet_enabled)
        quiet_time = (QDateTime.fromString(settings.quiet_until, Qt.DateFormat.ISODate)
                      if active_quiet else QDateTime.currentDateTime().addSecs(3600))
        self.quiet_until = QDateTimeEdit(quiet_time)
        self.quiet_until.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.quiet_until.setCalendarPopup(True)
        self.quiet_until.setEnabled(active_quiet)
        self.quiet_enabled.toggled.connect(self.quiet_until.setEnabled)
        layout.addWidget(self.quiet_until)
        self.overdue_override = QCheckBox("逾期任务始终强提醒（可打断免打扰）")
        self.overdue_override.setChecked(settings.overdue_override)
        layout.addWidget(self.overdue_override)
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("通知最小间隔（分钟）"))
        self.cooldown = QSpinBox()
        self.cooldown.setRange(1, 60)
        self.cooldown.setValue(settings.cooldown_minutes)
        interval_row.addWidget(self.cooldown)
        layout.addLayout(interval_row)
        note = QLabel("逾期强提醒同样遵守通知间隔与任务的稍后提醒。静默期间任务仍在面板显示；恢复后只汇总当前阶段。提示音取决于系统音量。")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet("color: #b42318;")
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self):
        try:
            quiet = self.quiet_until.dateTime().toString("yyyy-MM-ddTHH:mm:ss") if self.quiet_enabled.isChecked() else None
            if quiet and datetime.fromisoformat(quiet) <= datetime.now():
                raise ValueError("免打扰结束时间必须晚于当前时间")
            settings = ReminderSettings(mode=self.mode.currentData(), work_enabled=self.work_enabled.isChecked(),
                                        work_start=self.work_start.time().toString("HH:mm"),
                                        work_end=self.work_end.time().toString("HH:mm"), quiet_until=quiet,
                                        overdue_override=self.overdue_override.isChecked(),
                                        cooldown_minutes=self.cooldown.value())
            self.save_callback(settings)
        except (ValueError, sqlite3.Error, OSError) as error:
            self.error.setText(f"无法保存：{error}")
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
        self.settings_dialog = None
        self.analysis_dialog = None
        self._analysis_exit_pending = False
        self.reminder_error = ""
        try:
            self.reminder_settings = self.store.load_reminder_settings()
            self.last_alert = self.store.load_last_alert()
        except (ValueError, sqlite3.Error) as error:
            self.reminder_settings = ReminderSettings(mode="panel", overdue_override=False)
            self.last_alert = None
            self.reminder_error = f"提醒配置读取失败，暂仅面板显示；请重新保存设置。{error}"
        self.setWindowTitle("FocusPilot · 今日待办")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        self.resize(390, 580)
        self.setStyleSheet("""
            QMainWindow { background: __FOCUS_BACKGROUND__; }
            QDialog, QMessageBox, QMenu, QCalendarWidget { background-color: __FOCUS_BACKGROUND__; }
            QWidget#centralPanel, QWidget#taskListContainer { background: __FOCUS_BACKGROUND__; }
            QScrollArea { background: transparent; }
            QLabel { color: #1c2333; }
            QPushButton {
                color: #383340;
                font-family: "Comic Sans MS", "幼圆", "YouYuan", "Segoe Print";
                font-size: 10pt;
                font-weight: 600;
                padding: 6px 11px;
                border: 1px solid #55535a;
                border-radius: 9px;
                background-color: #e9def7;
            }
            QPushButton:hover { background-color: #ded0f0; }
            QPushButton:pressed { background-color: #d4c2e8; }
            QFrame#featureOption {
                background-color: #e9def7;
                border: 1px solid #55535a;
                border-radius: 10px;
            }
            QFrame#featureOptionInner {
                background-color: transparent;
                border: 1px dashed #55535a;
                border-top-left-radius: 11px;
                border-top-right-radius: 5px;
                border-bottom-right-radius: 9px;
                border-bottom-left-radius: 4px;
            }
            QPushButton#featureOptionButton {
                color: #383340;
                padding: 6px 7px;
                border: none;
                border-top-left-radius: 7px;
                border-top-right-radius: 4px;
                border-bottom-right-radius: 8px;
                border-bottom-left-radius: 5px;
                background-color: #e9def7;
            }
            QPushButton#featureOptionButton:hover { background-color: rgba(255, 255, 255, 75); }
            QFrame#taskCard { border: 1px solid #d8d6df; border-radius: 12px; }
        """.replace("__FOCUS_BACKGROUND__", FOCUS_BACKGROUND))
        root = QWidget()
        root.setObjectName("centralPanel")
        self.setCentralWidget(root)
        container = QVBoxLayout(root)
        header = QLabel("🎯 FocusPilot")
        header.setStyleSheet("font-size: 22px; font-weight: 700; padding: 6px 0;")
        container.addWidget(header)
        encouragement = QLabel("Keep going!")
        encouragement.setStyleSheet(
            'font-family: "Comic Sans MS", "Segoe Print"; font-size: 16px; '
            'font-weight: 700; color: #e98daf; padding: 0 0 4px 2px;'
        )
        container.addWidget(encouragement)
        self.summary = QLabel()
        container.addWidget(self.summary)
        actions = QHBoxLayout()
        actions.addWidget(_feature_option("＋ 添加任务", self.add_task))
        actions.addWidget(_feature_option("提醒设置", self.show_reminder_settings))
        actions.addWidget(_feature_option("刷新", self.refresh))
        container.addLayout(actions)
        container.addWidget(_feature_option("粘贴通知 · 分析待办", self.show_analysis))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.list_container = QWidget()
        self.list_container.setObjectName("taskListContainer")
        self.task_layout = QVBoxLayout(self.list_container)
        self.task_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list_container)
        container.addWidget(self.scroll)
        self.reminder_status = QLabel()
        self.reminder_status.setWordWrap(True)
        self.reminder_status.setStyleSheet("color: #64748b; font-size: 11px;")
        container.addWidget(self.reminder_status)
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
        if self.analysis_dialog and not self.analysis_dialog.shutdown():
            if not self._analysis_exit_pending:
                self._analysis_exit_pending = True
                self.analysis_dialog.worker.finished.connect(self.exit_app)
            self.analysis_dialog.status.setText('正在结束分析并清理，完成后自动退出。')
            return
        self.prepare_shutdown()
        QApplication.instance().quit()

    def show_analysis(self):
        if self.quitting:
            return
        if self.analysis_dialog is None:
            try:
                from .analysis_ui import AnalysisDialog
                self.analysis_dialog = AnalysisDialog(self.store, self)
                self.analysis_dialog.saved.connect(lambda count: (self.refresh(), self.tick()))
            except ImportError:
                QMessageBox.warning(self, '分析功能未就绪', '请按README安装项目的mcp和sandbox可选依赖后重试。')
                return
        self.analysis_dialog.show()
        self.analysis_dialog.raise_()
        self.analysis_dialog.activateWindow()

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
        if self.settings_dialog is not None:
            self.settings_dialog.reject()
        if self.analysis_dialog is not None:
            self.analysis_dialog.cancel()
            if self.analysis_dialog.worker is not None:
                self.analysis_dialog.worker.wait(30000)
            self.analysis_dialog.shutdown()

    def apply_reminder_settings(self, settings):
        self.store.save_reminder_settings(settings, reset_alert_clock=bool(self.reminder_error))
        self.reminder_settings = settings
        if self.reminder_error:
            self.last_alert = None
        self.reminder_error = ""
        self.refresh()

    def show_reminder_settings(self):
        if self.quitting:
            return
        if self.settings_dialog is not None:
            self.settings_dialog.raise_()
            self.settings_dialog.activateWindow()
            return
        dialog = ReminderSettingsDialog(self.reminder_settings, self.apply_reminder_settings, self)
        self.settings_dialog = dialog
        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self.settings_dialog = None
            dialog.deleteLater()
        if accepted:
            self.tick()

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
            if self.settings_dialog is not None:
                self.settings_dialog.raise_()
                self.settings_dialog.activateWindow()
                return
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

    def refresh(self, now=None):
        now = now or datetime.now()
        mode = {"panel": "仅面板", "standard": "标准提醒", "strong": "强提醒"}[self.reminder_settings.mode]
        reason = quiet_reason(self.reminder_settings, now)
        override = "逾期仍强提醒" if self.reminder_settings.overdue_override else "逾期遵循上述设置"
        remaining = cooldown_remaining(now, self.last_alert, self.reminder_settings.cooldown_minutes)
        text = f"{mode} · {reason or '当前可提醒'} · {override}"
        if remaining.total_seconds() > 0:
            text += f" · 通知间隔剩余 {math.ceil(remaining.total_seconds())} 秒"
        self.reminder_status.setText(self.reminder_error or text)
        tasks = sort_tasks(self.store.list_tasks(), now)
        self._clear_cards()
        self.summary.setText(f"待完成 {len(tasks)} 项 · 紧急 {sum(urgency(t, now) == 0 for t in tasks)} 项")
        if not tasks:
            self.task_layout.addWidget(QLabel("暂无待办。可以先添加一件最重要的事。"))
            return
        pastel_palette = ((255, 214, 224), (255, 231, 190), (255, 246, 190),
                          (205, 229, 250), (228, 214, 247))
        for task in tasks:
            level = urgency(task, now)
            badge = ("🔴 紧急", "🟠 优先", "🔵 普通")[level]
            due_text = task.due_at[:16].replace("T", " ") if task.due_at else "无截止时间"
            card = QFrame()
            card.setObjectName("taskCard")
            color = pastel_palette[sum(ord(char) for char in task.id) % len(pastel_palette)]
            card.setStyleSheet(
                "QFrame#taskCard { background-color: rgba(%d, %d, %d, 128); "
                "border: 1px solid #d8d6df; border-radius: 12px; }" % color
            )
            layout = QVBoxLayout(card)
            label = QLabel(f"{badge}  {task.title}")
            label.setTextFormat(Qt.TextFormat.PlainText)
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
            elif task.due_at and datetime.fromisoformat(task.due_at) <= now:
                layout.addWidget(QLabel("已逾期，等待提醒时段或通知间隔结束"))
            row = QHBoxLayout()
            row.addWidget(_feature_option("✓ 完成", lambda _, tid=task.id: self.complete_task(tid)))
            row.addWidget(_feature_option("稍后10分钟", lambda _, tid=task.id: self.snooze_task(tid)))
            layout.addLayout(row)
            self.task_layout.addWidget(card)

    def tick(self, now=None):
        if self.quitting:
            return
        now = now or datetime.now()
        panel_updates = []
        alerts = []
        ready = not cooldown_remaining(now, self.last_alert, self.reminder_settings.cooldown_minutes)
        for task in self.store.list_tasks():
            kind = reminder_to_send(task, now)
            if not kind:
                continue
            policy = delivery_policy(self.reminder_settings, now, kind)
            if not policy.allowed:
                continue
            if not (policy.banner or policy.reveal or policy.sound):
                panel_updates.append((task.id, kind))
            elif ready:
                alerts.append((task, kind, policy))
        updates = panel_updates + [(task.id, kind) for task, kind, _ in alerts]
        if updates:
            try:
                self.store.record_reminders(updates, alert_at=now if alerts else None)
            except sqlite3.Error as error:
                self.refresh(now)
                self.reminder_status.setText(f"提醒状态保存失败，将在下次检查重试：{error}")
                return
        if alerts:
            self.last_alert = now
            changes = [(task, kind) for task, kind, _ in alerts]
            lines = [f"{task.title}：{'已逾期' if kind == 'overdue' else '截止时间不足 30 分钟'}"
                     for task, kind in changes[:3]]
            if len(changes) > 3:
                lines.append(f"另有 {len(changes) - 3} 项，请打开面板查看")
            if any(policy.banner for _, _, policy in alerts) and self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
                self.tray.showMessage(f"FocusPilot · {len(changes)} 项任务提醒", "\n".join(lines))
            # The visual panel remains the fallback when OS notifications are suppressed.
            if any(policy.reveal for _, _, policy in alerts):
                self.show_panel()
            if any(policy.sound for _, _, policy in alerts):
                QApplication.beep()
        self.refresh(now)


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
