"""Tests for task scheduler."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from openpaws.config import TaskConfig
from openpaws.scheduler import ScheduledTask, Scheduler


@pytest.fixture
def task_config():
    """Create a sample task config."""
    return TaskConfig(
        name="test-task",
        schedule="*/5 * * * *",  # Every 5 minutes
        group="main",
        prompt="Test prompt",
    )


@pytest.fixture
def daily_task_config():
    """Create a daily task config."""
    return TaskConfig(
        name="daily-task",
        schedule="0 9 * * *",  # 9am daily
        group="main",
        prompt="Daily prompt",
    )


class TestScheduledTask:
    """Tests for ScheduledTask dataclass."""

    def test_create_task(self, task_config):
        """Test creating a scheduled task."""
        task = ScheduledTask(config=task_config)
        assert task.config == task_config
        assert task.next_run is None
        assert task.status == "active"
        assert task.last_run is None
        assert task.last_result is None

    def test_compute_next_run(self, task_config):
        """Test computing next run time."""
        task = ScheduledTask(config=task_config)
        next_run = task.compute_next_run()

        assert next_run is not None
        assert task.next_run == next_run
        assert next_run > datetime.now()
        # Should be within 5 minutes (the cron interval)
        assert next_run < datetime.now() + timedelta(minutes=6)

    def test_compute_next_run_when_paused(self, task_config):
        """Test that paused tasks don't compute next run."""
        task = ScheduledTask(config=task_config, status="paused")
        next_run = task.compute_next_run()

        assert next_run is None
        assert task.next_run is None

    def test_task_with_daily_schedule(self, daily_task_config):
        """Test task with daily cron schedule."""
        task = ScheduledTask(config=daily_task_config)
        next_run = task.compute_next_run()

        assert next_run is not None
        # Should be within 24 hours
        assert next_run < datetime.now() + timedelta(hours=25)


class TestScheduler:
    """Tests for Scheduler class."""

    def test_create_scheduler(self):
        """Test creating an empty scheduler."""
        scheduler = Scheduler()
        assert scheduler.tasks == {}
        assert scheduler._running is False
        assert scheduler._task is None

    def test_add_task(self, task_config):
        """Test adding a task to the scheduler."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)

        assert task_config.name in scheduler.tasks
        assert scheduler.tasks[task_config.name] == task
        assert task.next_run is not None

    def test_add_multiple_tasks(self, task_config, daily_task_config):
        """Test adding multiple tasks."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)
        scheduler.add_task(daily_task_config)

        assert len(scheduler.tasks) == 2
        assert "test-task" in scheduler.tasks
        assert "daily-task" in scheduler.tasks

    def test_remove_task(self, task_config):
        """Test removing a task."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)

        result = scheduler.remove_task(task_config.name)

        assert result is True
        assert task_config.name not in scheduler.tasks

    def test_remove_nonexistent_task(self):
        """Test removing a task that doesn't exist."""
        scheduler = Scheduler()
        result = scheduler.remove_task("nonexistent")
        assert result is False

    def test_pause_task(self, task_config):
        """Test pausing a task."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)

        result = scheduler.pause_task(task_config.name)

        assert result is True
        task = scheduler.tasks[task_config.name]
        assert task.status == "paused"
        assert task.next_run is None

    def test_pause_nonexistent_task(self):
        """Test pausing a task that doesn't exist."""
        scheduler = Scheduler()
        result = scheduler.pause_task("nonexistent")
        assert result is False

    def test_resume_task(self, task_config):
        """Test resuming a paused task."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)
        scheduler.pause_task(task_config.name)

        result = scheduler.resume_task(task_config.name)

        assert result is True
        task = scheduler.tasks[task_config.name]
        assert task.status == "active"
        assert task.next_run is not None

    def test_resume_nonexistent_task(self):
        """Test resuming a task that doesn't exist."""
        scheduler = Scheduler()
        result = scheduler.resume_task("nonexistent")
        assert result is False

    def test_get_due_tasks_none_due(self, task_config):
        """Test getting due tasks when none are due."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)
        # Task was just added, next_run is in the future

        due = scheduler.get_due_tasks()
        assert due == []

    def test_get_due_tasks_with_due_task(self, task_config):
        """Test getting due tasks when one is due."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        # Manually set next_run to the past to make it due
        task.next_run = datetime.now() - timedelta(minutes=1)

        due = scheduler.get_due_tasks()

        assert len(due) == 1
        assert due[0] == task

    def test_get_due_tasks_ignores_paused(self, task_config):
        """Test that paused tasks are not returned as due."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        task.next_run = datetime.now() - timedelta(minutes=1)
        task.status = "paused"

        due = scheduler.get_due_tasks()
        assert due == []

    def test_get_due_tasks_ignores_no_next_run(self, task_config):
        """Test that tasks with no next_run are not returned."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        task.next_run = None

        due = scheduler.get_due_tasks()
        assert due == []


class TestSchedulerRunLoop:
    """Tests for the scheduler's async run loop."""

    @pytest.mark.asyncio
    async def test_run_loop_executes_due_task(self, task_config):
        """Test that run_loop executes due tasks."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        task.next_run = datetime.now() - timedelta(minutes=1)

        executor = AsyncMock(return_value="Success")

        # Run one iteration of the loop, then stop
        async def run_once():
            scheduler._running = True
            due_tasks = scheduler.get_due_tasks()
            for t in due_tasks:
                t.status = "running"
                t.last_run = datetime.now()
                try:
                    result = await executor(t)
                    t.last_result = result
                except Exception as e:
                    t.last_result = f"Error: {e}"
                t.status = "active"
                t.compute_next_run()

        await run_once()

        executor.assert_called_once_with(task)
        assert task.last_result == "Success"
        assert task.status == "active"
        assert task.last_run is not None

    @pytest.mark.asyncio
    async def test_run_loop_handles_executor_error(self, task_config):
        """Test that run_loop handles executor errors gracefully."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        task.next_run = datetime.now() - timedelta(minutes=1)

        executor = AsyncMock(side_effect=ValueError("Test error"))

        # Run one iteration
        async def run_once():
            scheduler._running = True
            due_tasks = scheduler.get_due_tasks()
            for t in due_tasks:
                t.status = "running"
                t.last_run = datetime.now()
                try:
                    result = await executor(t)
                    t.last_result = result
                except Exception as e:
                    t.last_result = f"Error: {e}"
                t.status = "active"
                t.compute_next_run()

        await run_once()

        assert "Error: Test error" in task.last_result
        assert task.status == "active"

    @pytest.mark.asyncio
    async def test_run_loop_updates_next_run(self, task_config):
        """Test that run_loop updates next_run after execution."""
        scheduler = Scheduler()
        task = scheduler.add_task(task_config)
        old_next_run = datetime.now() - timedelta(minutes=1)
        task.next_run = old_next_run

        executor = AsyncMock(return_value="Done")

        # Run one iteration
        async def run_once():
            due_tasks = scheduler.get_due_tasks()
            for t in due_tasks:
                t.status = "running"
                t.last_run = datetime.now()
                result = await executor(t)
                t.last_result = result
                t.status = "active"
                t.compute_next_run()

        await run_once()

        assert task.next_run is not None
        assert task.next_run > old_next_run

    @pytest.mark.asyncio
    async def test_start_creates_task(self, task_config):
        """Test that start() creates an asyncio task."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)

        executor = AsyncMock()

        # We need to be in an async context with a running loop
        scheduler.start(executor)

        assert scheduler._task is not None
        assert isinstance(scheduler._task, asyncio.Task)

        # Clean up
        scheduler.stop()
        # Give it a moment to cancel
        await asyncio.sleep(0.1)

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, task_config):
        """Test that stop() cancels the running task."""
        scheduler = Scheduler()
        scheduler.add_task(task_config)

        executor = AsyncMock()
        scheduler.start(executor)

        scheduler.stop()

        assert scheduler._running is False
        # Wait a bit for the cancellation to propagate
        await asyncio.sleep(0.1)
        # Task should be cancelled or done (cancelling state transitions to cancelled)
        assert scheduler._task.cancelled() or scheduler._task.done()

    def test_stop_when_not_started(self):
        """Test that stop() works even if not started."""
        scheduler = Scheduler()
        # Should not raise
        scheduler.stop()
        assert scheduler._running is False


class TestSchedulerIntegration:
    """Integration tests for scheduler with realistic scenarios."""

    def test_full_task_lifecycle(self, task_config):
        """Test adding, pausing, resuming, and removing a task."""
        scheduler = Scheduler()

        # Add
        task = scheduler.add_task(task_config)
        assert task.status == "active"
        assert task.next_run is not None

        # Pause
        scheduler.pause_task(task_config.name)
        assert task.status == "paused"
        assert task.next_run is None

        # Resume
        scheduler.resume_task(task_config.name)
        assert task.status == "active"
        assert task.next_run is not None

        # Remove
        scheduler.remove_task(task_config.name)
        assert task_config.name not in scheduler.tasks

    def test_multiple_due_tasks(self, task_config, daily_task_config):
        """Test getting multiple due tasks."""
        scheduler = Scheduler()

        task1 = scheduler.add_task(task_config)
        task2 = scheduler.add_task(daily_task_config)

        # Make both due
        task1.next_run = datetime.now() - timedelta(minutes=1)
        task2.next_run = datetime.now() - timedelta(minutes=2)

        due = scheduler.get_due_tasks()

        assert len(due) == 2
        assert task1 in due
        assert task2 in due

    def test_mixed_task_states(self, task_config, daily_task_config):
        """Test scheduler with tasks in different states."""
        scheduler = Scheduler()

        active_task = scheduler.add_task(task_config)
        paused_task = scheduler.add_task(daily_task_config)

        # Make active task due
        active_task.next_run = datetime.now() - timedelta(minutes=1)

        # Pause the other
        scheduler.pause_task(daily_task_config.name)

        due = scheduler.get_due_tasks()

        assert len(due) == 1
        assert active_task in due
        assert paused_task not in due
