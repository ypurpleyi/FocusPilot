import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dataclasses import replace
from datetime import datetime, timedelta
import sqlite3

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication, QDialog, QSystemTrayIcon

from focuspilot.gui import FocusWindow
from focuspilot.reminders import ReminderSettings
from focuspilot.storage import TaskStore

NOW = datetime(2026, 10, 22, 10)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, tmp_path, monkeypatch):
    panel = FocusWindow(TaskStore(tmp_path / "policy.sqlite3"))
    panel.timer.stop()
    panel.effects = {"banner": [], "reveal": [], "sound": []}
    monkeypatch.setattr(QSystemTrayIcon, "supportsMessages", lambda: True)
    monkeypatch.setattr(panel.tray, "showMessage", lambda *args: panel.effects["banner"].append(args))
    monkeypatch.setattr(panel, "show_panel", lambda: panel.effects["reveal"].append(True))
    monkeypatch.setattr(QApplication, "beep", lambda: panel.effects["sound"].append(True))
    yield panel
    panel.prepare_shutdown()
    panel.deleteLater()
    app.processEvents()


def test_dnd_defers_soon_then_delivers_latest_overdue_once(window):
    task = window.store.create("Quiet pending", (NOW + timedelta(minutes=10)).isoformat())
    window.reminder_settings = ReminderSettings(quiet_until=(NOW + timedelta(minutes=20)).isoformat(),
                                                overdue_override=False)
    window.tick(NOW)
    assert window.store.get(task.id).reminder_stage == "none"
    assert not any(window.effects.values())
    window.tick(NOW + timedelta(minutes=20))
    assert window.store.get(task.id).reminder_stage == "overdue"
    assert len(window.effects["banner"]) == 1
    window.tick(NOW + timedelta(minutes=21))
    assert len(window.effects["banner"]) == 1


def test_overdue_exception_is_strong_but_preserves_user_task_snooze(window):
    task = window.store.create("Overdue", (NOW - timedelta(minutes=1)).isoformat())
    window.reminder_settings = ReminderSettings(quiet_until=(NOW + timedelta(hours=1)).isoformat())
    window.store.snooze(task.id, now=NOW)
    window.tick(NOW)
    assert not any(window.effects.values())
    window.tick(NOW + timedelta(minutes=10))
    assert all(len(events) == 1 for events in window.effects.values())


def test_quiet_completed_tasks_do_not_reappear_when_quiet_ends(window):
    task = window.store.create("Completed in quiet", NOW.isoformat())
    window.reminder_settings = ReminderSettings(quiet_until=(NOW + timedelta(minutes=5)).isoformat(),
                                                overdue_override=False)
    window.tick(NOW)
    window.complete_task(task.id)
    window.tick(NOW + timedelta(minutes=6))
    assert not any(window.effects.values())


def test_panel_mode_updates_cards_without_consuming_external_cooldown(window):
    task = window.store.create("Panel only", NOW.isoformat())
    window.reminder_settings = ReminderSettings(mode="panel", overdue_override=False)
    window.tick(NOW)
    assert window.store.get(task.id).reminder_stage == "overdue"
    assert window.store.load_last_alert() is None
    assert not any(window.effects.values())


def test_global_cooldown_preserves_pending_batch_across_window_restart(window, app, monkeypatch):
    first = window.store.create("First", (NOW + timedelta(minutes=10)).isoformat())
    window.tick(NOW)
    assert len(window.effects["banner"]) == 1
    second = window.store.create("Second", (NOW + timedelta(minutes=10)).isoformat())
    window.tick(NOW + timedelta(seconds=20))
    assert window.store.get(second.id).reminder_stage == "none"
    restarted = FocusWindow(window.store)
    restarted.timer.stop()
    messages = []
    monkeypatch.setattr(restarted.tray, "showMessage", lambda *args: messages.append(args))
    try:
        restarted.tick(NOW + timedelta(seconds=40))
        assert not messages
        restarted.tick(NOW + timedelta(minutes=1))
        assert len(messages) == 1
        assert restarted.store.get(second.id).reminder_stage == "soon"
        assert restarted.store.get(first.id).reminder_stage == "soon"
    finally:
        restarted.prepare_shutdown()
        restarted.deleteLater()
        app.processEvents()


def test_failed_settings_save_keeps_active_preferences(window, monkeypatch):
    before = window.reminder_settings

    def deny(*args, **kwargs):
        raise sqlite3.OperationalError("Read only")

    monkeypatch.setattr(window.store, "save_reminder_settings", deny)
    with pytest.raises(sqlite3.OperationalError):
        window.apply_reminder_settings(replace(before, mode="strong"))
    assert window.reminder_settings == before


def test_settings_dialog_rejects_equal_hours_and_preserves_fields_on_save_failure(app):
    from focuspilot.gui import ReminderSettingsDialog
    saved = []

    def save(settings):
        saved.append(settings)
        raise sqlite3.OperationalError("Read only")

    dialog = ReminderSettingsDialog(ReminderSettings(), save)
    dialog.work_enabled.setChecked(True)
    dialog.work_start.setTime(QTime(9, 0))
    dialog.work_end.setTime(QTime(9, 0))
    dialog.accept()
    assert not saved
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error.text()
    dialog.work_end.setTime(QTime(18, 0))
    dialog.accept()
    assert len(saved) == 1
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.work_enabled.isChecked()
    assert "Read only" in dialog.error.text()


def test_overdue_exception_does_not_claim_suppressed_soon_task(window):
    soon = window.store.create("Soon quiet", (NOW + timedelta(minutes=10)).isoformat())
    overdue = window.store.create("Overdue exception", (NOW - timedelta(minutes=1)).isoformat())
    window.reminder_settings = ReminderSettings(quiet_until=(NOW + timedelta(hours=1)).isoformat())
    window.tick(NOW)
    assert window.store.get(soon.id).reminder_stage == "none"
    assert window.store.get(overdue.id).reminder_stage == "overdue"
    assert len(window.effects["banner"]) == 1
    assert "Soon quiet" not in window.effects["banner"][0][1]


def test_failed_reminder_transaction_does_not_emit_or_start_cooldown(window, monkeypatch):
    task = window.store.create("Retry safely", NOW.isoformat())

    def deny(*args, **kwargs):
        raise sqlite3.OperationalError("Locked")

    monkeypatch.setattr(window.store, "record_reminders", deny)
    window.tick(NOW)
    assert window.store.get(task.id).reminder_stage == "none"
    assert window.last_alert is None
    assert not any(window.effects.values())
    assert "Locked" in window.reminder_status.text()


def test_corrupt_settings_use_visible_silent_fallback_until_explicit_save(app, tmp_path):
    store = TaskStore(tmp_path / "corrupt-gui.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute("INSERT INTO app_settings VALUES ('reminder_settings', 'invalid')")
        connection.execute("INSERT INTO app_settings VALUES ('last_alert_at', 'invalid')")
    panel = FocusWindow(store)
    panel.timer.stop()
    try:
        assert panel.reminder_settings.mode == "panel"
        assert not panel.reminder_settings.overdue_override
        assert panel.reminder_error
        panel.apply_reminder_settings(ReminderSettings())
        assert not panel.reminder_error
        assert store.load_reminder_settings() == ReminderSettings()
        assert store.load_last_alert() is None
    finally:
        panel.prepare_shutdown()
        panel.deleteLater()
        app.processEvents()
