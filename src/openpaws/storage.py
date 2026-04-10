"""SQLite state persistence for OpenPaws.

This module provides persistent storage for:
- Task state (next_run, last_run, status, last_result)
- Conversation sessions (for resume capability)
- Queue items for multi-conversation orchestration

Database location: ~/.openpaws/state.db (or $OPENPAWS_DIR/state.db)
"""

import json
import os
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

DB_FILE_NAME = "state.db"

# Environment variable names (duplicated to avoid circular import)
_ENV_OPENPAWS_DIR = "OPENPAWS_DIR"
_DEFAULT_OPENPAWS_DIR = Path.home() / ".openpaws"


def _get_openpaws_dir() -> Path:
    """Get the OpenPaws directory, creating it if needed."""
    env_dir = os.environ.get(_ENV_OPENPAWS_DIR)
    if env_dir:
        openpaws_dir = Path(env_dir)
    else:
        openpaws_dir = _DEFAULT_OPENPAWS_DIR
    openpaws_dir.mkdir(parents=True, exist_ok=True)
    return openpaws_dir


# SQL schema
SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    name TEXT PRIMARY KEY,
    schedule TEXT,
    group_name TEXT,
    prompt TEXT,
    status TEXT,
    next_run TEXT,
    last_run TEXT,
    last_result TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    group_name TEXT,
    created_at TEXT,
    updated_at TEXT,
    state BLOB
);

CREATE TABLE IF NOT EXISTS queue (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    context TEXT,
    group_name TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    result TEXT,
    error TEXT,
    parent_conversation_id TEXT,
    workflow_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_queue_status_priority 
    ON queue(status, priority DESC, created_at ASC);
"""


@dataclass
class TaskState:
    """Persisted state for a scheduled task."""

    name: str
    schedule: str | None = None
    group_name: str | None = None
    prompt: str | None = None
    status: str = "active"
    next_run: datetime | None = None
    last_run: datetime | None = None
    last_result: str | None = None


@dataclass
class SessionState:
    """Persisted state for a conversation session."""

    id: str
    group_name: str
    created_at: datetime
    updated_at: datetime
    state: bytes | None = None


@dataclass
class QueueItem:
    """A queued conversation for multi-conversation orchestration."""

    id: str
    prompt: str
    group_name: str
    priority: int = 0
    status: str = "pending"  # pending, processing, completed, failed
    created_at: datetime = field(default_factory=datetime.now)
    processed_at: datetime | None = None
    result: str | None = None
    error: str | None = None
    context: dict | None = None
    parent_conversation_id: str | None = None
    workflow_id: str | None = None

    @classmethod
    def create(
        cls,
        prompt: str,
        group_name: str,
        *,
        context: dict | None = None,
        priority: int = 0,
        parent_conversation_id: str | None = None,
        workflow_id: str | None = None,
    ) -> "QueueItem":
        """Create a new queue item with generated ID."""
        return cls(
            id=str(uuid.uuid4()),
            prompt=prompt,
            group_name=group_name,
            context=context,
            priority=priority,
            parent_conversation_id=parent_conversation_id,
            workflow_id=workflow_id,
            created_at=datetime.now(),
        )


def _datetime_to_str(dt: datetime | None) -> str | None:
    """Convert datetime to ISO format string."""
    return dt.isoformat() if dt else None


def _str_to_datetime(s: str | None) -> datetime | None:
    """Convert ISO format string to datetime."""
    return datetime.fromisoformat(s) if s else None


def task_state_from_scheduled(task) -> TaskState:
    """Convert a ScheduledTask to TaskState for persistence."""
    cfg = task.config
    return TaskState(
        name=cfg.name,
        schedule=cfg.schedule,
        group_name=cfg.group,
        prompt=cfg.prompt,
        status=task.status,
        next_run=task.next_run,
        last_run=task.last_run,
        last_result=task.last_result,
    )


def _task_to_row(task: TaskState) -> tuple:
    """Convert TaskState to a tuple for SQL insertion."""
    return (
        task.name,
        task.schedule,
        task.group_name,
        task.prompt,
        task.status,
        _datetime_to_str(task.next_run),
        _datetime_to_str(task.last_run),
        task.last_result,
    )


class Storage:
    """SQLite storage for task state and conversation sessions."""

    def __init__(self, db_path: Path | None = None):
        """Initialize storage with database path.

        Args:
            db_path: Path to SQLite database file.
                     Defaults to ~/.openpaws/state.db
        """
        if db_path is None:
            db_path = _get_openpaws_dir() / DB_FILE_NAME
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection context manager."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # Task persistence methods

    _INSERT_TASK_SQL = """
        INSERT OR REPLACE INTO tasks
            (name, schedule, group_name, prompt, status,
             next_run, last_run, last_result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """

    def save_task(self, task: TaskState) -> None:
        """Save or update a task state."""
        with self._connection() as conn:
            conn.execute(self._INSERT_TASK_SQL, _task_to_row(task))

    def load_task(self, name: str) -> TaskState | None:
        """Load a task state by name."""
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE name = ?", (name,)).fetchone()
            if row is None:
                return None
            return self._row_to_task(row)

    def _row_to_task(self, row: sqlite3.Row) -> TaskState:
        """Convert a database row to TaskState."""
        return TaskState(
            name=row["name"],
            schedule=row["schedule"],
            group_name=row["group_name"],
            prompt=row["prompt"],
            status=row["status"],
            next_run=_str_to_datetime(row["next_run"]),
            last_run=_str_to_datetime(row["last_run"]),
            last_result=row["last_result"],
        )

    def load_all_tasks(self) -> list[TaskState]:
        """Load all persisted task states."""
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM tasks").fetchall()
            return [self._row_to_task(row) for row in rows]

    def delete_task(self, name: str) -> bool:
        """Delete a task state by name. Returns True if deleted."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM tasks WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # Session persistence methods

    def save_session(self, session: SessionState) -> None:
        """Save or update a session state."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (id, group_name, created_at, updated_at, state)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.group_name,
                    _datetime_to_str(session.created_at),
                    _datetime_to_str(session.updated_at),
                    session.state,
                ),
            )

    def load_session(self, session_id: str) -> SessionState | None:
        """Load a session state by ID."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    def _row_to_session(self, row: sqlite3.Row) -> SessionState:
        """Convert a database row to SessionState."""
        return SessionState(
            id=row["id"],
            group_name=row["group_name"],
            created_at=_str_to_datetime(row["created_at"]),
            updated_at=_str_to_datetime(row["updated_at"]),
            state=row["state"],
        )

    def load_sessions_for_group(self, group_name: str) -> list[SessionState]:
        """Load all sessions for a group."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE group_name = ? ORDER BY updated_at DESC",
                (group_name,),
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        """Delete a session state by ID. Returns True if deleted."""
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cursor.rowcount > 0

    def get_latest_session_for_group(self, group_name: str) -> SessionState | None:
        """Get the most recently updated session for a group."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM sessions 
                WHERE group_name = ? 
                ORDER BY updated_at DESC 
                LIMIT 1
                """,
                (group_name,),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_session(row)

    # Queue persistence methods

    _INSERT_QUEUE_SQL = """
        INSERT INTO queue
            (id, prompt, context, group_name, priority, status,
             created_at, processed_at, result, error,
             parent_conversation_id, workflow_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def _queue_item_to_row(self, item: QueueItem) -> tuple:  # length-ok
        """Convert QueueItem to a tuple for SQL insertion."""
        return (
            item.id,
            item.prompt,
            json.dumps(item.context) if item.context else None,
            item.group_name,
            item.priority,
            item.status,
            _datetime_to_str(item.created_at),
            _datetime_to_str(item.processed_at),
            item.result,
            item.error,
            item.parent_conversation_id,
            item.workflow_id,
        )

    def _row_to_queue_item(self, row: sqlite3.Row) -> QueueItem:  # length-ok
        """Convert a database row to QueueItem."""
        context = row["context"]
        return QueueItem(
            id=row["id"],
            prompt=row["prompt"],
            context=json.loads(context) if context else None,
            group_name=row["group_name"],
            priority=row["priority"],
            status=row["status"],
            created_at=_str_to_datetime(row["created_at"]),
            processed_at=_str_to_datetime(row["processed_at"]),
            result=row["result"],
            error=row["error"],
            parent_conversation_id=row["parent_conversation_id"],
            workflow_id=row["workflow_id"],
        )

    def enqueue(self, item: QueueItem) -> str:
        """Add a queue item. Returns the item ID."""
        with self._connection() as conn:
            conn.execute(self._INSERT_QUEUE_SQL, self._queue_item_to_row(item))
        return item.id

    def dequeue(self, max_items: int = 1) -> list[QueueItem]:
        """Fetch pending items and mark as processing.

        Items are ordered by priority (descending) then created_at (ascending).
        """
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM queue
                WHERE status = 'pending'
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (max_items,),
            ).fetchall()

            items = [self._row_to_queue_item(row) for row in rows]

            # Mark as processing
            for item in items:
                conn.execute(
                    "UPDATE queue SET status = 'processing' WHERE id = ?",
                    (item.id,),
                )
                item.status = "processing"

        return items

    def complete_queue_item(self, item_id: str, result: str) -> bool:
        """Mark a queue item as completed. Returns True if updated."""
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE queue 
                SET status = 'completed', result = ?, processed_at = ?
                WHERE id = ?
                """,
                (result, _datetime_to_str(datetime.now()), item_id),
            )
            return cursor.rowcount > 0

    def fail_queue_item(self, item_id: str, error: str) -> bool:
        """Mark a queue item as failed. Returns True if updated."""
        with self._connection() as conn:
            cursor = conn.execute(
                """
                UPDATE queue 
                SET status = 'failed', error = ?, processed_at = ?
                WHERE id = ?
                """,
                (error, _datetime_to_str(datetime.now()), item_id),
            )
            return cursor.rowcount > 0

    def load_queue_item(self, item_id: str) -> QueueItem | None:
        """Load a queue item by ID."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM queue WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_queue_item(row)

    def list_queue(
        self, status: str | None = None, limit: int | None = None
    ) -> list[QueueItem]:
        """List queue items, optionally filtered by status."""
        order = "ORDER BY priority DESC, created_at ASC"
        with self._connection() as conn:
            if status:
                query = f"SELECT * FROM queue WHERE status = ? {order}"
                if limit:
                    query += f" LIMIT {limit}"
                rows = conn.execute(query, (status,)).fetchall()
            else:
                query = f"SELECT * FROM queue {order}"
                if limit:
                    query += f" LIMIT {limit}"
                rows = conn.execute(query).fetchall()
            return [self._row_to_queue_item(row) for row in rows]

    def clear_queue(self, status: str | None = None) -> int:
        """Clear queue items, optionally filtered by status. Returns count deleted."""
        with self._connection() as conn:
            if status:
                cursor = conn.execute("DELETE FROM queue WHERE status = ?", (status,))
            else:
                cursor = conn.execute("DELETE FROM queue")
            return cursor.rowcount

    def get_queue_stats(self) -> dict[str, int]:
        """Get queue statistics by status."""
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) as count
                FROM queue
                GROUP BY status
                """
            ).fetchall()
            return {row["status"]: row["count"] for row in rows}
