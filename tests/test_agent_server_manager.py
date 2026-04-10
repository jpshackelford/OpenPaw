"""Tests for AgentServerManager."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import httpx
import pytest

from openpaws.agent_server_manager import (
    DEFAULT_PORT_START,
    REGISTRY_FILENAME,
    AgentServerManager,
    ServerInfo,
)


class TestServerInfo:
    """Tests for ServerInfo dataclass."""

    def test_to_dict_without_conversation(self):
        """ServerInfo serializes correctly without conversation ID."""
        info = ServerInfo(pid=1234, port=18000, group_id="main")
        result = info.to_dict()

        assert result == {
            "pid": 1234,
            "port": 18000,
            "conversation_id": None,
            "group_id": "main",
        }

    def test_to_dict_with_conversation(self):
        """ServerInfo serializes correctly with conversation ID."""
        conv_id = UUID("12345678-1234-5678-1234-567812345678")
        info = ServerInfo(
            pid=1234, port=18000, conversation_id=conv_id, group_id="main"
        )
        result = info.to_dict()

        assert result["conversation_id"] == str(conv_id)
        assert result["pid"] == 1234

    def test_from_dict_without_conversation(self):
        """ServerInfo deserializes correctly without conversation ID."""
        data = {
            "pid": 1234,
            "port": 18000,
            "conversation_id": None,
            "group_id": "main",
        }
        info = ServerInfo.from_dict(data)

        assert info.pid == 1234
        assert info.port == 18000
        assert info.conversation_id is None
        assert info.group_id == "main"

    def test_from_dict_with_conversation(self):
        """ServerInfo deserializes correctly with conversation ID."""
        conv_id = "12345678-1234-5678-1234-567812345678"
        data = {
            "pid": 1234,
            "port": 18000,
            "conversation_id": conv_id,
            "group_id": "main",
        }
        info = ServerInfo.from_dict(data)

        assert info.conversation_id == UUID(conv_id)

    def test_roundtrip(self):
        """ServerInfo survives serialization roundtrip."""
        original = ServerInfo(
            pid=9999,
            port=18050,
            conversation_id=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
            group_id="test-group",
        )
        data = original.to_dict()
        restored = ServerInfo.from_dict(data)

        assert restored.pid == original.pid
        assert restored.port == original.port
        assert restored.conversation_id == original.conversation_id
        assert restored.group_id == original.group_id


class TestAgentServerManagerInit:
    """Tests for AgentServerManager initialization."""

    def test_init_creates_base_dir(self, tmp_path: Path):
        """Manager creates base directory on init."""
        base_dir = tmp_path / "agent_servers"
        assert not base_dir.exists()

        manager = AgentServerManager(base_dir=base_dir)

        assert base_dir.exists()
        assert manager._next_port == DEFAULT_PORT_START

    def test_init_with_custom_ports(self, tmp_path: Path):
        """Manager respects custom port range."""
        manager = AgentServerManager(
            base_dir=tmp_path, port_start=19000, port_end=19100
        )

        assert manager.port_start == 19000
        assert manager.port_end == 19100
        assert manager._next_port == 19000

    def test_registry_path(self, tmp_path: Path):
        """Registry path is under base_dir."""
        manager = AgentServerManager(base_dir=tmp_path)

        assert manager.registry_path == tmp_path / REGISTRY_FILENAME

    def test_conversations_dir(self, tmp_path: Path):
        """Conversations directory is under base_dir."""
        manager = AgentServerManager(base_dir=tmp_path)

        assert manager.conversations_dir == tmp_path / "conversations"


class TestPortAllocation:
    """Tests for port allocation."""

    def test_allocate_port_sequential(self, tmp_path: Path):
        """Ports are allocated sequentially."""
        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18000, port_end=18010
        )

        port1 = manager._allocate_port()
        port2 = manager._allocate_port()
        port3 = manager._allocate_port()

        assert port1 == 18000
        assert port2 == 18001
        assert port3 == 18002

    def test_allocate_port_wraps_around(self, tmp_path: Path):
        """Port allocation wraps around at range end."""
        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18000, port_end=18002
        )

        port1 = manager._allocate_port()  # 18000
        port2 = manager._allocate_port()  # 18001
        port3 = manager._allocate_port()  # 18002
        port4 = manager._allocate_port()  # wraps to 18000

        assert port1 == 18000
        assert port2 == 18001
        assert port3 == 18002
        assert port4 == 18000


class TestRegistryPersistence:
    """Tests for registry save/load."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        return AgentServerManager(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_save_registry_creates_file(self, manager: AgentServerManager):
        """Saving registry creates JSON file."""
        manager._servers = {
            "main": ServerInfo(pid=1234, port=18000, group_id="main")
        }

        await manager._save_registry()

        assert manager.registry_path.exists()
        data = json.loads(manager.registry_path.read_text())
        assert "servers" in data
        assert "main" in data["servers"]

    @pytest.mark.asyncio
    async def test_save_registry_atomic(
        self, manager: AgentServerManager, tmp_path: Path
    ):
        """Registry saves atomically (no .tmp file left)."""
        manager._servers = {
            "main": ServerInfo(pid=1234, port=18000, group_id="main")
        }

        await manager._save_registry()

        tmp_file = manager.registry_path.with_suffix(".tmp")
        assert not tmp_file.exists()

    @pytest.mark.asyncio
    async def test_load_registry_empty(self, manager: AgentServerManager):
        """Loading non-existent registry starts empty."""
        assert not manager.registry_path.exists()

        await manager._load_registry()

        assert manager._servers == {}

    @pytest.mark.asyncio
    async def test_load_registry_restores_state(self, manager: AgentServerManager):
        """Loading registry restores servers and port counter."""
        data = {
            "servers": {
                "main": {
                    "pid": 1234,
                    "port": 18005,
                    "conversation_id": None,
                    "group_id": "main",
                }
            },
            "next_port": 18006,
        }
        manager.registry_path.write_text(json.dumps(data))

        await manager._load_registry()

        assert "main" in manager._servers
        assert manager._servers["main"].pid == 1234
        assert manager._next_port == 18006

    @pytest.mark.asyncio
    async def test_load_registry_handles_corrupt_file(
        self, manager: AgentServerManager
    ):
        """Loading corrupt registry gracefully fails."""
        manager.registry_path.write_text("not valid json {{{")

        # Should not raise
        await manager._load_registry()

        assert manager._servers == {}


class TestServerHealthCheck:
    """Tests for server health checking."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_unhealthy_when_process_not_running(
        self, manager: AgentServerManager
    ):
        """Server is unhealthy if process is not running."""
        server = ServerInfo(pid=99999999, port=18000, group_id="main")

        # Process check will fail (invalid PID)
        result = await manager._is_server_healthy(server)

        assert result is False

    @pytest.mark.asyncio
    async def test_unhealthy_when_http_fails(self, manager: AgentServerManager):
        """Server is unhealthy if HTTP health check fails."""
        import os
        server = ServerInfo(pid=os.getpid(), port=18000, group_id="main")

        # HTTP check fails
        manager._http_client.get.side_effect = httpx.ConnectError("Connection refused")

        result = await manager._is_server_healthy(server)

        assert result is False

    @pytest.mark.asyncio
    async def test_healthy_when_all_checks_pass(self, manager: AgentServerManager):
        """Server is healthy if process runs and HTTP succeeds."""
        import os
        server = ServerInfo(pid=os.getpid(), port=18000, group_id="main")

        # HTTP check succeeds
        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.get.return_value = mock_response

        result = await manager._is_server_healthy(server)

        assert result is True
        manager._http_client.get.assert_called_with("http://127.0.0.1:18000/health")


class TestLifecycle:
    """Tests for manager startup and shutdown."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        return AgentServerManager(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_startup_creates_http_client(self, manager: AgentServerManager):
        """Startup initializes HTTP client."""
        assert manager._http_client is None

        await manager.startup()

        assert manager._http_client is not None
        assert isinstance(manager._http_client, httpx.AsyncClient)

        # Cleanup
        await manager.shutdown(pause_conversations=False)

    @pytest.mark.asyncio
    async def test_startup_creates_conversations_dir(self, manager: AgentServerManager):
        """Startup creates conversations directory."""
        assert not manager.conversations_dir.exists()

        await manager.startup()

        assert manager.conversations_dir.exists()

        # Cleanup
        await manager.shutdown(pause_conversations=False)

    @pytest.mark.asyncio
    async def test_shutdown_closes_http_client(self, manager: AgentServerManager):
        """Shutdown closes HTTP client."""
        await manager.startup()
        assert manager._http_client is not None  # Verify client exists

        await manager.shutdown(pause_conversations=False)

        assert manager._http_client is None

    @pytest.mark.asyncio
    async def test_shutdown_saves_registry(self, manager: AgentServerManager):
        """Shutdown saves registry."""
        await manager.startup()
        manager._servers = {"main": ServerInfo(pid=1234, port=18000, group_id="main")}

        await manager.shutdown(pause_conversations=False)

        assert manager.registry_path.exists()
        data = json.loads(manager.registry_path.read_text())
        assert "main" in data["servers"]


class TestConversationPause:
    """Tests for pausing conversations."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_pause_returns_false_when_no_server(
        self, manager: AgentServerManager
    ):
        """Pausing unknown group returns False."""
        result = await manager.pause_conversation("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_pause_returns_false_when_no_conversation(
        self, manager: AgentServerManager
    ):
        """Pausing group without active conversation returns False."""
        manager._servers = {"main": ServerInfo(pid=1234, port=18000, group_id="main")}

        result = await manager.pause_conversation("main")

        assert result is False

    @pytest.mark.asyncio
    async def test_pause_success(self, manager: AgentServerManager):
        """Successful pause returns True."""
        conv_id = UUID("12345678-1234-5678-1234-567812345678")
        manager._servers = {
            "main": ServerInfo(
                pid=1234, port=18000, conversation_id=conv_id, group_id="main"
            )
        }
        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.post.return_value = mock_response

        result = await manager.pause_conversation("main")

        assert result is True
        manager._http_client.post.assert_called_with(
            f"http://127.0.0.1:18000/api/conversations/{conv_id}/pause"
        )

    @pytest.mark.asyncio
    async def test_pause_handles_http_error(self, manager: AgentServerManager):
        """Pause handles HTTP errors gracefully."""
        conv_id = UUID("12345678-1234-5678-1234-567812345678")
        manager._servers = {
            "main": ServerInfo(
                pid=1234, port=18000, conversation_id=conv_id, group_id="main"
            )
        }
        manager._http_client.post.side_effect = httpx.ConnectError("Connection refused")

        result = await manager.pause_conversation("main")

        assert result is False


class TestServerUrl:
    """Tests for getting server URLs."""

    def test_get_server_url_returns_none_for_unknown(self, tmp_path: Path):
        """Unknown group returns None URL."""
        manager = AgentServerManager(base_dir=tmp_path)

        result = manager.get_server_url("nonexistent")

        assert result is None

    def test_get_server_url_returns_localhost(self, tmp_path: Path):
        """Known group returns localhost URL."""
        manager = AgentServerManager(base_dir=tmp_path)
        manager._servers = {"main": ServerInfo(pid=1234, port=18042, group_id="main")}

        result = manager.get_server_url("main")

        assert result == "http://127.0.0.1:18042"


class TestReconcileServers:
    """Tests for reconciling server registry with running processes."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_removes_dead_servers(self, manager: AgentServerManager):
        """Reconcile removes servers that are no longer running."""
        # Add a server with invalid PID
        manager._servers = {
            "dead": ServerInfo(pid=99999999, port=18000, group_id="dead")
        }

        await manager._reconcile_servers()

        assert "dead" not in manager._servers

    @pytest.mark.asyncio
    async def test_keeps_healthy_servers(self, manager: AgentServerManager):
        """Reconcile keeps healthy servers."""
        import os

        # Add a server with current process PID (valid)
        manager._servers = {
            "alive": ServerInfo(pid=os.getpid(), port=18000, group_id="alive")
        }

        # Mock health check to succeed
        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.get.return_value = mock_response

        await manager._reconcile_servers()

        assert "alive" in manager._servers


class TestTerminateServer:
    """Tests for server termination."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        return AgentServerManager(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_terminate_unknown_server_returns_false(
        self, manager: AgentServerManager
    ):
        """Terminating unknown group returns False."""
        result = await manager.terminate_server("nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_terminate_removes_from_registry(self, manager: AgentServerManager):
        """Termination removes server from registry."""
        import os

        # Use current PID so we can signal it (mock prevents actual signal)
        manager._servers = {
            "main": ServerInfo(pid=os.getpid(), port=18000, group_id="main")
        }

        # Mock os.kill to avoid actually sending signal
        with patch("openpaws.agent_server_manager.os.kill"):
            result = await manager.terminate_server("main")

        assert result is True
        assert "main" not in manager._servers
