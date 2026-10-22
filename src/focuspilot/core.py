"""Deterministic task rules; deliberately independent from any LLM and GUI."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    due_at: Optional[str]
    importance: int
    estimated_minutes: int
    status: str
    created_at: str
    snoozed_until: Optional[str] = None
    reminder_stage: str = "none"


def deadline(task: Task) -> Optional[datetime]:
    return datetime.fromisoformat(task.due_at) if task.due_at else None


def urgency(task: Task, now: datetime) -> int:
    """Return 0=critical, 1=high, 2=normal. Completed tasks excluded by caller."""
    due = deadline(task)
    if due is not None:
        remaining = due - now
        if remaining <= timedelta(hours=2):
            return 0
        if remaining <= timedelta(hours=24):
            return 1
    if task.importance == 3:
        return 1
    return 2


def sort_tasks(tasks: list[Task], now: datetime) -> list[Task]:
    remaining = [t for t in tasks if t.status == "pending"]
    return sorted(
        remaining,
        key=lambda t: (
            urgency(t, now),
            -t.importance,
            deadline(t) or datetime.max,
            t.created_at,
        ),
    )


def reminder_to_send(task: Task, now: datetime) -> Optional[str]:
    """Emit 'soon' once before due, 'overdue' once after due; support explicit snoozing."""
    if task.status != "pending" or task.due_at is None:
        return None
    if task.snoozed_until and datetime.fromisoformat(task.snoozed_until) > now:
        return None
    due = deadline(task)
    assert due is not None
    if now >= due:
        return "overdue" if task.reminder_stage != "overdue" else None
    if now >= due - timedelta(minutes=30):
        return "soon" if task.reminder_stage not in ("soon", "overdue") else None
    return None
