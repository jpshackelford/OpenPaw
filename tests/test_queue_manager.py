"""Tests for queue manager."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from openpaws.config import GroupConfig, QueueConfig
from openpaws.queue_manager import QueueManager
from openpaws.runner import ConversationResult
from openpaws.storage import Storage


@pytest.fixture
def storage(tmp_path):
    """Create a storage instance with a temporary database."""
    db_path = tmp_path / "test_state.db"
    return Storage(db_path=db_path)


@pytest.fixture
def mock_runner():
    """Create a mock runner with config containing 'main' group."""
    runner = MagicMock()
    runner.run_prompt = AsyncMock(
        return_value=ConversationResult(success=True, message="Test response")
    )
    # Add config with groups for validation
    runner.config = MagicMock()
    runner.config.groups = {
        "main": GroupConfig(name="main", channel="test", chat_id="123")
    }
    return runner


@pytest.fixture
def queue_manager(storage, mock_runner):
    """Create a queue manager with default config."""
    return QueueManager(
        storage=storage,
        runner=mock_runner,
        config=QueueConfig(),
    )


class TestEnqueue:
    """Tests for enqueueing items."""

    @pytest.mark.asyncio
    async def test_enqueue_basic(self, queue_manager, storage):
        """Test basic enqueueing."""
        item_id = await queue_manager.enqueue(
            prompt="Test prompt",
            group_name="main",
        )
        assert item_id is not None

        # Verify item is in storage
        item = storage.load_queue_item(item_id)
        assert item is not None
        assert item.prompt == "Test prompt"
        assert item.group_name == "main"
        assert item.status == "pending"

    @pytest.mark.asyncio
    async def test_enqueue_with_context(self, queue_manager, storage):
        """Test enqueueing with context."""
        item_id = await queue_manager.enqueue(
            prompt="Test prompt",
            group_name="main",
            context={"key": "value", "step": 1},
        )

        item = storage.load_queue_item(item_id)
        assert item.context == {"key": "value", "step": 1}

    @pytest.mark.asyncio
    async def test_enqueue_with_priority(self, queue_manager, storage):
        """Test enqueueing with priority."""
        item_id = await queue_manager.enqueue(
            prompt="Test prompt",
            group_name="main",
            priority=10,
        )

        item = storage.load_queue_item(item_id)
        assert item.priority == 10

    @pytest.mark.asyncio
    async def test_enqueue_with_workflow_id(self, queue_manager, storage):
        """Test enqueueing with workflow ID."""
        item_id = await queue_manager.enqueue(
            prompt="Test prompt",
            group_name="main",
            workflow_id="workflow-123",
            parent_conversation_id="parent-456",
        )

        item = storage.load_queue_item(item_id)
        assert item.workflow_id == "workflow-123"
        assert item.parent_conversation_id == "parent-456"

    @pytest.mark.asyncio
    async def test_enqueue_invalid_group(self, queue_manager):
        """Test enqueueing with a group that doesn't exist."""
        with pytest.raises(ValueError, match="not found"):
            await queue_manager.enqueue(
                prompt="Test prompt",
                group_name="nonexistent",
            )


class TestProcessBatch:
    """Tests for processing queue batches."""

    @pytest.mark.asyncio
    async def test_process_empty_queue(self, queue_manager):
        """Test processing empty queue returns 0."""
        processed = await queue_manager.process_batch()
        assert processed == 0

    @pytest.mark.asyncio
    async def test_process_single_item(self, queue_manager, mock_runner, storage):
        """Test processing a single item."""
        await queue_manager.enqueue(prompt="Test", group_name="main")

        processed = await queue_manager.process_batch()
        assert processed == 1

        # Verify runner was called
        mock_runner.run_prompt.assert_called_once()
        call_kwargs = mock_runner.run_prompt.call_args.kwargs
        assert call_kwargs["group_name"] == "main"
        assert call_kwargs["prompt"] == "Test"

    @pytest.mark.asyncio
    async def test_process_item_with_context(self, queue_manager, mock_runner):
        """Test that context is included in prompt."""
        await queue_manager.enqueue(
            prompt="Analyze this",
            group_name="main",
            context={"data": "test_data", "step": 2},
        )

        await queue_manager.process_batch()

        call_kwargs = mock_runner.run_prompt.call_args.kwargs
        prompt = call_kwargs["prompt"]
        assert "Context from previous conversation:" in prompt
        assert "data: test_data" in prompt
        assert "step: 2" in prompt
        assert "Analyze this" in prompt

    @pytest.mark.asyncio
    async def test_process_respects_max_dispatch(self, storage, mock_runner):
        """Test that process_batch respects max_dispatch config."""
        qm = QueueManager(
            storage=storage,
            runner=mock_runner,
            config=QueueConfig(max_dispatch=2),
        )

        # Enqueue 5 items
        for i in range(5):
            await qm.enqueue(prompt=f"Item {i}", group_name="main")

        processed = await qm.process_batch()
        assert processed == 2
        assert mock_runner.run_prompt.call_count == 2

    @pytest.mark.asyncio
    async def test_process_disabled_queue(self, storage, mock_runner):
        """Test that disabled queue doesn't process items."""
        qm = QueueManager(
            storage=storage,
            runner=mock_runner,
            config=QueueConfig(enabled=False),
        )

        await qm.enqueue(prompt="Test", group_name="main")

        processed = await qm.process_batch()
        assert processed == 0
        mock_runner.run_prompt.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_successful_completion(self, queue_manager, storage):
        """Test that successful items are marked completed."""
        item_id = await queue_manager.enqueue(prompt="Test", group_name="main")

        await queue_manager.process_batch()

        item = storage.load_queue_item(item_id)
        assert item.status == "completed"
        assert item.result == "Test response"
        assert item.processed_at is not None

    @pytest.mark.asyncio
    async def test_process_failed_item(self, storage, mock_runner):
        """Test that failed items are marked as failed."""
        mock_runner.run_prompt = AsyncMock(
            return_value=ConversationResult(
                success=False, message="Failed", error="Something went wrong"
            )
        )
        qm = QueueManager(storage=storage, runner=mock_runner, config=QueueConfig())

        item_id = await qm.enqueue(prompt="Test", group_name="main")
        await qm.process_batch()

        item = storage.load_queue_item(item_id)
        assert item.status == "failed"
        assert item.error == "Something went wrong"

    @pytest.mark.asyncio
    async def test_process_exception(self, storage, mock_runner):
        """Test that exceptions are caught and item is marked failed."""
        mock_runner.run_prompt = AsyncMock(side_effect=Exception("Unexpected error"))
        qm = QueueManager(storage=storage, runner=mock_runner, config=QueueConfig())

        item_id = await qm.enqueue(prompt="Test", group_name="main")
        await qm.process_batch()

        item = storage.load_queue_item(item_id)
        assert item.status == "failed"
        assert "Unexpected error" in item.error


class TestHelperMethods:
    """Tests for helper methods."""

    @pytest.mark.asyncio
    async def test_get_stats(self, queue_manager, storage):
        """Test getting queue statistics."""
        # Enqueue and process some items
        await queue_manager.enqueue(prompt="Item 1", group_name="main")
        await queue_manager.enqueue(prompt="Item 2", group_name="main")
        await queue_manager.enqueue(prompt="Item 3", group_name="main")

        # Process one
        storage.dequeue(max_items=1)
        storage.complete_queue_item(
            storage.list_queue(status="processing")[0].id, "Done"
        )

        stats = queue_manager.get_stats()
        assert stats.get("pending") == 2
        assert stats.get("completed") == 1

    @pytest.mark.asyncio
    async def test_list_pending(self, queue_manager):
        """Test listing pending items."""
        await queue_manager.enqueue(prompt="Item 1", group_name="main")
        await queue_manager.enqueue(prompt="Item 2", group_name="main")

        pending = queue_manager.list_pending()
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_clear_completed(self, queue_manager, storage):
        """Test clearing completed items."""
        item_id = await queue_manager.enqueue(prompt="Item 1", group_name="main")
        await queue_manager.enqueue(prompt="Item 2", group_name="main")

        # Complete one
        storage.complete_queue_item(item_id, "Done")

        deleted = queue_manager.clear_completed()
        assert deleted == 1

        # Pending item should still exist
        pending = queue_manager.list_pending()
        assert len(pending) == 1


class TestBuildPromptWithContext:
    """Tests for _build_prompt_with_context."""

    def test_no_context(self, queue_manager, storage):
        """Test prompt without context."""
        from openpaws.storage import QueueItem

        item = QueueItem.create(prompt="Test prompt", group_name="main")
        result = queue_manager._build_prompt_with_context(item)
        assert result == "Test prompt"

    def test_with_context(self, queue_manager, storage):
        """Test prompt with context."""
        from openpaws.storage import QueueItem

        item = QueueItem.create(
            prompt="Test prompt",
            group_name="main",
            context={"key1": "val1", "key2": "val2"},
        )
        result = queue_manager._build_prompt_with_context(item)
        assert "Context from previous conversation:" in result
        assert "- key1: val1" in result
        assert "- key2: val2" in result
        assert "Test prompt" in result
