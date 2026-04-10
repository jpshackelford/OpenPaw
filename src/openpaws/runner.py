"""Conversation runner - integrates OpenPaws with software-agent-sdk."""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event.base import Event
from openhands.sdk.tool import Tool
from openhands.tools.delegate import DelegateTool
from openhands.tools.file_editor import FileEditorTool
from openhands.tools.preset.default import get_default_condenser
from openhands.tools.task_tracker import TaskTrackerTool
from openhands.tools.terminal import TerminalTool
from pydantic import SecretStr

from openpaws.config import Config, GroupConfig
from openpaws.tools import (
    QueueNextTool,
    SendStatusTool,
    register_queue_callback,
    register_send_callback,
    unregister_queue_callback,
    unregister_send_callback,
)

if TYPE_CHECKING:
    from openpaws.agent_server_manager import AgentServerManager
    from openpaws.scheduler import ScheduledTask

logger = logging.getLogger(__name__)


def _register_openpaws_tools() -> None:
    """Register OpenPaws custom tools with the SDK."""
    from openhands.sdk.tool import register_tool

    # Register tools if not already registered
    for tool_cls in [SendStatusTool, QueueNextTool]:
        try:
            register_tool(tool_cls.name, tool_cls)
        except ValueError:
            pass  # Already registered


# Register tools at module load time
_register_openpaws_tools()

# Type for status message callback
SendCallback = Callable[[str], Awaitable[None]]
# Type for queue callback
QueueCallback = Callable[[str, str, dict | None, int, str | None], Awaitable[str]]

# Instructions for the agent on handling immediate vs. deferred responses
CHAT_RESPONSE_INSTRUCTIONS = """
## Response Guidelines

When responding to messages from users in chat:

1. **Assess first**: Determine if you can answer the user's question directly without
   using any tools (terminal commands, file operations, etc.).

2. **Immediate responses**: If you can answer immediately from your knowledge, just
   provide the answer directly using the finish tool. No need to send a status message.

3. **Work required**: If you need to run commands, read/edit files, or do any work:
   - First, use the `send_status` tool to let the user know you're working on it
   - Example: send_status("I'm on it! Let me look into that for you.")
   - Then proceed with your work
   - Finally, use the finish tool with your complete response

Keep status messages brief and friendly. Only send ONE status message per request.
"""


@dataclass
class ConversationResult:
    """Result from running a conversation."""

    success: bool
    message: str
    events: list[Event] = field(default_factory=list)
    error: str | None = None


class ConversationRunner:
    """Runs agent conversations for scheduled tasks and channel messages.

    This class bridges OpenPaws configuration with the software-agent-sdk,
    creating and managing conversations based on tasks or incoming messages.

    Example:
        >>> from openpaws.config import load_config
        >>> config = load_config()
        >>> runner = ConversationRunner(config)
        >>> result = await runner.run_prompt("main", "Summarize today's news")
    """

    def __init__(
        self,
        config: Config,
        base_dir: Path | None = None,
        queue_callback: QueueCallback | None = None,
        server_manager: AgentServerManager | None = None,
    ):
        """Initialize the conversation runner.

        Args:
            config: OpenPaws configuration
            base_dir: Base directory for OpenPaws data (default: ~/.openpaws)
            queue_callback: Optional callback for queuing follow-up conversations
            server_manager: Optional AgentServerManager for remote conversation mode.
                           If provided, conversations run in separate agent-server
                           processes that survive daemon restarts.
        """
        self.config = config
        self.base_dir = base_dir or Path.home() / ".openpaws"
        self._queue_callback = queue_callback
        self._server_manager = server_manager

        self._llm: LLM | None = None
        # Note: Agent is not cached because it may need different tools per conversation
        # (e.g., different send_callback for different channels)

    def set_queue_callback(self, callback: QueueCallback) -> None:
        """Set or update the queue callback after initialization."""
        self._queue_callback = callback

    @property
    def use_remote_servers(self) -> bool:
        """Check if remote server mode is enabled."""
        return (
            self.config.remote_servers.enabled
            and self._server_manager is not None
        )

    @property
    def llm(self) -> LLM:
        """Get or create the LLM instance."""
        if self._llm is None:
            self._llm = self._create_llm()
        return self._llm

    @property
    def agent(self) -> Agent:
        """Get the Agent instance (created fresh each time for stateless operation)."""
        return self._create_agent()

    def _get_model(self) -> str:
        """Get the model to use, checking LLM_MODEL env var first."""
        return os.environ.get("LLM_MODEL") or self.config.agent.model

    def _get_base_url(self) -> str | None:
        """Get the base URL, checking LLM_BASE_URL env var first."""
        return os.environ.get("LLM_BASE_URL") or self.config.agent.llm_proxy

    def _get_api_key(self, model: str) -> str | None:
        """Get the appropriate API key for a model.

        Priority:
        1. LLM_API_KEY (generic, works with any proxy)
        2. Provider-specific keys (ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
        """
        # Check generic key first (useful with LiteLLM proxy)
        generic_key = os.environ.get("LLM_API_KEY")
        if generic_key:
            return generic_key

        # Fall back to provider-specific keys
        model_lower = model.lower()
        if model_lower.startswith("anthropic/") or "claude" in model_lower:
            return os.environ.get("ANTHROPIC_API_KEY")
        elif model_lower.startswith("openai/") or "gpt" in model_lower:
            return os.environ.get("OPENAI_API_KEY")
        elif model_lower.startswith("gemini/") or model_lower.startswith("google/"):
            return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

        return None

    def _build_llm_kwargs(self) -> dict[str, Any]:
        """Build kwargs dict for LLM constructor."""
        model = self._get_model()
        kwargs: dict[str, Any] = {"model": model}
        if api_key := self._get_api_key(model):
            kwargs["api_key"] = SecretStr(api_key)
        if base_url := self._get_base_url():
            kwargs["base_url"] = base_url
        if (temp := self.config.agent.temperature) is not None:
            kwargs["temperature"] = temp
        if (tokens := self.config.agent.max_tokens) is not None:
            kwargs["max_output_tokens"] = tokens
        return kwargs

    def _create_llm(self) -> LLM:
        """Create an LLM instance from configuration."""
        return LLM(**self._build_llm_kwargs())

    def _get_default_tools(self) -> list[Tool]:
        """Get the default tool specifications for OpenPaws (CLI mode, no browser).

        Always includes SendStatusTool - it will look up the callback from the
        registry at runtime based on conversation ID.
        """
        return [
            Tool(name=TerminalTool.name),
            Tool(name=FileEditorTool.name),
            Tool(name=TaskTrackerTool.name),
            Tool(name=DelegateTool.name),
            Tool(name=SendStatusTool.name),
        ]

    def _build_custom_instructions(self) -> str:
        """Build custom instructions with chat response guidelines."""
        base = CHAT_RESPONSE_INSTRUCTIONS
        if self.config.agent.system_prompt:
            return f"{self.config.agent.system_prompt}\n\n{base}"
        return base

    def _create_agent(self) -> Agent:
        """Create an Agent instance with default tools."""
        return Agent(
            llm=self.llm,
            tools=self._get_default_tools(),
            condenser=get_default_condenser(
                llm=self.llm.model_copy(update={"usage_id": "condenser"})
            ),
            system_prompt_kwargs={
                "custom_instructions": self._build_custom_instructions()
            },
        )

    def _get_group_workspace(self, group: GroupConfig) -> Path:
        """Get the workspace directory for a group."""
        workspace = self.base_dir / "groups" / group.name / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def _get_group_persistence_dir(self, group: GroupConfig) -> Path:
        """Get the persistence directory for a group."""
        persistence_dir = self.base_dir / "groups" / group.name / "sessions"
        persistence_dir.mkdir(parents=True, exist_ok=True)
        return persistence_dir

    def _make_event_collector(self, events: list[Event]):
        """Create a callback that appends events to a list."""

        def callback(event: Event) -> None:
            events.append(event)

        return callback

    def _build_callbacks(self, events: list[Event], extra: list | None) -> list:
        """Build callback list with event collector."""
        callbacks = [self._make_event_collector(events)]
        if extra:
            callbacks.extend(extra)
        return callbacks

    def _create_conversation(
        self,
        group: GroupConfig,
        conversation_id: UUID | None,
        callbacks: list,
    ) -> Conversation:
        """Create a Conversation instance for a group."""
        return Conversation(
            agent=self.agent,
            workspace=self._get_group_workspace(group),
            persistence_dir=self._get_group_persistence_dir(group),
            conversation_id=conversation_id,
            callbacks=callbacks,
        )

    def _run_conversation(
        self, conversation: Conversation, prompt: str, events: list[Event]
    ) -> ConversationResult:
        """Execute a conversation and return the result."""
        conversation.send_message(prompt)
        conversation.run()
        final_message = self._extract_final_response(events)
        conversation.close()
        return ConversationResult(success=True, message=final_message, events=events)

    def _conversation_error(
        self, e: Exception, events: list[Event]
    ) -> ConversationResult:
        """Return a failure result for conversation error."""
        return ConversationResult(
            success=False,
            message=f"Conversation failed: {e}",
            events=events,
            error=str(e),
        )

    def _register_callbacks(self, conv, send_callback: SendCallback | None) -> None:
        """Register tool callbacks for this conversation."""
        conv_id = str(conv.state.id)
        if send_callback:
            register_send_callback(conv_id, send_callback)
        if self._queue_callback:
            register_queue_callback(conv_id, self._queue_callback)

    def _unregister_callbacks(self, conv) -> None:
        """Unregister tool callbacks for this conversation."""
        conv_id = str(conv.state.id)
        unregister_send_callback(conv_id)
        unregister_queue_callback(conv_id)

    async def _execute_prompt_local(
        self, group: GroupConfig, prompt: str, conversation_id, callbacks, send_callback
    ) -> ConversationResult:
        """Execute the prompt locally with callback registration and cleanup."""
        events: list[Event] = []
        conv = None
        try:
            cbs = self._build_callbacks(events, callbacks)
            conv = self._create_conversation(group, conversation_id, cbs)
            self._register_callbacks(conv, send_callback)
            return self._run_conversation(conv, prompt, events)
        except Exception as e:
            return self._conversation_error(e, events)
        finally:
            if conv:
                self._unregister_callbacks(conv)

    async def _execute_prompt_remote(
        self, group: GroupConfig, prompt: str, send_callback: SendCallback | None
    ) -> ConversationResult:
        """Execute the prompt via remote agent-server.

        This runs the conversation in a separate process that survives
        daemon restarts. The agent-server handles persistence and can
        be reconnected to after a daemon restart.
        """
        try:
            # Get or create server for this group
            server = await self._server_manager.get_or_create_server(group.name)
            logger.info(f"Using agent-server on port {server.port} for {group.name}")

            # Start or resume conversation on the server
            workspace = self._get_group_workspace(group)
            agent_config = self._build_agent_config()
            conv_id = await self._server_manager.start_conversation(
                group_id=group.name,
                agent_config=agent_config,
                workspace=workspace,
            )
            logger.info(f"Remote conversation {conv_id} for {group.name}")

            # Send the message
            await self._server_manager.send_message(group.name, prompt)

            # Trigger the run
            await self._server_manager.run_conversation(group.name)

            # Wait for completion and get the response
            response = await self._wait_for_remote_completion(group.name, send_callback)
            return ConversationResult(success=True, message=response)

        except Exception as e:
            logger.exception(f"Remote conversation error for {group.name}: {e}")
            return ConversationResult(
                success=False,
                message=f"Remote conversation failed: {e}",
                error=str(e),
            )

    def _build_agent_config(self) -> dict:
        """Build agent configuration dict for remote server."""
        return {
            "model": self._get_model(),
            "base_url": self._get_base_url(),
            "api_key": self._get_api_key(self._get_model()),
            "temperature": self.config.agent.temperature,
            "max_tokens": self.config.agent.max_tokens,
            "system_prompt": self._build_custom_instructions(),
        }

    async def _wait_for_remote_completion(
        self, group_id: str, send_callback: SendCallback | None
    ) -> str:
        """Wait for remote conversation to complete and return the response.

        This polls the conversation status until it's no longer running.
        In the future, this could use WebSocket events for real-time updates.
        """
        import asyncio

        max_wait = 600  # 10 minutes max
        poll_interval = 2  # seconds

        for _ in range(max_wait // poll_interval):
            status = await self._server_manager.get_conversation_status(group_id)

            if status is None:
                return "Conversation not found"

            if status in ("completed", "finished", "idle"):
                # Get the final response from the conversation
                return await self._get_remote_response(group_id)

            if status == "error":
                return "Conversation encountered an error"

            await asyncio.sleep(poll_interval)

        return "Conversation timed out waiting for completion"

    async def _get_remote_response(self, group_id: str) -> str:
        """Get the final response from a remote conversation.

        Fetches the conversation events and extracts the final response.
        """
        # For now, return a placeholder - full implementation would fetch
        # events from the server and extract the response
        # This requires the agent-server to expose an events endpoint
        return "Remote conversation completed (response extraction not yet implemented)"

    async def run_prompt(
        self,
        group_name: str,
        prompt: str,
        *,
        conversation_id: UUID | None = None,
        callbacks: list | None = None,
        send_callback: SendCallback | None = None,
    ) -> ConversationResult:
        """Run a conversation with a prompt for a specific group."""
        group = self.config.groups.get(group_name)
        if not group:
            return self._group_not_found_result(group_name)

        # Use remote servers if enabled
        if self.use_remote_servers:
            return await self._execute_prompt_remote(group, prompt, send_callback)

        return await self._execute_prompt_local(
            group, prompt, conversation_id, callbacks, send_callback
        )

    def _group_not_found_result(self, group_name: str) -> ConversationResult:
        """Return a failure result for unknown group."""
        return ConversationResult(
            success=False,
            message=f"Group '{group_name}' not found",
            error=f"Unknown group: {group_name}",
        )

    async def run_task(self, task: ScheduledTask) -> ConversationResult:
        """Run a scheduled task."""
        cfg = task.config
        return await self.run_prompt(group_name=cfg.group, prompt=cfg.prompt)

    async def run_message(
        self,
        group_name: str,
        message: str,
        *,
        sender: str | None = None,
        conversation_id: UUID | None = None,
        send_callback: SendCallback | None = None,
    ) -> ConversationResult:
        """Handle an incoming message from a channel.

        Args:
            group_name: Name of the group this message belongs to.
            message: The user's message text.
            sender: Optional sender name for context.
            conversation_id: Optional conversation ID for persistence.
            send_callback: Optional callback for sending status messages to the channel.
        """
        return await self.run_prompt(
            group_name=group_name,
            prompt=message,
            conversation_id=conversation_id,
            send_callback=send_callback,
        )

    def _extract_finish_action_message(self, event) -> str | None:
        """Extract message from FinishAction event if present."""
        from openhands.sdk.event import ActionEvent

        if not isinstance(event, ActionEvent):
            return None
        action = getattr(event, "action", None)
        if action and getattr(action, "kind", None) == "FinishAction":
            return getattr(action, "message", None)
        return None

    def _extract_assistant_message(self, event) -> str | None:
        """Extract text from assistant message event if present."""
        from openhands.sdk.event import MessageEvent

        if not isinstance(event, MessageEvent):
            return None
        msg = event.llm_message
        if msg.role == "assistant" and msg.content:
            texts = [c.text for c in msg.content if hasattr(c, "text")]
            return "\n".join(texts) if texts else None
        return None

    def _extract_final_response(self, events: list[Event]) -> str:
        """Extract the final agent response from events."""
        for event in reversed(events):
            if msg := self._extract_finish_action_message(event):
                return msg
            if msg := self._extract_assistant_message(event):
                return msg
        return "No response generated"
