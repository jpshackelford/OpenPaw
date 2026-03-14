"""Task scheduler with cron support."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime

from croniter import croniter

from openpaws.config import TaskConfig


@dataclass
class ScheduledTask:
    """A task with its next run time."""
    
    config: TaskConfig
    next_run: datetime | None = None
    status: str = "active"  # active, paused, running
    last_run: datetime | None = None
    last_result: str | None = None
    
    def compute_next_run(self) -> datetime | None:
        """Compute the next run time based on cron expression."""
        if self.status == "paused":
            return None
        
        base = datetime.now()
        cron = croniter(self.config.schedule, base)
        self.next_run = cron.get_next(datetime)
        return self.next_run


@dataclass
class Scheduler:
    """Manages scheduled tasks."""
    
    tasks: dict[str, ScheduledTask] = field(default_factory=dict)
    _running: bool = False
    _task: asyncio.Task | None = None
    
    def add_task(self, config: TaskConfig) -> ScheduledTask:
        """Add a task to the scheduler."""
        task = ScheduledTask(config=config)
        task.compute_next_run()
        self.tasks[config.name] = task
        return task
    
    def remove_task(self, name: str) -> bool:
        """Remove a task from the scheduler."""
        if name in self.tasks:
            del self.tasks[name]
            return True
        return False
    
    def pause_task(self, name: str) -> bool:
        """Pause a task."""
        if name in self.tasks:
            self.tasks[name].status = "paused"
            self.tasks[name].next_run = None
            return True
        return False
    
    def resume_task(self, name: str) -> bool:
        """Resume a paused task."""
        if name in self.tasks:
            task = self.tasks[name]
            task.status = "active"
            task.compute_next_run()
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
        try:
            task.last_result = await executor(task)
        except Exception as e:
            task.last_result = f"Error: {e}"
        task.status = "active"
        task.compute_next_run()

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
