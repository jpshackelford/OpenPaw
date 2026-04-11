"""SendStatusTool - allows the agent to send interim status messages to the user.

This tool enables the agent to communicate with the user mid-conversation,
useful for letting them know that work is in progress before the final response.

Supports two modes of operation:
1. Direct posting: Uses channel_context from agent_state to POST directly to channel
2. Callback registry: Falls back to registered callback (local mode only)

Direct posting enables remote mode conversations to send status updates,
since the callback registry only works when conversation runs in the same process.
"""

import logging
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

from openpaws.channels.base import ChannelContext

if TYPE_CHECKING:
    from openhands.sdk.conversation.base import BaseConversation
    from openhands.sdk.conversation.state import ConversationState

logger = logging.getLogger(__name__)

# Type for the callback that sends messages
SendCallback = Callable[[str], Awaitable[None]]

# Registry to store callbacks by conversation ID
# This is necessary because Tool params must be JSON-serializable,
# but callbacks are not. We store the callback here and look it up by ID.
_callback_registry: dict[str, SendCallback] = {}


def register_send_callback(conversation_id: str, callback: SendCallback) -> None:
    """Register a send callback for a conversation."""
    _callback_registry[conversation_id] = callback


def unregister_send_callback(conversation_id: str) -> None:
    """Unregister a send callback for a conversation."""
    _callback_registry.pop(conversation_id, None)


def get_send_callback(conversation_id: str) -> SendCallback | None:
    """Get the send callback for a conversation."""
    return _callback_registry.get(conversation_id)


class SendStatusAction(Action):
    """Action for sending a status message to the user."""

    message: str = Field(
        description=(
            "The status message to send to the user (e.g., 'I'm working on that...')"
        )
    )

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation."""
        content = Text()
        content.append("📤 ", style="cyan")
        content.append("Sending status: ", style="bold cyan")
        content.append(self.message, style="white")
        return content


class SendStatusObservation(Observation):
    """Observation returned after sending a status message."""

    sent: bool = Field(default=True, description="Whether the message was sent")

    @property
    def visualize(self) -> Text:
        """Return Rich Text representation."""
        content = Text()
        if self.sent:
            content.append("✓ ", style="green")
            content.append("Status message sent", style="green")
        else:
            content.append("✗ ", style="red")
            content.append("Failed to send status message", style="red")
        return content


SEND_STATUS_DESCRIPTION = """Send an interim status message to the user.

Use this tool when you determine that fulfilling the user's request will require
running commands, making file changes, or other work that takes time. This lets
the user know you've received their message and are working on it.

**When to use:**
- Before running terminal commands or making file edits
- When the task involves multiple steps
- When you need to research or explore before answering

**When NOT to use:**
- For simple questions you can answer immediately
- For conversational responses that don't require work
- After you've already completed the work (use the finish tool instead)

**Example messages:**
- "I'm on it. Let me look into that for you."
- "Working on it! I'll have a response shortly."
- "Let me check that out and get back to you."

Keep status messages brief and friendly."""


def _run_async(coro):
    """Run an async coroutine from sync context."""
    import asyncio
    import concurrent.futures

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _run_async_callback(callback, message: str) -> None:
    """Run an async callback, handling event loop conflicts."""
    _run_async(callback(message))


class SendStatusExecutor(ToolExecutor):
    """Executor that sends status messages via direct posting or callback.

    Priority order:
    1. Direct posting via channel_context (works in both local and remote mode)
    2. Callback registry lookup (works in local mode only)
    3. Log and return "not sent" (no channel configured)
    """

    def _get_channel_context(self, conversation) -> ChannelContext | None:
        """Get channel context from conversation agent_state if available."""
        if not conversation or not hasattr(conversation, "state"):
            return None
        agent_state = getattr(conversation.state, "agent_state", None)
        ctx_data = agent_state.get("channel_context") if agent_state else None
        if not ctx_data:
            return None
        try:
            return ChannelContext.from_dict(ctx_data)
        except (KeyError, TypeError):
            return None

    def _get_credential(self, conversation, credential_key: str) -> str | None:
        """Get credential from conversation secret_registry."""
        if not conversation or not hasattr(conversation, "state"):
            return None
        secret_registry = getattr(conversation.state, "secret_registry", None)
        if not secret_registry:
            return None
        secrets = secret_registry.get_secrets()
        return secrets.get(credential_key)

    def _get_callback(self, conversation) -> Callable | None:
        """Look up the callback from the registry using conversation ID."""
        if conversation and hasattr(conversation, "state"):
            return get_send_callback(str(conversation.state.id))
        return None

    def _execute_direct_post(self, ctx, message: str, credential: str) -> bool:
        """Execute the channel posting. Returns True on success."""
        from openpaws.tools.channel_poster import post_to_channel

        try:
            return _run_async(
                post_to_channel(
                    channel_type=ctx.channel_type,
                    channel_id=ctx.channel_id,
                    message=message,
                    thread_id=ctx.thread_id,
                    base_url=ctx.base_url,
                    credential=credential,
                )
            )
        except Exception as e:
            logger.exception(f"Direct posting failed: {e}")
            return False

    def _try_direct_post(
        self, action: SendStatusAction, conversation
    ) -> SendStatusObservation | None:
        """Try to post directly using channel_context. Returns None if unavailable."""
        ctx = self._get_channel_context(conversation)
        if not ctx:
            return None
        credential = self._get_credential(conversation, ctx.credential_key)
        if not credential:
            logger.warning(f"No credential found for key: {ctx.credential_key}")
            return None
        if self._execute_direct_post(ctx, action.message, credential):
            return SendStatusObservation.from_text(
                text="Status message sent.", sent=True
            )
        logger.warning("Direct posting failed, falling back")
        return None

    def _try_callback(
        self, action: SendStatusAction, conversation
    ) -> SendStatusObservation | None:
        """Try to send via callback registry. Returns None if no callback."""
        send_callback = self._get_callback(conversation)
        if send_callback is None:
            return None

        _run_async_callback(send_callback, action.message)
        return SendStatusObservation.from_text(
            text="Status message sent to user.", sent=True
        )

    def __call__(
        self,
        action: SendStatusAction,
        conversation: "BaseConversation | None" = None,
    ) -> SendStatusObservation:
        # 1. Try direct posting via channel_context
        result = self._try_direct_post(action, conversation)
        if result:
            return result

        # 2. Fall back to callback registry
        result = self._try_callback(action, conversation)
        if result:
            return result

        # 3. No channel configured
        return SendStatusObservation.from_text(
            text="Status message logged (no channel configured).", sent=False
        )


class SendStatusTool(ToolDefinition[SendStatusAction, SendStatusObservation]):
    """Tool for sending interim status messages to the user."""

    @classmethod
    def _make_annotations(cls) -> ToolAnnotations:
        """Create tool annotations for the send_status tool."""
        return ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,
        **params,  # noqa: ARG003
    ) -> Sequence[Self]:
        """Create SendStatusTool instance."""
        if params:
            raise ValueError(f"SendStatusTool doesn't accept: {list(params.keys())}")
        return [
            cls(
                description=SEND_STATUS_DESCRIPTION,
                action_type=SendStatusAction,
                observation_type=SendStatusObservation,
                executor=SendStatusExecutor(),
                annotations=cls._make_annotations(),
            )
        ]
