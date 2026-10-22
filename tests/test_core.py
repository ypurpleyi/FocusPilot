from dataclasses import replace
from datetime import datetime, timedelta

from focuspilot.core import Task, reminder_to_send, sort_tasks, urgency

NOW = datetime(2026, 10, 22, 10, 0, 0)


def task(name="a", due_in_minutes=90, stage="none", status="pending", importance=2):
    due = None if due_in_minutes is None else (NOW + timedelta(minutes=due_in_minutes)).isoformat()
    return Task(name, name, due, importance, 30, status, NOW.isoformat(), None, stage)


def test_urgent_and_completed_tasks_are_not_scheduled():
    assert urgency(task(), NOW) == 0
    assert sort_tasks([task(status="done")], NOW) == []


def test_due_soon_only_notifies_once():
    sample = task(due_in_minutes=20)
    assert reminder_to_send(sample, NOW) == "soon"
    assert reminder_to_send(replace(sample, reminder_stage="soon"), NOW) is None


def test_overdue_notifies_even_if_prior_soon_notification():
    sample = task(due_in_minutes=-1, stage="soon")
    assert reminder_to_send(sample, NOW) == "overdue"
    assert reminder_to_send(replace(sample, reminder_stage="overdue"), NOW) is None


def test_completed_or_no_deadline_never_notifies():
    assert reminder_to_send(task(status="done"), NOW) is None
    assert reminder_to_send(task(due_in_minutes=None), NOW) is None


def test_snooze_blocks_and_then_reenables_reminder():
    t = replace(task(due_in_minutes=15, stage="snoozed"),
                snoozed_until=(NOW + timedelta(minutes=10)).isoformat())
    assert reminder_to_send(t, NOW) is None
    assert reminder_to_send(t, NOW + timedelta(minutes=11)) == "soon"


def test_priority_sorting_and_no_deadline():
    normal = task("normal", due_in_minutes=None)
    urgent = task("urgent", due_in_minutes=30)
    high = task("high", due_in_minutes=None, importance=3)
    assert [x.id for x in sort_tasks([normal, high, urgent], NOW)] == ["urgent", "high", "normal"]
