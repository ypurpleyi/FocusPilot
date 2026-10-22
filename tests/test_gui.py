"""Qt workflow regressions; these do not prove OS notification delivery."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QLabel

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
