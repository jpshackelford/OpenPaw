"""Tests for QueueNextTool."""

import pytest

from openpaws.tools.queue_next import (
    QueueNextAction,
    QueueNextExecutor,
    QueueNextObservation,
    QueueNextTool,
    get_queue_callback,
    register_queue_callback,
    unregister_queue_callback,
)


class TestCallbackRegistry:
    """Tests for callback registration."""

    def test_register_and_get_callback(self):
        """Test registering and retrieving a callback."""

        async def callback(prompt, group_name, context, priority, workflow_id):
            return "test-id"

        register_queue_callback("conv-1", callback)
        assert get_queue_callback("conv-1") is callback

        # Cleanup
        unregister_queue_callback("conv-1")

    def test_unregister_callback(self):
        """Test unregistering a callback."""

        async def callback(prompt, group_name, context, priority, workflow_id):
            return "test-id"

        register_queue_callback("conv-2", callback)
        unregister_queue_callback("conv-2")
        assert get_queue_callback("conv-2") is None

    def test_get_nonexistent_callback(self):
        """Test getting a callback that doesn't exist."""
        assert get_queue_callback("nonexistent") is None

    def test_unregister_nonexistent_callback(self):
        """Test unregistering a callback that doesn't exist (should not raise)."""
        unregister_queue_callback("nonexistent")  # Should not raise


class TestQueueNextAction:
    """Tests for QueueNextAction."""

    def test_action_creation(self):
        """Test creating an action with required fields."""
        action = QueueNextAction(prompt="Test prompt", group_name="main")
        assert action.prompt == "Test prompt"
        assert action.group_name == "main"
        assert action.context is None
        assert action.priority == 0
        assert action.workflow_id is None

    def test_action_with_all_fields(self):
        """Test creating an action with all fields."""
        action = QueueNextAction(
            prompt="Test prompt",
            group_name="main",
            context={"key": "value"},
            priority=10,
            workflow_id="wf-123",
        )
        assert action.context == {"key": "value"}
        assert action.priority == 10
        assert action.workflow_id == "wf-123"

    def test_action_visualize(self):
        """Test the visualize property."""
        action = QueueNextAction(prompt="Test prompt", group_name="main")
        viz = action.visualize
        assert "Queuing follow-up" in viz.plain
        assert "Test prompt" in viz.plain

    def test_action_visualize_truncates_long_prompt(self):
        """Test that long prompts are truncated in visualization."""
        long_prompt = "x" * 100
        action = QueueNextAction(prompt=long_prompt, group_name="main")
        viz = action.visualize
        assert "..." in viz.plain
        assert len(action.prompt) == 100  # Original still full


class TestQueueNextObservation:
    """Tests for QueueNextObservation."""

    def test_observation_success(self):
        """Test successful observation."""
        obs = QueueNextObservation(queued=True, item_id="abc123def456")
        assert obs.queued is True
        assert obs.item_id == "abc123def456"

    def test_observation_failure(self):
        """Test failure observation."""
        obs = QueueNextObservation(queued=False, item_id=None)
        assert obs.queued is False
        assert obs.item_id is None

    def test_observation_visualize_success(self):
        """Test success visualization."""
        obs = QueueNextObservation(queued=True, item_id="abc123def456")
        viz = obs.visualize
        assert "queued" in viz.plain.lower()
        assert "abc123de" in viz.plain  # Shows truncated ID

    def test_observation_visualize_failure(self):
        """Test failure visualization."""
        obs = QueueNextObservation(queued=False, item_id=None)
        viz = obs.visualize
        assert "Failed" in viz.plain


class TestQueueNextExecutor:
    """Tests for QueueNextExecutor."""

    def test_executor_no_callback(self):
        """Test executor when no callback is registered."""
        executor = QueueNextExecutor()
        action = QueueNextAction(prompt="Test", group_name="main")

        obs = executor(action, conversation=None)
        assert obs.queued is False
        assert "not available" in obs.text.lower()

    @pytest.mark.asyncio
    async def test_executor_with_callback(self):
        """Test executor with registered callback."""

        async def callback(prompt, group_name, context, priority, workflow_id):
            return f"item-{prompt[:4]}"

        # Create a mock conversation with state
        class MockState:
            id = "test-conv-id"

        class MockConversation:
            state = MockState()

        register_queue_callback("test-conv-id", callback)
        try:
            executor = QueueNextExecutor()
            action = QueueNextAction(prompt="Test prompt", group_name="main")

            obs = executor(action, conversation=MockConversation())
            assert obs.queued is True
            assert obs.item_id == "item-Test"
        finally:
            unregister_queue_callback("test-conv-id")


class TestQueueNextTool:
    """Tests for QueueNextTool creation."""

    def test_tool_create(self):
        """Test creating the tool."""
        tools = QueueNextTool.create()
        assert len(tools) == 1
        assert isinstance(tools[0], QueueNextTool)

    def test_tool_annotations(self):
        """Test tool annotations."""
        tools = QueueNextTool.create()
        tool = tools[0]
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is False

    def test_tool_description(self):
        """Test tool has a description."""
        tools = QueueNextTool.create()
        tool = tools[0]
        assert "Queue" in tool.description
        assert "follow-up" in tool.description

    def test_tool_create_with_invalid_params(self):
        """Test that invalid params raise ValueError."""
        with pytest.raises(ValueError):
            QueueNextTool.create(invalid_param="test")
