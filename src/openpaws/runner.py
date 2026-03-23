"""Conversation runner - integrates OpenPaws with software-agent-sdk."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.event.base import Event
from openhands.sdk.workspace import LocalWorkspace
from openhands.workspace import OpenHandsCloudWorkspace
from pydantic import SecretStr

from openpaws.config import Config, GroupConfig

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

    Supports two execution modes:
    - Local: Uses local workspace (default)
    - Cloud: Uses OpenHands Cloud sandboxes when OH_API_KEY is configured

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
        self._cloud_api_key: str | None = None

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

    def _build_llm_kwargs(self) -> dict[str, Any]:
        """Build kwargs dict for LLM constructor."""
        agent_config = self.config.agent
        kwargs: dict[str, Any] = {"model": agent_config.model}

        api_key = self._get_api_key_for_model(agent_config.model)
        if api_key:
            kwargs["api_key"] = SecretStr(api_key)
        if agent_config.llm_proxy:
            kwargs["base_url"] = agent_config.llm_proxy
        if agent_config.temperature is not None:
            kwargs["temperature"] = agent_config.temperature
        if agent_config.max_tokens is not None:
            kwargs["max_output_tokens"] = agent_config.max_tokens
        return kwargs

    def _create_llm(self) -> LLM:
        """Create an LLM instance from configuration."""
        return LLM(**self._build_llm_kwargs())

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

    def _get_cloud_api_key(self) -> str | None:
        """Get the OpenHands Cloud API key from config or environment."""
        if self._cloud_api_key:
            return self._cloud_api_key
        key = self.config.agent.cloud_api_key
        if not key:
            key = os.environ.get("OH_API_KEY") or os.environ.get(
                "OPENHANDS_CLOUD_API_KEY"
            )
        self._cloud_api_key = key
        return key

    def uses_cloud_workspace(self) -> bool:
        """Check if cloud workspace mode is enabled."""
        return self._get_cloud_api_key() is not None

    def _get_group_workspace_path(self, group: GroupConfig) -> Path:
        """Get the workspace directory path for a group."""
        workspace = self.base_dir / "groups" / group.name / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    def _get_group_persistence_dir(self, group: GroupConfig) -> Path:
        """Get the persistence directory for a group."""
        persistence_dir = self.base_dir / "groups" / group.name / "sessions"
        persistence_dir.mkdir(parents=True, exist_ok=True)
        return persistence_dir

    def _create_cloud_workspace(self):
        """Create an OpenHands Cloud workspace."""
        agent_config = self.config.agent
        cloud_api_key = self._get_cloud_api_key()
        if not cloud_api_key:
            raise ValueError("Cloud API key required for cloud workspace")

        return OpenHandsCloudWorkspace(
            cloud_api_url=agent_config.cloud_api_url,
            cloud_api_key=cloud_api_key,
            sandbox_spec_id=agent_config.sandbox_spec_id,
            keep_alive=agent_config.keep_alive,
        )

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

    def _create_local_conversation(
        self, group: GroupConfig, conversation_id: UUID | None, callbacks: list
    ) -> Conversation:
        """Create a local Conversation instance for a group."""
        workspace = LocalWorkspace(
            working_dir=str(self._get_group_workspace_path(group))
        )
        return Conversation(
            agent=self.agent,
            workspace=workspace,
            persistence_dir=self._get_group_persistence_dir(group),
            conversation_id=conversation_id,
            callbacks=callbacks,
        )

    def _create_cloud_conversation(
        self, group: GroupConfig, conversation_id: UUID | None, callbacks: list
    ) -> Conversation:
        """Create a cloud-based Conversation instance for a group."""
        workspace = self._create_cloud_workspace()
        return Conversation(
            agent=self.agent,
            workspace=workspace,
            persistence_dir=self._get_group_persistence_dir(group),
            conversation_id=conversation_id,
            callbacks=callbacks,
        )

    def _should_use_cloud(self, runtime: str = "auto") -> bool:
        """Determine if cloud workspace should be used based on runtime setting.

        Args:
            runtime: "auto", "cloud", or "local"

        Returns:
            True if cloud workspace should be used

        Raises:
            ValueError: If runtime is not a valid option
            ValueError: If runtime is "cloud" but no API key is available
        """
        if runtime not in ("auto", "cloud", "local"):
            raise ValueError(
                f"Invalid runtime '{runtime}'. Must be 'auto', 'cloud', or 'local'"
            )
        if runtime == "local":
            return False
        if runtime == "cloud":
            if not self._get_cloud_api_key():
                raise ValueError(
                    "Runtime 'cloud' requires OH_API_KEY or OPENHANDS_CLOUD_API_KEY"
                )
            return True
        # runtime == "auto"
        return self.uses_cloud_workspace()

    def _create_conversation(
        self,
        group: GroupConfig,
        conversation_id: UUID | None,
        callbacks: list,
        runtime: str = "auto",
    ) -> Conversation:
        """Create a Conversation instance for a group.

        Args:
            group: Group configuration
            conversation_id: Optional conversation ID for persistence
            callbacks: Event callbacks
            runtime: "auto", "cloud", or "local"
        """
        if self._should_use_cloud(runtime):
            return self._create_cloud_conversation(group, conversation_id, callbacks)
        return self._create_local_conversation(group, conversation_id, callbacks)

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

    async def run_prompt(
        self,
        group_name: str,
        prompt: str,
        *,
        conversation_id: UUID | None = None,
        callbacks: list | None = None,
        runtime: str = "auto",
    ) -> ConversationResult:
        """Run a conversation with a prompt for a specific group.

        Args:
            group_name: Name of the group to run the conversation for
            prompt: The prompt to send to the agent
            conversation_id: Optional conversation ID for persistence
            callbacks: Optional list of event callbacks
            runtime: Where to run - "auto", "cloud", or "local"
        """
        group = self.config.groups.get(group_name)
        if not group:
            return self._group_not_found_result(group_name)

        events: list[Event] = []
        all_callbacks = self._build_callbacks(events, callbacks)
        try:
            conv = self._create_conversation(
                group, conversation_id, all_callbacks, runtime=runtime
            )
            return self._run_conversation(conv, prompt, events)
        except Exception as e:
            return self._conversation_error(e, events)

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
        return await self.run_prompt(
            group_name=cfg.group,
            prompt=cfg.prompt,
            runtime=cfg.runtime,
        )

    async def run_message(
        self,
        group_name: str,
        message: str,
        *,
        sender: str | None = None,
        conversation_id: UUID | None = None,
    ) -> ConversationResult:
        """Handle an incoming message from a channel."""
        # sender can be used for context in the future
        return await self.run_prompt(
            group_name=group_name, prompt=message, conversation_id=conversation_id
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
