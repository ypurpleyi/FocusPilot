from datetime import datetime, timedelta

import pytest

from focuspilot.core import reminder_to_send
from focuspilot.storage import TaskStore


def test_persistence_across_store_restarts(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = TaskStore(path)
    created = store.create("完成周报", due_at="2026-10-23T20:00:00", importance=3)
    restarted = TaskStore(path)
    assert restarted.get(created.id).title == "完成周报"
    restarted.complete(created.id)
    assert TaskStore(path).get(created.id).status == "done"


def test_invalid_title_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        TaskStore(tmp_path / "tasks.sqlite3").create("   ")


def test_snooze_and_reminder_state_are_durable(tmp_path):
    path = tmp_path / "data.sqlite3"
    store = TaskStore(path)
    now = datetime(2026, 10, 22, 18)
    created = store.create("任务", due_at=(now + timedelta(minutes=20)).isoformat())
    store.snooze(created.id, minutes=10, now=now)
    assert reminder_to_send(TaskStore(path).get(created.id), now) is None
    store.mark_reminded(created.id, "soon")
    assert reminder_to_send(TaskStore(path).get(created.id), now) is None
