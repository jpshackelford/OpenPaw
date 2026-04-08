"""SendStatusTool - allows the agent to send interim status messages to the user.

This tool enables the agent to communicate with the user mid-conversation,
useful for letting them know that work is in progress before the final response.
"""

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


# Type for the callback that sends messages
SendCallback = Callable[[str], Awaitable[None]]


class SendStatusAction(Action):
    """Action for sending a status message to the user."""

    message: str = Field(
        description=(
            "The status message to send to the user "
            "(e.g., 'I'm working on that...')"
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


class SendStatusExecutor(ToolExecutor):
    """Executor that sends status messages via the provided callback."""

    def __init__(self, send_callback: SendCallback | None = None):
        self._send_callback = send_callback

    def __call__(
        self,
        action: SendStatusAction,
        conversation: "BaseConversation | None" = None,  # noqa: ARG002
    ) -> SendStatusObservation:
        if self._send_callback is None:
            # No callback configured - just acknowledge
            return SendStatusObservation.from_text(
                text="Status message logged (no channel configured).",
                sent=False,
            )

        # Run the async callback - use thread pool to avoid event loop conflicts
        import asyncio
        import concurrent.futures

        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(
                    asyncio.run, self._send_callback(action.message)
                ).result()
        except Exception:
            # Fallback: try running directly if thread pool fails
            asyncio.run(self._send_callback(action.message))

        return SendStatusObservation.from_text(
            text="Status message sent to user.",
            sent=True,
        )


class SendStatusTool(ToolDefinition[SendStatusAction, SendStatusObservation]):
    """Tool for sending interim status messages to the user."""

    @classmethod
    def create(
        cls,
        conv_state: "ConversationState | None" = None,  # noqa: ARG003
        send_callback: SendCallback | None = None,
        **params,
    ) -> Sequence[Self]:
        """Create SendStatusTool instance.

        Args:
            conv_state: Optional conversation state (not used).
            send_callback: Async callback to send messages to the channel.
            **params: Additional parameters (none supported).

        Returns:
            A sequence containing a single SendStatusTool instance.
        """
        if params:
            raise ValueError(
                f"SendStatusTool doesn't accept these parameters: {list(params.keys())}"
            )

        return [
            cls(
                description=SEND_STATUS_DESCRIPTION,
                action_type=SendStatusAction,
                observation_type=SendStatusObservation,
                executor=SendStatusExecutor(send_callback),
                annotations=ToolAnnotations(
                    readOnlyHint=False,  # It does send messages
                    destructiveHint=False,  # Not destructive
                    idempotentHint=False,  # Sending twice sends two messages
                    openWorldHint=True,  # Interacts with external channel
                ),
            )
        ]
