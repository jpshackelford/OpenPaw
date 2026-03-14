"""Conversation runner - integrates OpenPaws with software-agent-sdk."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import SecretStr

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.event.base import Event

from openpaws.config import AgentConfig, Config, GroupConfig

if TYPE_CHECKING:
    from openpaws.scheduler import ScheduledTask


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
    ):
        """Initialize the conversation runner.

        Args:
            config: OpenPaws configuration
            base_dir: Base directory for OpenPaws data (default: ~/.openpaws)
        """
        self.config = config
        self.base_dir = base_dir or Path.home() / ".openpaws"

        self._llm: LLM | None = None
        self._agent: Agent | None = None

    @property
    def llm(self) -> LLM:
        """Get or create the LLM instance."""
        if self._llm is None:
            self._llm = self._create_llm()
        return self._llm

    @property
    def agent(self) -> Agent:
        """Get or create the Agent instance."""
        if self._agent is None:
            self._agent = self._create_agent()
        return self._agent

    def _create_llm(self) -> LLM:
        """Create an LLM instance from configuration."""
        agent_config = self.config.agent

        # Build LLM kwargs
        llm_kwargs: dict[str, Any] = {
            "model": agent_config.model,
        }

        # API key from environment (supports various providers via litellm)
        # The model prefix determines which env var to check
        api_key = self._get_api_key_for_model(agent_config.model)
        if api_key:
            llm_kwargs["api_key"] = SecretStr(api_key)

        # LiteLLM proxy support
        if agent_config.llm_proxy:
            llm_kwargs["base_url"] = agent_config.llm_proxy

        # Optional settings from agent config
        if agent_config.temperature is not None:
            llm_kwargs["temperature"] = agent_config.temperature
        if agent_config.max_tokens is not None:
            llm_kwargs["max_output_tokens"] = agent_config.max_tokens

        return LLM(**llm_kwargs)

    def _get_api_key_for_model(self, model: str) -> str | None:
        """Get the appropriate API key for a model.

        Checks environment variables based on model provider prefix.
        """
        model_lower = model.lower()

        # Check for provider-specific keys
        if model_lower.startswith("anthropic/") or "claude" in model_lower:
            return os.environ.get("ANTHROPIC_API_KEY")
        elif model_lower.startswith("openai/") or "gpt" in model_lower:
            return os.environ.get("OPENAI_API_KEY")
        elif model_lower.startswith("gemini/") or model_lower.startswith("google/"):
            return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")

        # Fallback to generic key (useful with LiteLLM proxy)
        return os.environ.get("LLM_API_KEY")

    def _create_agent(self) -> Agent:
        """Create an Agent instance."""
        agent_config = self.config.agent

        agent_kwargs: dict[str, Any] = {
            "llm": self.llm,
        }

        # Custom system prompt if provided
        if agent_config.system_prompt:
            agent_kwargs["system_prompt_kwargs"] = {
                "custom_instructions": agent_config.system_prompt
            }

        return Agent(**agent_kwargs)

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

    def _collect_events(self, events: list[Event]) -> list[Event]:
        """Create a callback that collects events."""
        def callback(event: Event) -> None:
            events.append(event)
        return callback

    async def run_prompt(
        self,
        group_name: str,
        prompt: str,
        *,
        conversation_id: UUID | None = None,
        callbacks: list | None = None,
    ) -> ConversationResult:
        """Run a conversation with a prompt for a specific group.

        Args:
            group_name: Name of the group to run the conversation for
            prompt: The prompt/message to send to the agent
            conversation_id: Optional ID to resume an existing conversation
            callbacks: Optional list of event callbacks

        Returns:
            ConversationResult with success status and collected events
        """
        # Get group config
        group = self.config.groups.get(group_name)
        if not group:
            return ConversationResult(
                success=False,
                message=f"Group '{group_name}' not found",
                error=f"Unknown group: {group_name}",
            )

        events: list[Event] = []
        all_callbacks = [self._collect_events(events)]
        if callbacks:
            all_callbacks.extend(callbacks)

        try:
            # Create conversation
            conversation = Conversation(
                agent=self.agent,
                workspace=self._get_group_workspace(group),
                persistence_dir=self._get_group_persistence_dir(group),
                conversation_id=conversation_id,
                callbacks=all_callbacks,
            )

            # Send message and run
            conversation.send_message(prompt)
            conversation.run()

            # Get final message from events
            final_message = self._extract_final_response(events)

            conversation.close()

            return ConversationResult(
                success=True,
                message=final_message,
                events=events,
            )

        except Exception as e:
            return ConversationResult(
                success=False,
                message=f"Conversation failed: {e}",
                events=events,
                error=str(e),
            )

    async def run_task(self, task: ScheduledTask) -> ConversationResult:
        """Run a scheduled task.

        Args:
            task: The scheduled task to run

        Returns:
            ConversationResult with the task execution result
        """
        return await self.run_prompt(
            group_name=task.config.group,
            prompt=task.config.prompt,
        )

    async def run_message(
        self,
        group_name: str,
        message: str,
        *,
        sender: str | None = None,
        conversation_id: UUID | None = None,
    ) -> ConversationResult:
        """Handle an incoming message from a channel.

        Args:
            group_name: Name of the group the message is for
            message: The message content
            sender: Optional identifier of who sent the message
            conversation_id: Optional ID to continue an existing conversation

        Returns:
            ConversationResult with the agent's response
        """
        # Could add sender context to prompt in the future
        return await self.run_prompt(
            group_name=group_name,
            prompt=message,
            conversation_id=conversation_id,
        )

    def _extract_final_response(self, events: list[Event]) -> str:
        """Extract the final agent response from events.

        Looks for the last assistant message or finish event.
        """
        from openhands.sdk.event.message import MessageEvent

        for event in reversed(events):
            if isinstance(event, MessageEvent) and event.role == "assistant":
                return event.content
        return "No response generated"
