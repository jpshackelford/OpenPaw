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

    def test_allocate_port_skips_unavailable(self, tmp_path: Path):
        """Port allocation skips unavailable ports."""
        import socket

        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18000, port_end=18010
        )

        # Bind to port 18000 so it's unavailable
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 18000))
            s.listen(1)

            port = manager._allocate_port()
            # Should skip 18000 and return 18001
            assert port == 18001

    def test_allocate_port_raises_when_exhausted(self, tmp_path: Path):
        """Port allocation raises when all ports unavailable."""
        import socket

        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18050, port_end=18052
        )

        # Bind to all ports in range
        sockets = []
        for port in range(18050, 18053):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", port))
            s.listen(1)
            sockets.append(s)

        try:
            with pytest.raises(RuntimeError, match="No available ports"):
                manager._allocate_port()
        finally:
            for s in sockets:
                s.close()

    def test_is_port_available_returns_true_for_free_port(self, tmp_path: Path):
        """_is_port_available returns True for unbound port."""
        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18060, port_end=18070
        )
        assert manager._is_port_available(18060) is True

    def test_is_port_available_returns_false_for_bound_port(self, tmp_path: Path):
        """_is_port_available returns False for bound port."""
        import socket

        manager = AgentServerManager(
            base_dir=tmp_path, port_start=18060, port_end=18070
        )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 18061))
            s.listen(1)
            assert manager._is_port_available(18061) is False


class TestRegistryPersistence:
    """Tests for registry save/load."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        return AgentServerManager(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_save_registry_creates_file(self, manager: AgentServerManager):
        """Saving registry creates JSON file."""
        manager._servers = {"main": ServerInfo(pid=1234, port=18000, group_id="main")}

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
        manager._servers = {"main": ServerInfo(pid=1234, port=18000, group_id="main")}

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


class TestGetOrCreateServer:
    """Tests for get_or_create_server."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_returns_existing_healthy_server(self, manager: AgentServerManager):
        """Returns existing server if healthy."""
        import os

        existing = ServerInfo(pid=os.getpid(), port=18000, group_id="main")
        manager._servers = {"main": existing}
        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.get.return_value = mock_response

        result = await manager.get_or_create_server("main")

        assert result == existing

    @pytest.mark.asyncio
    async def test_respawns_unhealthy_server(self, manager: AgentServerManager):
        """Respawns server if existing one is unhealthy."""
        existing = ServerInfo(pid=99999999, port=18000, group_id="main")
        manager._servers = {"main": existing}

        with patch.object(manager, "_spawn_server") as mock_spawn:
            new_server = ServerInfo(pid=12345, port=18001, group_id="main")
            mock_spawn.return_value = new_server
            result = await manager.get_or_create_server("main")

        assert result == new_server
        mock_spawn.assert_called_once_with("main")


class TestSpawnServer:
    """Tests for _spawn_server."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_spawn_server_returns_server_info(self, manager: AgentServerManager):
        """_spawn_server returns ServerInfo with correct data."""
        mock_process = MagicMock()
        mock_process.pid = 54321

        with (
            patch.object(manager, "_start_server_process", return_value=mock_process),
            patch.object(manager, "_wait_for_server_ready"),
        ):
            result = await manager._spawn_server("test-group")

        assert result.pid == 54321
        assert result.port == manager.port_start
        assert result.group_id == "test-group"


class TestStartServerProcess:
    """Tests for _start_server_process."""

    def test_returns_popen_object(self, tmp_path: Path):
        """_start_server_process returns Popen object."""
        manager = AgentServerManager(base_dir=tmp_path)
        mock_popen = MagicMock()

        with patch(
            "openpaws.agent_server_manager.subprocess.Popen", return_value=mock_popen
        ) as mock_cls:
            result = manager._start_server_process(18000)

        assert result == mock_popen
        mock_cls.assert_called_once()

    def test_creates_log_directory(self, tmp_path: Path):
        """_start_server_process creates logs/servers directory."""
        manager = AgentServerManager(base_dir=tmp_path)
        mock_popen = MagicMock()

        with patch(
            "openpaws.agent_server_manager.subprocess.Popen", return_value=mock_popen
        ):
            manager._start_server_process(18000)

        log_dir = tmp_path / "logs" / "servers"
        assert log_dir.exists()

    def test_redirects_output_to_log_file(self, tmp_path: Path):
        """_start_server_process redirects stdout/stderr to log file."""
        import subprocess

        manager = AgentServerManager(base_dir=tmp_path)

        with patch("openpaws.agent_server_manager.subprocess.Popen") as mock_cls:
            manager._start_server_process(18000)

        # Check that Popen was called with file handle for stdout
        call_kwargs = mock_cls.call_args[1]
        assert call_kwargs["stderr"] == subprocess.STDOUT
        # stdout should be a file handle (not DEVNULL)
        assert call_kwargs["stdout"] is not subprocess.DEVNULL


class TestWaitForServerReady:
    """Tests for _wait_for_server_ready."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_returns_when_server_responds(self, manager: AgentServerManager):
        """Returns when health check succeeds."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.get.return_value = mock_response

        # Should not raise
        await manager._wait_for_server_ready(18000, timeout=1.0)

        manager._http_client.get.assert_called()

    @pytest.mark.asyncio
    async def test_raises_timeout_when_server_unresponsive(
        self, manager: AgentServerManager
    ):
        """Raises TimeoutError if server never responds."""
        manager._http_client.get.side_effect = httpx.ConnectError("refused")

        with pytest.raises(TimeoutError):
            await manager._wait_for_server_ready(18000, timeout=0.1, interval=0.05)


class TestStartConversation:
    """Tests for start_conversation."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_start_conversation_returns_uuid(self, manager: AgentServerManager):
        """start_conversation returns conversation UUID."""
        server = ServerInfo(pid=1234, port=18000, group_id="main")
        manager._servers = {"main": server}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"}
        mock_response.raise_for_status = MagicMock()
        manager._http_client.post.return_value = mock_response

        with patch.object(manager, "get_or_create_server", return_value=server):
            result = await manager.start_conversation(
                "main", {"model": "test"}, "/workspace"
            )

        assert result == UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        assert server.conversation_id == result


class TestSendMessage:
    """Tests for send_message."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_send_message_raises_without_conversation(
        self, manager: AgentServerManager
    ):
        """send_message raises if no active conversation."""
        with pytest.raises(ValueError, match="No active conversation"):
            await manager.send_message("nonexistent", "hello")

    @pytest.mark.asyncio
    async def test_send_message_posts_to_api(self, manager: AgentServerManager):
        """send_message POSTs message to API."""
        conv_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        server = ServerInfo(
            pid=1234, port=18000, conversation_id=conv_id, group_id="main"
        )
        manager._servers = {"main": server}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        manager._http_client.post.return_value = mock_response

        await manager.send_message("main", "hello world")

        manager._http_client.post.assert_called_once()
        call_url = manager._http_client.post.call_args[0][0]
        assert f"/api/conversations/{conv_id}/messages" in call_url


class TestRunConversation:
    """Tests for run_conversation."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_run_raises_without_conversation(self, manager: AgentServerManager):
        """run_conversation raises if no active conversation."""
        with pytest.raises(ValueError, match="No active conversation"):
            await manager.run_conversation("nonexistent")

    @pytest.mark.asyncio
    async def test_run_posts_to_api(self, manager: AgentServerManager):
        """run_conversation POSTs to run endpoint."""
        conv_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        server = ServerInfo(
            pid=1234, port=18000, conversation_id=conv_id, group_id="main"
        )
        manager._servers = {"main": server}

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        manager._http_client.post.return_value = mock_response

        await manager.run_conversation("main")

        manager._http_client.post.assert_called_once()
        call_url = manager._http_client.post.call_args[0][0]
        assert f"/api/conversations/{conv_id}/run" in call_url


class TestGetConversationStatus:
    """Tests for get_conversation_status."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_returns_none_without_server(self, manager: AgentServerManager):
        """Returns None if no server for group."""
        result = await manager.get_conversation_status("nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_status_from_api(self, manager: AgentServerManager):
        """Returns execution_status from API response."""
        conv_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        server = ServerInfo(
            pid=1234, port=18000, conversation_id=conv_id, group_id="main"
        )
        manager._servers = {"main": server}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"execution_status": "running"}
        manager._http_client.get.return_value = mock_response

        result = await manager.get_conversation_status("main")

        assert result == "running"

    @pytest.mark.asyncio
    async def test_returns_none_on_error(self, manager: AgentServerManager):
        """Returns None on API error."""
        conv_id = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        server = ServerInfo(
            pid=1234, port=18000, conversation_id=conv_id, group_id="main"
        )
        manager._servers = {"main": server}

        manager._http_client.get.side_effect = httpx.ConnectError("refused")

        result = await manager.get_conversation_status("main")

        assert result is None


class TestPauseAllConversations:
    """Tests for _pause_all_conversations."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        mgr = AgentServerManager(base_dir=tmp_path)
        mgr._http_client = AsyncMock(spec=httpx.AsyncClient)
        return mgr

    @pytest.mark.asyncio
    async def test_pauses_multiple_conversations(self, manager: AgentServerManager):
        """Pauses all active conversations."""
        conv_id1 = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        conv_id2 = UUID("11111111-2222-3333-4444-555555555555")
        manager._servers = {
            "group1": ServerInfo(
                pid=1234, port=18000, conversation_id=conv_id1, group_id="group1"
            ),
            "group2": ServerInfo(
                pid=5678, port=18001, conversation_id=conv_id2, group_id="group2"
            ),
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        manager._http_client.post.return_value = mock_response

        await manager._pause_all_conversations()

        assert manager._http_client.post.call_count == 2


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

    @pytest.mark.asyncio
    async def test_terminate_with_force_uses_sigkill(self, manager: AgentServerManager):
        """Termination with force=True uses SIGKILL."""
        import os
        import signal

        manager._servers = {
            "main": ServerInfo(pid=os.getpid(), port=18000, group_id="main")
        }

        with patch("openpaws.agent_server_manager.os.kill") as mock_kill:
            await manager.terminate_server("main", force=True)
            mock_kill.assert_called_with(os.getpid(), signal.SIGKILL)

    @pytest.mark.asyncio
    async def test_terminate_handles_oserror(self, manager: AgentServerManager):
        """Termination handles OSError gracefully."""
        manager._servers = {
            "main": ServerInfo(pid=99999999, port=18000, group_id="main")
        }

        with patch(
            "openpaws.agent_server_manager.os.kill",
            side_effect=OSError("No such process"),
        ):
            result = await manager.terminate_server("main")

        assert result is False


class TestTerminateAllServers:
    """Tests for terminate_all_servers."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> AgentServerManager:
        return AgentServerManager(base_dir=tmp_path)

    @pytest.mark.asyncio
    async def test_terminates_all_servers(self, manager: AgentServerManager):
        """Terminates all managed servers."""
        import os

        manager._servers = {
            "group1": ServerInfo(pid=os.getpid(), port=18000, group_id="group1"),
            "group2": ServerInfo(pid=os.getpid(), port=18001, group_id="group2"),
        }

        with patch("openpaws.agent_server_manager.os.kill"):
            await manager.terminate_all_servers()

        assert len(manager._servers) == 0
