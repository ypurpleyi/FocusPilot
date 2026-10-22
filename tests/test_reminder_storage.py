from datetime import datetime
import sqlite3

import pytest

from focuspilot.reminders import ReminderSettings
from focuspilot.storage import TaskStore


def test_settings_are_opt_in_and_survive_store_restart_without_changing_tasks(tmp_path):
    path = tmp_path / "settings.sqlite3"
    store = TaskStore(path)
    task = store.create("Unchanged deadline", "2026-10-23T10:00:00")
    assert store.load_reminder_settings() == ReminderSettings()
    settings = ReminderSettings(mode="strong", work_enabled=True, work_start="22:00", work_end="06:00",
                                quiet_until="2026-10-23T12:00:00", cooldown_minutes=5)
    store.save_reminder_settings(settings)
    assert TaskStore(path).load_reminder_settings() == settings
    assert TaskStore(path).get(task.id) == task


def test_settings_table_is_added_to_an_existing_task_database(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('keep')")
    store = TaskStore(path)
    store.save_reminder_settings(ReminderSettings())
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT value FROM unrelated").fetchone()[0] == "keep"


def test_reminder_batch_and_rate_limit_timestamp_survive_restart(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    store = TaskStore(path)
    first, second = store.create("First"), store.create("Second")
    now = datetime(2026, 10, 22, 10, 0, 0, 123456)
    store.record_reminders([(first.id, "soon"), (second.id, "overdue")], alert_at=now)
    restarted = TaskStore(path)
    assert restarted.get(first.id).reminder_stage == "soon"
    assert restarted.get(second.id).reminder_stage == "overdue"
    assert restarted.load_last_alert() == now
    restarted.record_reminders([(first.id, "overdue")])
    assert restarted.load_last_alert() == now  # Panel-only updates do not consume notification cooldown.


def test_invalid_batch_does_not_partially_mark_tasks(tmp_path):
    store = TaskStore(tmp_path / "invalid.sqlite3")
    first, second = store.create("First"), store.create("Second")
    with pytest.raises(ValueError):
        store.record_reminders([(first.id, "soon"), (second.id, "invalid")], alert_at=datetime(2026, 10, 22))
    assert store.get(first.id).reminder_stage == "none"
    assert store.load_last_alert() is None


def test_delivery_timestamp_failure_rolls_back_task_stage(tmp_path):
    store = TaskStore(tmp_path / "rollback.sqlite3")
    task = store.create("Keep pending")
    with sqlite3.connect(store.path) as connection:
        connection.execute("""CREATE TRIGGER deny_alert BEFORE INSERT ON app_settings
            WHEN NEW.key = 'last_alert_at' BEGIN SELECT RAISE(ABORT, 'read only'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_reminders([(task.id, "soon")], alert_at=datetime(2026, 10, 22))
    assert store.get(task.id).reminder_stage == "none"
    assert store.load_last_alert() is None


@pytest.mark.parametrize("payload", ['[]', '{"mode":"invalid"}', '{"work_enabled":"false"}', 'not json'])
def test_corrupt_saved_settings_are_reported_instead_of_overwritten(tmp_path, payload):
    store = TaskStore(tmp_path / "corrupt.sqlite3")
    with sqlite3.connect(store.path) as connection:
        connection.execute("INSERT INTO app_settings VALUES ('reminder_settings', ?)", (payload,))
    with pytest.raises(ValueError):
        store.load_reminder_settings()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT value FROM app_settings WHERE key='reminder_settings'").fetchone()[0] == payload
