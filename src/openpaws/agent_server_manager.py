"""Agent Server Manager for OpenPaws.

Manages lifecycle of agent-server subprocesses and provides RemoteConversation
instances for chat interactions. Agent servers run independently of the OpenPaws
daemon, allowing the daemon to restart without interrupting running conversations.

Architecture:
    OpenPaws Daemon
        │
        ├── AgentServerManager
        │       │
        │       ├── spawn_server() → starts agent-server subprocess
        │       ├── get_or_create_conversation(group_id) → RemoteConversation
        │       ├── pause_conversation(group_id) → HTTP POST /pause
        │       └── shutdown() → pause all, daemon can exit
        │
        └── HTTP/WebSocket ──► agent-server processes (independent)

Shutdown behavior:
    - Daemon receives SIGTERM
    - AgentServerManager.shutdown() called
    - Optionally pauses all active conversations (non-blocking)
    - Daemon exits immediately
    - Agent servers continue running, finish current work, persist state

Startup behavior:
    - Daemon starts
    - AgentServerManager.startup() called
    - Reads server registry to find running servers
    - Reconnects to existing servers or spawns new ones
    - Resumes any paused conversations
"""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)

# Port range for agent servers
DEFAULT_PORT_START = 18000
DEFAULT_PORT_END = 18100

# Registry filename
REGISTRY_FILENAME = "agent_servers.json"


@dataclass
class ServerInfo:
    """Information about a running agent-server process."""

    pid: int
    port: int
    conversation_id: UUID | None = None
    group_id: str | None = None

    def to_dict(self) -> dict:
        conv_id = str(self.conversation_id) if self.conversation_id else None
        return {
            "pid": self.pid,
            "port": self.port,
            "conversation_id": conv_id,
            "group_id": self.group_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ServerInfo":
        conv_id_str = data.get("conversation_id")
        conv_id = UUID(conv_id_str) if conv_id_str else None
        return cls(
            pid=data["pid"],
            port=data["port"],
            conversation_id=conv_id,
            group_id=data.get("group_id"),
        )


@dataclass
class AgentServerManager:
    """Manages agent-server subprocess lifecycle.

    Responsibilities:
    - Spawn agent-server processes on demand
    - Track running servers and their conversations
    - Provide RemoteConversation access to conversations
    - Handle graceful shutdown (pause + detach)
    - Handle startup (reconnect to existing servers)
    """

    base_dir: Path
    port_start: int = DEFAULT_PORT_START
    port_end: int = DEFAULT_PORT_END

    # Runtime state
    _servers: dict[str, ServerInfo] = field(default_factory=dict)
    _http_client: httpx.AsyncClient | None = field(default=None, init=False)
    _next_port: int = field(default=0, init=False)

    def __post_init__(self):
        self._next_port = self.port_start
        self.base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def registry_path(self) -> Path:
        return self.base_dir / REGISTRY_FILENAME

    @property
    def conversations_dir(self) -> Path:
        """Directory where agent-servers store conversation data."""
        return self.base_dir / "conversations"

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def startup(self) -> None:
        """Initialize manager and reconnect to any existing servers."""
        self._http_client = httpx.AsyncClient(timeout=30.0)
        self.conversations_dir.mkdir(parents=True, exist_ok=True)

        # Load existing server registry
        await self._load_registry()

        # Check which servers are still running
        await self._reconcile_servers()

        logger.info(
            f"AgentServerManager started with {len(self._servers)} active servers"
        )

    async def shutdown(self, pause_conversations: bool = True) -> None:
        """Shutdown manager, optionally pausing active conversations.

        Args:
            pause_conversations: If True, send pause request to all active
                conversations before detaching. Servers will finish their
                current work and persist state.
        """
        if pause_conversations:
            await self._pause_all_conversations()

        # Save registry so we can reconnect on restart
        await self._save_registry()

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        logger.info("AgentServerManager shutdown complete - servers continue running")

    # =========================================================================
    # Server Management
    # =========================================================================

    async def get_or_create_server(self, group_id: str) -> ServerInfo:
        """Get existing server for group or spawn a new one."""
        if group_id in self._servers:
            server = self._servers[group_id]
            if await self._is_server_healthy(server):
                return server
            else:
                logger.warning(f"Server for {group_id} unhealthy, respawning")
                del self._servers[group_id]

        # Spawn new server
        server = await self._spawn_server(group_id)
        self._servers[group_id] = server
        await self._save_registry()
        return server

    async def _spawn_server(self, group_id: str) -> ServerInfo:
        """Spawn a new agent-server subprocess."""
        port = self._allocate_port()
        process = self._start_server_process(port)
        logger.info(f"Spawned agent-server for {group_id} on port {port}")
        await self._wait_for_server_ready(port)
        return ServerInfo(pid=process.pid, port=port, group_id=group_id)

    def _start_server_process(self, port: int) -> subprocess.Popen:
        """Start agent-server subprocess on given port."""
        cmd = ["agent-server", "--host", "127.0.0.1", "--port", str(port)]
        env = os.environ.copy()
        env["OPENHANDS_CONVERSATIONS_DIR"] = str(self.conversations_dir)

        # Redirect output to log file for debugging
        log_dir = self.base_dir / "logs" / "servers"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"agent-server-{port}.log"
        log_handle = open(log_file, "a")

        return subprocess.Popen(
            cmd,
            env=env,
            start_new_session=True,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )

    def _is_port_available(self, port: int) -> bool:
        """Check if a port is available for binding."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return True
        except OSError:
            return False

    def _allocate_port(self) -> int:
        """Allocate next available port."""
        # Try ports in sequence, skipping unavailable ones
        max_attempts = self.port_end - self.port_start + 1
        for _ in range(max_attempts):
            port = self._next_port
            self._next_port += 1
            if self._next_port > self.port_end:
                self._next_port = self.port_start

            if self._is_port_available(port):
                return port
            logger.debug(f"Port {port} unavailable, trying next")

        raise RuntimeError(
            f"No available ports in range {self.port_start}-{self.port_end}"
        )

    async def _wait_for_server_ready(
        self, port: int, timeout: float = 30.0, interval: float = 0.5
    ) -> None:
        """Wait for server to be ready to accept connections."""
        url = f"http://127.0.0.1:{port}/health"
        deadline = asyncio.get_event_loop().time() + timeout
        attempt = 0

        while asyncio.get_event_loop().time() < deadline:
            try:
                response = await self._http_client.get(url)
                if response.status_code == 200:
                    logger.debug(f"Server on port {port} is ready after {attempt} attempts")
                    return
            except httpx.ConnectError as e:
                attempt += 1
                if attempt % 10 == 0:  # Log every ~5 seconds
                    logger.debug(f"Waiting for server on port {port} (attempt {attempt}): {e}")
            await asyncio.sleep(interval)

        raise TimeoutError(f"Server on port {port} did not become ready in {timeout}s")

    async def _is_server_healthy(self, server: ServerInfo) -> bool:
        """Check if server is running and healthy."""
        # First check if process is running
        try:
            os.kill(server.pid, 0)  # Signal 0 = check if process exists
        except OSError:
            return False

        # Then check HTTP health
        try:
            url = f"http://127.0.0.1:{server.port}/health"
            response = await self._http_client.get(url)
            return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False
        except Exception as e:
            logger.warning(
                f"Unexpected error checking health for server on port {server.port}: {e}"
            )
            return False

    # =========================================================================
    # Conversation Management
    # =========================================================================

    def get_server_url(self, group_id: str) -> str | None:
        """Get base URL for a group's server."""
        server = self._servers.get(group_id)
        if server:
            return f"http://127.0.0.1:{server.port}"
        return None

    async def start_conversation(
        self, group_id: str, agent_config: dict, workspace_dir: str | Path
    ) -> UUID:
        """Start a new conversation on the server for this group."""
        server = await self.get_or_create_server(group_id)
        conversation_id = await self._create_conversation(
            server.port, agent_config, workspace_dir
        )
        server.conversation_id = conversation_id
        await self._save_registry()
        logger.info(f"Started conversation {conversation_id} for group {group_id}")
        return conversation_id

    async def _create_conversation(
        self, port: int, agent_config: dict, workspace_dir: str | Path
    ) -> UUID:
        """Create conversation via API and return its UUID."""
        url = f"http://127.0.0.1:{port}/api/conversations"
        payload = {
            "agent": agent_config,
            "workspace": {"working_dir": str(workspace_dir)},
        }
        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()
        return UUID(response.json()["id"])

    async def send_message(self, group_id: str, text: str) -> None:
        """Send a message to the conversation for this group."""
        server = self._servers.get(group_id)
        if not server or not server.conversation_id:
            raise ValueError(f"No active conversation for group {group_id}")

        url = f"http://127.0.0.1:{server.port}/api/conversations/{server.conversation_id}/messages"
        payload = {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        }

        response = await self._http_client.post(url, json=payload)
        response.raise_for_status()

    async def run_conversation(self, group_id: str) -> None:
        """Trigger the conversation to run (process pending messages)."""
        server = self._servers.get(group_id)
        if not server or not server.conversation_id:
            raise ValueError(f"No active conversation for group {group_id}")

        url = f"http://127.0.0.1:{server.port}/api/conversations/{server.conversation_id}/run"
        response = await self._http_client.post(url)
        response.raise_for_status()

    async def pause_conversation(self, group_id: str) -> bool:
        """Pause the conversation for this group."""
        server = self._servers.get(group_id)
        if not server or not server.conversation_id:
            return False

        try:
            url = f"http://127.0.0.1:{server.port}/api/conversations/{server.conversation_id}/pause"
            response = await self._http_client.post(url)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Failed to pause conversation for {group_id}: {e}")
            return False

    async def get_conversation_status(self, group_id: str) -> str | None:
        """Get the execution status of a conversation."""
        server = self._servers.get(group_id)
        if not server or not server.conversation_id:
            return None

        try:
            url = f"http://127.0.0.1:{server.port}/api/conversations/{server.conversation_id}"
            response = await self._http_client.get(url)
            if response.status_code == 200:
                return response.json().get("execution_status")
        except Exception:
            pass
        return None

    async def _pause_all_conversations(self) -> None:
        """Pause all active conversations (best effort, non-blocking)."""
        tasks = []
        for group_id in self._servers:
            tasks.append(self.pause_conversation(group_id))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            paused = sum(1 for r in results if r is True)
            logger.info(f"Paused {paused}/{len(tasks)} conversations")

    # =========================================================================
    # Registry Persistence
    # =========================================================================

    async def _save_registry(self) -> None:
        """Save server registry to disk."""
        data = {
            "servers": {
                group_id: server.to_dict() for group_id, server in self._servers.items()
            },
            "next_port": self._next_port,
        }

        # Write atomically
        tmp_path = self.registry_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        tmp_path.rename(self.registry_path)

    async def _load_registry(self) -> None:
        """Load server registry from disk."""
        if not self.registry_path.exists():
            return

        try:
            data = json.loads(self.registry_path.read_text())
            self._servers = {
                group_id: ServerInfo.from_dict(server_data)
                for group_id, server_data in data.get("servers", {}).items()
            }
            self._next_port = data.get("next_port", self.port_start)
        except Exception as e:
            logger.warning(f"Failed to load server registry: {e}")
            self._servers = {}

    async def _reconcile_servers(self) -> None:
        """Check registered servers and remove dead ones."""
        dead_groups = []

        for group_id, server in self._servers.items():
            if not await self._is_server_healthy(server):
                logger.info(
                    f"Server for {group_id} (PID {server.pid}) is no longer running"
                )
                dead_groups.append(group_id)

        for group_id in dead_groups:
            del self._servers[group_id]

        if dead_groups:
            await self._save_registry()

    # =========================================================================
    # Server Termination (for cleanup)
    # =========================================================================

    async def terminate_server(self, group_id: str, force: bool = False) -> bool:
        """Terminate a server process.

        Args:
            group_id: The group whose server to terminate
            force: If True, use SIGKILL; otherwise SIGTERM
        """
        server = self._servers.get(group_id)
        if not server:
            return False

        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            os.kill(server.pid, sig)
            del self._servers[group_id]
            await self._save_registry()
            logger.info(f"Terminated server for {group_id} (PID {server.pid})")
            return True
        except OSError as e:
            logger.warning(f"Failed to terminate server for {group_id}: {e}")
            return False

    async def terminate_all_servers(self, force: bool = False) -> None:
        """Terminate all managed servers."""
        for group_id in list(self._servers.keys()):
            await self.terminate_server(group_id, force=force)


# =============================================================================
# Integration Notes
# =============================================================================
#
# To integrate with OpenPaws, the ConversationRunner would change from:
#
#   Current (LocalConversation, in-process):
#   ----------------------------------------
#   conversation = Conversation(
#       agent=self.agent,
#       workspace=workspace_dir,
#       persistence_dir=persistence_dir,
#   )
#   conversation.send_message(prompt)
#   conversation.run()  # Blocks until done
#   conversation.close()
#
#   New (RemoteConversation, via agent-server):
#   -------------------------------------------
#   from openhands.sdk import RemoteConversation
#
#   # Get or create server for this group
#   server_url = await self.server_manager.get_or_create_server(group_id)
#
#   # Start conversation on server (or resume existing)
#   conv_id = await self.server_manager.start_conversation(
#       group_id, agent_config, workspace_dir
#   )
#
#   # Use RemoteConversation client
#   conversation = RemoteConversation(
#       host=server_url,
#       conversation_id=conv_id,
#       callbacks=[...],
#   )
#   conversation.send_message(prompt)
#   conversation.run()  # Non-blocking, returns when agent is processing
#
#   # Wait for completion via WebSocket events or polling
#   await wait_for_completion(conversation)
#
# The key differences:
# 1. Conversations run in separate processes (agent-servers)
# 2. Daemon can exit without killing conversations
# 3. Conversations persist and can be resumed after daemon restart
# 4. Need to handle async events via WebSocket
#
# Configuration changes needed:
# - Add `use_remote_servers: bool` to config
# - Add `server_port_range: tuple[int, int]` to config
#
# Daemon changes needed:
# - Initialize AgentServerManager in daemon startup
# - Call manager.shutdown() in daemon shutdown (before exit)
# - Pass manager to ConversationRunner
#
# This is a significant but straightforward refactor. The agent-server
# already handles all the hard parts (persistence, pause/resume, API).
# =============================================================================
