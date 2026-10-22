"""Qt workflow regressions; these do not prove OS notification delivery."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QSystemTrayIcon

from focuspilot.gui import AddTaskDialog, FocusWindow
from focuspilot.storage import TaskStore


@pytest.fixture(scope="module")
def app():
    instance = QApplication.instance() or QApplication([])
    yield instance


def test_blank_title_keeps_dialog_open_and_preserves_fields(app):
    dialog = AddTaskDialog()
    dialog.title_edit.setText("   ")
    dialog.estimate.setValue(75)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.estimate.value() == 75
    assert dialog.error.text()
    dialog.title_edit.setText("有效任务")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_snooze_is_visible_and_completed_task_disappears(app, tmp_path):
    store = TaskStore(tmp_path / "gui.sqlite3")
    task = store.create("回归测试任务", "2026-10-22T10:00:00")
    window = FocusWindow(store=store)
    window.timer.stop()
    try:
        window.snooze_task(task.id)
        assert any("提醒已推迟至" in label.text() for label in window.findChildren(QLabel))
        window.complete_task(task.id)
        assert "待完成 0 项" in window.summary.text()
        assert TaskStore(store.path).get(task.id).status == "done"
    finally:
        window.tray.hide()
        window.deleteLater()
        app.processEvents()


def test_light_palette_remains_readable_under_dark_system_theme(app):
    from focuspilot.gui import configure_app

    dark = QPalette()
    dark.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    dark.setColor(QPalette.ColorRole.WindowText, QColor("#ffffff"))
    app.setPalette(dark)
    configure_app(app)
    palette = app.palette()
    assert palette.color(QPalette.ColorRole.Window).lightness() > 220
    assert palette.color(QPalette.ColorRole.WindowText).lightness() < 80
    assert palette.color(QPalette.ColorRole.ButtonText).lightness() < 80


@pytest.fixture
def panel(app, tmp_path, monkeypatch):
    monkeypatch.setattr(QApplication, "beep", lambda: None)
    window = FocusWindow(TaskStore(tmp_path / "workflow.sqlite3"))
    window.timer.stop()
    yield window
    window.tray.hide()
    window.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("available,visible,should_hide", [
    (True, True, True), (True, False, False), (False, False, False),
])
def test_close_only_hides_when_tray_can_restore(panel, app, monkeypatch, available, visible, should_hide):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: available)
    panel.tray.setVisible(visible)
    quit_calls = []
    monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
    event = QCloseEvent()
    panel.closeEvent(event)
    assert event.isAccepted() is not should_hide
    assert bool(quit_calls) is not should_hide


def test_tray_double_click_and_notification_click_restore_panel(panel, app):
    panel.hide()
    panel.tray.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert panel.isVisible()
    panel.hide()
    panel.tray.messageClicked.emit()
    assert panel.isVisible()


def test_explicit_exit_stops_background_timer(panel, app, monkeypatch):
    quit_calls = []
    monkeypatch.setattr(app, "quit", lambda: quit_calls.append(True))
    panel.timer.start(30_000)
    panel.exit_app()
    assert panel.quitting
    assert not panel.tray.isVisible()
    assert not panel.timer.isActive()
    assert quit_calls == [True]


def test_simultaneous_reminders_are_summarized_without_losing_tasks(panel, monkeypatch):
    for index in range(5):
        panel.store.create(f"Batch {index}", "2020-01-01T10:00:00")
    messages = []
    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", lambda: True)
    monkeypatch.setattr(panel.tray, "showMessage", lambda *args: messages.append(args))
    panel.tick()
    assert len(messages) == 1
    assert "5" in messages[0][0]
    assert "另有 2 项" in messages[0][1]
    assert all(task.reminder_stage == "overdue" for task in panel.store.list_tasks())
    panel.tick()
    assert len(messages) == 1


def test_session_shutdown_does_not_hide_in_tray(panel, app, monkeypatch):
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: True)
    monkeypatch.setattr(app, "isSavingSession", lambda: True)
    monkeypatch.setattr(app, "quit", lambda: None)
    panel.tray.show()
    event = QCloseEvent()
    panel.closeEvent(event)
    assert event.isAccepted()
    assert panel.quitting
    assert not panel.tray.isVisible()


def test_repeated_capture_uses_one_dialog(panel, monkeypatch):
    calls = []

    def fake_exec(dialog):
        calls.append(dialog)
        panel.add_task()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(AddTaskDialog, "exec", fake_exec)
    panel.add_task()
    assert len(calls) == 1
    assert panel.capture_dialog is None


def test_startup_permission_error_restores_checkbox(panel, app, monkeypatch):
    from types import SimpleNamespace
    from focuspilot.gui import QMessageBox

    def deny(enabled):
        raise PermissionError("Denied")

    startup = SimpleNamespace(supported=True, is_enabled=lambda: False, set_enabled=deny)
    releases = []
    hotkey = SimpleNamespace(start=lambda: False, registration=SimpleNamespace(error="Conflict"),
                             close=lambda: releases.append(True))
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    panel.enable_desktop(app, startup_manager=startup, hotkey=hotkey)
    assert not panel.startup_check.isChecked()
    assert panel.desktop_status.text() == "Conflict"
    panel.startup_check.setChecked(True)
    assert not panel.startup_check.isChecked()
    assert warnings
    panel.prepare_shutdown()
    panel.prepare_shutdown()
    assert releases == [True]
