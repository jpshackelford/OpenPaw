"""QueueNextTool - allows agents to queue follow-up conversations.

This tool enables agents to schedule follow-up conversations that will be
processed by the heartbeat dispatcher, enabling multi-step workflows that
span multiple conversations.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Self

from openhands.sdk.tool.tool import (
    Action,
    Observation,
    ToolAnnotations,
    ToolDefinition,
    ToolExecutor,
)
from pydantic import Field
from rich.text import Text

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

# Type for the callback that queues conversations
QueueCallback = Callable[[str, str, dict | None, int, str | None], Awaitable[str]]

# Registry to store callbacks by conversation ID
_callback_registry: dict[str, QueueCallback] = {}


def register_queue_callback(conversation_id: str, callback: QueueCallback) -> None:
    """Register a queue callback for a conversation."""
    _callback_registry[conversation_id] = callback


def unregister_queue_callback(conversation_id: str) -> None:
    """Unregister a queue callback for a conversation."""
    _callback_registry.pop(conversation_id, None)


def get_queue_callback(conversation_id: str) -> QueueCallback | None:
    """Get the queue callback for a conversation."""
    return _callback_registry.get(conversation_id)


class QueueNextAction(Action):
    """Action for queuing a follow-up conversation."""

    prompt: str = Field(
        description="The prompt for the follow-up conversation"
    )
    group_name: str = Field(
        description="The group to run the follow-up conversation in"
    )
    context: dict | None = Field(
        default=None,
        description="Optional context to pass to the follow-up conversation",
    )
    priority: int = Field(
        default=0,
        description="Priority level (higher = processed first). Default 0.",
    )
    workflow_id: str | None = Field(
        default=None,
        description="Optional workflow ID to group related conversations",
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation."""
        content = Text()
        content.append("📋 ", style="cyan")
        content.append("Queuing follow-up: ", style="bold cyan")
        prompt_preview = (
            self.prompt[:50] + "..." if len(self.prompt) > 50 else self.prompt
        )
        content.append(prompt_preview, style="white")
        content.append(f" (group={self.group_name})", style="dim")
        return content


class QueueNextObservation(Observation):
    """Observation returned after queuing a conversation."""

    queued: bool = Field(
        default=True, description="Whether the conversation was queued"
    )
    item_id: str | None = Field(
        default=None, description="The ID of the queued item"
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation."""
        content = Text()
        if self.queued:
            content.append("✓ ", style="green")
            content.append("Follow-up queued", style="green")
            if self.item_id:
                content.append(f" (id={self.item_id[:8]}...)", style="dim")
        else:
            content.append("✗ ", style="red")
            content.append("Failed to queue follow-up conversation", style="red")
        return content


QUEUE_NEXT_DESCRIPTION = """Queue a follow-up conversation for later processing.

Use this tool when a workflow should continue in a separate conversation,
allowing the current conversation to complete and free resources. The queued
conversation will be processed by the heartbeat dispatcher at the configured
interval.

**When to use:**
- Multi-step workflows that benefit from context separation
- Long-running operations that should be broken into discrete steps
- Workflows that need to wait for external events between steps

**Parameters:**
- prompt: The task description for the follow-up conversation
- group_name: Which group to run the conversation in (must match config)
- context: Optional dict of key-value pairs to pass to the next step
- priority: Higher numbers are processed first (default: 0)
- workflow_id: Optional ID to track related conversations

**Example workflow:**
1. Current conversation: "Build remediation plan" → queue next step
2. Queued conversation: "Open PR with fixes" → queue validation
3. Queued conversation: "Run tests and validate" → complete

Keep prompts specific and actionable. Include relevant context from current work."""


def _run_async_callback(callback, *args) -> str | None:
    """Run an async callback, handling event loop conflicts."""
    import asyncio
    import concurrent.futures

    try:
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, callback(*args)).result()
    except Exception:
        return asyncio.run(callback(*args))


class QueueNextExecutor(ToolExecutor):
    """Executor that queues conversations via callback looked up from registry."""

    def _get_callback(self, conversation) -> QueueCallback | None:
        """Look up the callback from the registry using conversation ID."""
        if conversation and hasattr(conversation, "state"):
            return get_queue_callback(str(conversation.state.id))
        return None

    def __call__(
        self,
        action: QueueNextAction,
        conversation: BaseConversation | None = None,
    ) -> QueueNextObservation:
        queue_callback = self._get_callback(conversation)

        if queue_callback is None:
            return QueueNextObservation.from_text(
                text="Queue not available (no callback configured).",
                queued=False,
                item_id=None,
            )

        item_id = _run_async_callback(
            queue_callback,
            action.prompt,
            action.group_name,
            action.context,
            action.priority,
            action.workflow_id,
        )

        if item_id:
            return QueueNextObservation.from_text(
                text=f"Follow-up conversation queued with id={item_id[:8]}...",
                queued=True,
                item_id=item_id,
            )
        return QueueNextObservation.from_text(
            text="Failed to queue follow-up conversation.",
            queued=False,
            item_id=None,
        )


class QueueNextTool(ToolDefinition[QueueNextAction, QueueNextObservation]):
    """Tool for queuing follow-up conversations."""

    @classmethod
    def _make_annotations(cls) -> ToolAnnotations:
        """Create tool annotations for the queue_next tool."""
        return ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )

    @classmethod
    def create(
        cls,
        conv_state: ConversationState | None = None,
        **params,  # noqa: ARG003
    ) -> Sequence[Self]:
        """Create QueueNextTool instance."""
        if params:
            raise ValueError(f"QueueNextTool doesn't accept: {list(params.keys())}")
        return [
            cls(
                description=QUEUE_NEXT_DESCRIPTION,
                action_type=QueueNextAction,
                observation_type=QueueNextObservation,
                executor=QueueNextExecutor(),
                annotations=cls._make_annotations(),
            )
        ]
