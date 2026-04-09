"""Tests for SQLite state persistence."""

from datetime import datetime

import pytest

from openpaws.storage import QueueItem, SessionState, Storage, TaskState


@pytest.fixture
def storage(tmp_path):
    """Create a storage instance with a temporary database."""
    db_path = tmp_path / "test_state.db"
    return Storage(db_path=db_path)


class TestTaskPersistence:
    """Tests for task state persistence."""

    def test_save_and_load_task(self, storage):
        """Test saving and loading a task."""
        task = TaskState(
            name="test-task",
            schedule="0 8 * * *",
            group_name="main",
            prompt="Test prompt",
            status="active",
            next_run=datetime(2026, 3, 15, 8, 0, 0),
            last_run=datetime(2026, 3, 14, 8, 0, 0),
            last_result="Success",
        )

        storage.save_task(task)
        loaded = storage.load_task("test-task")

        assert loaded is not None
        assert loaded.name == "test-task"
        assert loaded.schedule == "0 8 * * *"
        assert loaded.group_name == "main"
        assert loaded.prompt == "Test prompt"
        assert loaded.status == "active"
        assert loaded.next_run == datetime(2026, 3, 15, 8, 0, 0)
        assert loaded.last_run == datetime(2026, 3, 14, 8, 0, 0)
        assert loaded.last_result == "Success"

    def test_load_nonexistent_task(self, storage):
        """Test loading a task that doesn't exist."""
        loaded = storage.load_task("nonexistent")
        assert loaded is None

    def test_update_task(self, storage):
        """Test updating an existing task."""
        task = TaskState(name="update-test", status="active")
        storage.save_task(task)

        task.status = "paused"
        task.last_result = "Updated"
        storage.save_task(task)

        loaded = storage.load_task("update-test")
        assert loaded.status == "paused"
        assert loaded.last_result == "Updated"

    def test_load_all_tasks(self, storage):
        """Test loading all persisted tasks."""
        storage.save_task(TaskState(name="task1", status="active"))
        storage.save_task(TaskState(name="task2", status="paused"))
        storage.save_task(TaskState(name="task3", status="running"))

        all_tasks = storage.load_all_tasks()
        assert len(all_tasks) == 3
        names = {t.name for t in all_tasks}
        assert names == {"task1", "task2", "task3"}

    def test_delete_task(self, storage):
        """Test deleting a task."""
        storage.save_task(TaskState(name="to-delete", status="active"))

        assert storage.delete_task("to-delete") is True
        assert storage.load_task("to-delete") is None

    def test_delete_nonexistent_task(self, storage):
        """Test deleting a task that doesn't exist."""
        assert storage.delete_task("nonexistent") is False

    def test_task_with_null_fields(self, storage):
        """Test task with minimal fields (null values)."""
        task = TaskState(name="minimal")
        storage.save_task(task)

        loaded = storage.load_task("minimal")
        assert loaded.name == "minimal"
        assert loaded.schedule is None
        assert loaded.group_name is None
        assert loaded.next_run is None
        assert loaded.last_run is None
        assert loaded.last_result is None


class TestSessionPersistence:
    """Tests for session state persistence."""

    def test_save_and_load_session(self, storage):
        """Test saving and loading a session."""
        session = SessionState(
            id="session-123",
            group_name="main",
            created_at=datetime(2026, 3, 14, 10, 0, 0),
            updated_at=datetime(2026, 3, 14, 10, 30, 0),
            state=b'{"messages": []}',
        )

        storage.save_session(session)
        loaded = storage.load_session("session-123")

        assert loaded is not None
        assert loaded.id == "session-123"
        assert loaded.group_name == "main"
        assert loaded.created_at == datetime(2026, 3, 14, 10, 0, 0)
        assert loaded.updated_at == datetime(2026, 3, 14, 10, 30, 0)
        assert loaded.state == b'{"messages": []}'

    def test_load_nonexistent_session(self, storage):
        """Test loading a session that doesn't exist."""
        loaded = storage.load_session("nonexistent")
        assert loaded is None

    def test_update_session(self, storage):
        """Test updating an existing session."""
        now = datetime.now()
        session = SessionState(
            id="update-session",
            group_name="test",
            created_at=now,
            updated_at=now,
            state=b"original",
        )
        storage.save_session(session)

        session.state = b"updated"
        session.updated_at = datetime(2026, 3, 15, 12, 0, 0)
        storage.save_session(session)

        loaded = storage.load_session("update-session")
        assert loaded.state == b"updated"
        assert loaded.updated_at == datetime(2026, 3, 15, 12, 0, 0)

    def test_load_sessions_for_group(self, storage):
        """Test loading all sessions for a group."""
        now = datetime.now()
        storage.save_session(
            SessionState(id="s1", group_name="group-a", created_at=now, updated_at=now)
        )
        storage.save_session(
            SessionState(id="s2", group_name="group-a", created_at=now, updated_at=now)
        )
        storage.save_session(
            SessionState(id="s3", group_name="group-b", created_at=now, updated_at=now)
        )

        group_a_sessions = storage.load_sessions_for_group("group-a")
        assert len(group_a_sessions) == 2
        ids = {s.id for s in group_a_sessions}
        assert ids == {"s1", "s2"}

    def test_delete_session(self, storage):
        """Test deleting a session."""
        now = datetime.now()
        storage.save_session(
            SessionState(
                id="to-delete", group_name="test", created_at=now, updated_at=now
            )
        )

        assert storage.delete_session("to-delete") is True
        assert storage.load_session("to-delete") is None

    def test_delete_nonexistent_session(self, storage):
        """Test deleting a session that doesn't exist."""
        assert storage.delete_session("nonexistent") is False

    def test_get_latest_session_for_group(self, storage):
        """Test getting the most recent session for a group."""
        storage.save_session(
            SessionState(
                id="older",
                group_name="test-group",
                created_at=datetime(2026, 3, 14, 10, 0, 0),
                updated_at=datetime(2026, 3, 14, 10, 0, 0),
            )
        )
        storage.save_session(
            SessionState(
                id="newer",
                group_name="test-group",
                created_at=datetime(2026, 3, 14, 10, 0, 0),
                updated_at=datetime(2026, 3, 14, 12, 0, 0),
            )
        )

        latest = storage.get_latest_session_for_group("test-group")
        assert latest is not None
        assert latest.id == "newer"

    def test_get_latest_session_empty_group(self, storage):
        """Test getting latest session for a group with no sessions."""
        latest = storage.get_latest_session_for_group("empty-group")
        assert latest is None

    def test_session_with_null_state(self, storage):
        """Test session with null state blob."""
        now = datetime.now()
        session = SessionState(
            id="no-state", group_name="test", created_at=now, updated_at=now, state=None
        )
        storage.save_session(session)

        loaded = storage.load_session("no-state")
        assert loaded.state is None


class TestStorageInitialization:
    """Tests for storage initialization."""

    def test_creates_database_file(self, tmp_path):
        """Test that storage creates the database file."""
        db_path = tmp_path / "subdir" / "test.db"
        Storage(db_path=db_path)
        assert db_path.exists()

    def test_creates_tables(self, tmp_path):
        """Test that storage creates the required tables."""
        import sqlite3

        db_path = tmp_path / "test.db"
        Storage(db_path=db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "tasks" in tables
        assert "sessions" in tables

    def test_default_path_uses_openpaws_dir(self, tmp_path, monkeypatch):
        """Test that default path uses OPENPAWS_DIR."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))
        storage = Storage()
        assert storage.db_path == tmp_path / "state.db"

    def test_creates_queue_table(self, tmp_path):
        """Test that storage creates the queue table."""
        import sqlite3

        db_path = tmp_path / "test.db"
        Storage(db_path=db_path)

        conn = sqlite3.connect(db_path)
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        assert "queue" in tables


class TestQueuePersistence:
    """Tests for queue item persistence."""

    def test_create_queue_item(self):
        """Test QueueItem.create factory method."""
        item = QueueItem.create(
            prompt="Test prompt",
            group_name="main",
            context={"key": "value"},
            priority=5,
            workflow_id="workflow-123",
        )
        assert item.id is not None
        assert item.prompt == "Test prompt"
        assert item.group_name == "main"
        assert item.context == {"key": "value"}
        assert item.priority == 5
        assert item.workflow_id == "workflow-123"
        assert item.status == "pending"
        assert item.created_at is not None

    def test_enqueue_and_load(self, storage):
        """Test enqueueing and loading a queue item."""
        item = QueueItem.create(
            prompt="Test prompt",
            group_name="main",
            context={"step": 1},
            priority=1,
            parent_conversation_id="parent-123",
            workflow_id="workflow-456",
        )
        returned_id = storage.enqueue(item)
        assert returned_id == item.id

        loaded = storage.load_queue_item(item.id)
        assert loaded is not None
        assert loaded.id == item.id
        assert loaded.prompt == "Test prompt"
        assert loaded.group_name == "main"
        assert loaded.context == {"step": 1}
        assert loaded.priority == 1
        assert loaded.status == "pending"
        assert loaded.parent_conversation_id == "parent-123"
        assert loaded.workflow_id == "workflow-456"

    def test_load_nonexistent_queue_item(self, storage):
        """Test loading a queue item that doesn't exist."""
        loaded = storage.load_queue_item("nonexistent")
        assert loaded is None

    def test_dequeue_single_item(self, storage):
        """Test dequeuing a single item."""
        item = QueueItem.create(prompt="Test", group_name="main")
        storage.enqueue(item)

        dequeued = storage.dequeue(max_items=1)
        assert len(dequeued) == 1
        assert dequeued[0].id == item.id
        assert dequeued[0].status == "processing"

        # Verify status is persisted
        loaded = storage.load_queue_item(item.id)
        assert loaded.status == "processing"

    def test_dequeue_respects_priority(self, storage):
        """Test that dequeue returns items in priority order."""
        low = QueueItem.create(prompt="Low", group_name="main", priority=0)
        high = QueueItem.create(prompt="High", group_name="main", priority=10)
        medium = QueueItem.create(prompt="Medium", group_name="main", priority=5)

        storage.enqueue(low)
        storage.enqueue(high)
        storage.enqueue(medium)

        dequeued = storage.dequeue(max_items=3)
        assert len(dequeued) == 3
        assert dequeued[0].id == high.id
        assert dequeued[1].id == medium.id
        assert dequeued[2].id == low.id

    def test_dequeue_respects_max_items(self, storage):
        """Test that dequeue respects the max_items limit."""
        for i in range(5):
            storage.enqueue(QueueItem.create(prompt=f"Item {i}", group_name="main"))

        dequeued = storage.dequeue(max_items=2)
        assert len(dequeued) == 2

    def test_dequeue_only_pending_items(self, storage):
        """Test that dequeue only returns pending items."""
        pending = QueueItem.create(prompt="Pending", group_name="main")
        storage.enqueue(pending)

        # First dequeue marks as processing
        storage.dequeue(max_items=1)

        # Second dequeue should return empty
        dequeued = storage.dequeue(max_items=1)
        assert len(dequeued) == 0

    def test_complete_queue_item(self, storage):
        """Test marking a queue item as completed."""
        item = QueueItem.create(prompt="Test", group_name="main")
        storage.enqueue(item)

        result = storage.complete_queue_item(item.id, "Success result")
        assert result is True

        loaded = storage.load_queue_item(item.id)
        assert loaded.status == "completed"
        assert loaded.result == "Success result"
        assert loaded.processed_at is not None

    def test_complete_nonexistent_item(self, storage):
        """Test completing a queue item that doesn't exist."""
        result = storage.complete_queue_item("nonexistent", "Result")
        assert result is False

    def test_fail_queue_item(self, storage):
        """Test marking a queue item as failed."""
        item = QueueItem.create(prompt="Test", group_name="main")
        storage.enqueue(item)

        result = storage.fail_queue_item(item.id, "Error message")
        assert result is True

        loaded = storage.load_queue_item(item.id)
        assert loaded.status == "failed"
        assert loaded.error == "Error message"
        assert loaded.processed_at is not None

    def test_fail_nonexistent_item(self, storage):
        """Test failing a queue item that doesn't exist."""
        result = storage.fail_queue_item("nonexistent", "Error")
        assert result is False

    def test_list_queue_all(self, storage):
        """Test listing all queue items."""
        item1 = QueueItem.create(prompt="Item 1", group_name="main")
        item2 = QueueItem.create(prompt="Item 2", group_name="main")
        storage.enqueue(item1)
        storage.enqueue(item2)

        items = storage.list_queue()
        assert len(items) == 2

    def test_list_queue_by_status(self, storage):
        """Test listing queue items filtered by status."""
        item1 = QueueItem.create(prompt="Item 1", group_name="main")
        item2 = QueueItem.create(prompt="Item 2", group_name="main")
        storage.enqueue(item1)
        storage.enqueue(item2)

        # Complete one item
        storage.complete_queue_item(item1.id, "Done")

        pending = storage.list_queue(status="pending")
        assert len(pending) == 1
        assert pending[0].id == item2.id

        completed = storage.list_queue(status="completed")
        assert len(completed) == 1
        assert completed[0].id == item1.id

    def test_clear_queue_all(self, storage):
        """Test clearing all queue items."""
        for i in range(3):
            storage.enqueue(QueueItem.create(prompt=f"Item {i}", group_name="main"))

        deleted = storage.clear_queue()
        assert deleted == 3

        items = storage.list_queue()
        assert len(items) == 0

    def test_clear_queue_by_status(self, storage):
        """Test clearing queue items by status."""
        item1 = QueueItem.create(prompt="Item 1", group_name="main")
        item2 = QueueItem.create(prompt="Item 2", group_name="main")
        storage.enqueue(item1)
        storage.enqueue(item2)

        # Complete one item
        storage.complete_queue_item(item1.id, "Done")

        # Clear only completed items
        deleted = storage.clear_queue(status="completed")
        assert deleted == 1

        # Pending item should still exist
        items = storage.list_queue()
        assert len(items) == 1
        assert items[0].id == item2.id

    def test_get_queue_stats(self, storage):
        """Test getting queue statistics."""
        item1 = QueueItem.create(prompt="Item 1", group_name="main")
        item2 = QueueItem.create(prompt="Item 2", group_name="main")
        item3 = QueueItem.create(prompt="Item 3", group_name="main")
        storage.enqueue(item1)
        storage.enqueue(item2)
        storage.enqueue(item3)

        # Complete one, fail one
        storage.complete_queue_item(item1.id, "Done")
        storage.fail_queue_item(item2.id, "Error")

        stats = storage.get_queue_stats()
        assert stats.get("pending") == 1
        assert stats.get("completed") == 1
        assert stats.get("failed") == 1

    def test_queue_item_with_null_context(self, storage):
        """Test queue item with no context."""
        item = QueueItem.create(prompt="Test", group_name="main", context=None)
        storage.enqueue(item)

        loaded = storage.load_queue_item(item.id)
        assert loaded.context is None

    def test_queue_fifo_within_priority(self, storage):
        """Test that items with same priority are FIFO ordered."""
        import time

        item1 = QueueItem.create(prompt="First", group_name="main", priority=5)
        storage.enqueue(item1)
        time.sleep(0.01)  # Small delay to ensure different timestamps

        item2 = QueueItem.create(prompt="Second", group_name="main", priority=5)
        storage.enqueue(item2)

        dequeued = storage.dequeue(max_items=2)
        assert dequeued[0].id == item1.id
        assert dequeued[1].id == item2.id
