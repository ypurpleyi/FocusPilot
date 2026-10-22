"""SQLite persistence. Local-only MVP; no external API credentials needed."""

import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .core import Task


def default_db_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / ".local" / "share")
    return base / "FocusPilot" / "tasks.sqlite3"


class TaskStore:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                due_at TEXT,
                importance INTEGER NOT NULL,
                estimated_minutes INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                snoozed_until TEXT,
                reminder_stage TEXT NOT NULL DEFAULT 'none'
            )""")

    def _connect(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def create(
        self,
        title: str,
        due_at: Optional[str] = None,
        importance: int = 2,
        estimated_minutes: int = 30,
    ) -> Task:
        title = title.strip()
        if not title:
            raise ValueError("任务标题不能为空")
        if not 1 <= importance <= 3:
            raise ValueError("重要程度必须在 1 到 3 之间")
        if not 1 <= estimated_minutes <= 1440:
            raise ValueError("预计时长必须在 1 到 1440 分钟之间")
        if due_at is not None:
            datetime.fromisoformat(due_at)  # validate basic local ISO date-time
        task = Task(
            id=str(uuid.uuid4()), title=title, due_at=due_at,
            importance=importance, estimated_minutes=estimated_minutes,
            status="pending", created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._connect() as c:
            c.execute("""INSERT INTO tasks
                (id,title,due_at,importance,estimated_minutes,status,created_at,snoozed_until,reminder_stage)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (task.id, task.title, task.due_at, task.importance,
                 task.estimated_minutes, task.status, task.created_at,
                 task.snoozed_until, task.reminder_stage),
            )
        return task

    def list_tasks(self) -> list[Task]:
        with self._connect() as c:
            rows = c.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [Task(**dict(r)) for r in rows]

    def get(self, task_id: str) -> Task | None:
        with self._connect() as c:
            row = c.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return Task(**dict(row)) if row else None

    def complete(self, task_id: str) -> None:
        with self._connect() as c:
            c.execute("UPDATE tasks SET status='done' WHERE id=?", (task_id,))

    def snooze(self, task_id: str, minutes: int = 10, now: datetime | None = None) -> None:
        if minutes <= 0:
            raise ValueError("推迟时间必须大于0")
        now = now or datetime.now()
        until = (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self._connect() as c:
            c.execute("UPDATE tasks SET snoozed_until=?, reminder_stage='snoozed' WHERE id=?",
                      (until, task_id))

    def mark_reminded(self, task_id: str, stage: str) -> None:
        if stage not in ("soon", "overdue"):
            raise ValueError("提醒阶段无效")
        with self._connect() as c:
            c.execute("UPDATE tasks SET reminder_stage=?, snoozed_until=NULL WHERE id=?",
                      (stage, task_id))
