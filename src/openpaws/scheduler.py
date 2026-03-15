"""Task scheduler with cron, interval, and one-time support."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from croniter import croniter

from openpaws.config import TaskConfig

if TYPE_CHECKING:
    from openpaws.storage import Storage


def _parse_once_timestamp(once_str: str) -> datetime:
    """Parse a once timestamp string to datetime.

    Supports ISO format (YYYY-MM-DDTHH:MM:SS) and simple format (YYYY-MM-DD HH:MM).
    """
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(once_str, fmt)
        except ValueError:
            continue
    raise ValueError(f"Invalid timestamp format: {once_str}")


@dataclass
class ScheduledTask:
    """A task with its next run time."""

    config: TaskConfig
    next_run: datetime | None = None
    status: str = "active"  # active, paused, running, completed
    last_run: datetime | None = None
    last_result: str | None = None

    def _compute_cron_next(self) -> datetime:
        """Compute next run for cron-based schedule."""
        return croniter(self.config.schedule, datetime.now()).get_next(datetime)

    def _compute_interval_next(self) -> datetime:
        """Compute next run for interval-based schedule."""
        base = self.last_run if self.last_run else datetime.now()
        return base + timedelta(seconds=self.config.interval)

    def _compute_once_next(self) -> datetime | None:
        """Compute next run for one-time schedule."""
        if self.last_run:
            self.status = "completed"
            return None
        return _parse_once_timestamp(self.config.once)

    def compute_next_run(self) -> datetime | None:
        """Compute the next run time based on task schedule type."""
        if self.status in ("paused", "completed"):
            return None

        if self.config.schedule:
            self.next_run = self._compute_cron_next()
        elif self.config.interval:
            self.next_run = self._compute_interval_next()
        elif self.config.once:
            self.next_run = self._compute_once_next()

        return self.next_run


@dataclass
class Scheduler:
    """Manages scheduled tasks."""

    tasks: dict[str, ScheduledTask] = field(default_factory=dict)
    storage: Storage | None = None
    _running: bool = False
    _task: asyncio.Task | None = None

    def add_task(self, config: TaskConfig) -> ScheduledTask:
        """Add a task to the scheduler."""
        task = ScheduledTask(config=config)
        self._restore_task_state(task)
        task.compute_next_run()
        self.tasks[config.name] = task
        self._persist_task(task)
        return task

    def _restore_task_state(self, task: ScheduledTask) -> None:
        """Restore task state from storage if available."""
        if self.storage is None:
            return
        persisted = self.storage.load_task(task.config.name)
        if persisted is not None:
            task.status = persisted.status
            task.last_run = persisted.last_run
            task.last_result = persisted.last_result

    def _persist_task(self, task: ScheduledTask) -> None:
        """Persist task state to storage."""
        if self.storage is None:
            return
        from openpaws.storage import task_state_from_scheduled

        self.storage.save_task(task_state_from_scheduled(task))

    def remove_task(self, name: str) -> bool:
        """Remove a task from the scheduler."""
        if name in self.tasks:
            del self.tasks[name]
            if self.storage:
                self.storage.delete_task(name)
            return True
        return False

    def pause_task(self, name: str) -> bool:
        """Pause a task."""
        if name in self.tasks:
            self.tasks[name].status = "paused"
            self.tasks[name].next_run = None
            self._persist_task(self.tasks[name])
            return True
        return False

    def resume_task(self, name: str) -> bool:
        """Resume a paused task."""
        if name in self.tasks:
            task = self.tasks[name]
            task.status = "active"
            task.compute_next_run()
            self._persist_task(task)
            return True
        return False

    def get_due_tasks(self) -> list[ScheduledTask]:
        """Get all tasks that are due to run."""
        now = datetime.now()
        due = []

        for task in self.tasks.values():
            if task.status == "active" and task.next_run and task.next_run <= now:
                due.append(task)

        return due

    async def _execute_task(self, task: ScheduledTask, executor) -> None:
        """Execute a single task and update its state."""
        task.status = "running"
        task.last_run = datetime.now()
        self._persist_task(task)
        try:
            task.last_result = await executor(task)
        except Exception as e:
            task.last_result = f"Error: {e}"
        task.status = "active"
        task.compute_next_run()
        self._persist_task(task)

    async def run_loop(self, executor) -> None:
        """Main scheduler loop."""
        self._running = True
        while self._running:
            for task in self.get_due_tasks():
                await self._execute_task(task, executor)
            await asyncio.sleep(30)

    def start(self, executor):
        """Start the scheduler loop."""
        self._task = asyncio.create_task(self.run_loop(executor))

    def stop(self):
        """Stop the scheduler loop."""
        self._running = False
        if self._task:
            self._task.cancel()
