"""Daemon process management for OpenPaws.

Environment Variables
---------------------
OPENPAWS_DIR : str
    Base directory for OpenPaws files (default: ~/.openpaws).
    Controls location of PID file, logs, and default config.

OPENPAWS_PID_FILE : str
    Explicit path to PID file. Overrides OPENPAWS_DIR-based location.

OPENPAWS_LOG_FILE : str
    Explicit path to log file. Overrides OPENPAWS_DIR-based location.

These environment variables enable running multiple daemon instances
(e.g., for integration testing) without conflicts.
"""

import asyncio
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from openpaws.agent_server_manager import AgentServerManager
from openpaws.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from openpaws.channels.campfire import CampfireAdapter, CampfireConfig
from openpaws.channels.gmail import GmailAdapter, GmailConfig
from openpaws.channels.slack import SlackAdapter, SlackConfig
from openpaws.config import Config, load_config
from openpaws.queue_manager import QueueManager
from openpaws.runner import ConversationRunner
from openpaws.scheduler import Scheduler
from openpaws.storage import Storage

logger = logging.getLogger(__name__)

# Environment variable names
ENV_OPENPAWS_DIR = "OPENPAWS_DIR"
ENV_PID_FILE = "OPENPAWS_PID_FILE"
ENV_LOG_FILE = "OPENPAWS_LOG_FILE"

# Default values
DEFAULT_OPENPAWS_DIR = Path.home() / ".openpaws"
PID_FILE_NAME = "openpaws.pid"
LOG_DIR_NAME = "logs"
LOG_FILE_NAME = "openpaws.log"


@dataclass
class DaemonState:
    """Runtime state of the daemon."""

    started_at: datetime
    config_path: Path | None = None


def get_openpaws_dir() -> Path:
    """Get the OpenPaws directory, creating it if needed.

    Respects OPENPAWS_DIR environment variable if set.
    """
    env_dir = os.environ.get(ENV_OPENPAWS_DIR)
    if env_dir:
        openpaws_dir = Path(env_dir)
    else:
        openpaws_dir = DEFAULT_OPENPAWS_DIR
    openpaws_dir.mkdir(parents=True, exist_ok=True)
    return openpaws_dir


def get_pid_file() -> Path:
    """Get the path to the PID file.

    Respects OPENPAWS_PID_FILE environment variable if set,
    otherwise uses OPENPAWS_DIR/openpaws.pid.
    """
    env_pid_file = os.environ.get(ENV_PID_FILE)
    if env_pid_file:
        pid_file = Path(env_pid_file)
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        return pid_file
    return get_openpaws_dir() / PID_FILE_NAME


def get_log_file() -> Path:
    """Get the path to the log file.

    Respects OPENPAWS_LOG_FILE environment variable if set,
    otherwise uses OPENPAWS_DIR/logs/openpaws.log.
    """
    env_log_file = os.environ.get(ENV_LOG_FILE)
    if env_log_file:
        log_file = Path(env_log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        return log_file
    log_dir = get_openpaws_dir() / LOG_DIR_NAME
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / LOG_FILE_NAME


def write_pid_file(pid: int | None = None) -> Path:
    """Write the PID file."""
    pid_file = get_pid_file()
    pid = pid or os.getpid()
    pid_file.write_text(str(pid))
    logger.info(f"PID file written: {pid_file} (pid={pid})")
    return pid_file


def read_pid_file() -> int | None:
    """Read the PID from the PID file."""
    pid_file = get_pid_file()
    if not pid_file.exists():
        return None
    try:
        return int(pid_file.read_text().strip())
    except (ValueError, OSError):
        return None


def remove_pid_file() -> bool:
    """Remove the PID file."""
    pid_file = get_pid_file()
    if pid_file.exists():
        pid_file.unlink()
        logger.info(f"PID file removed: {pid_file}")
        return True
    return False


def _is_zombie_process(pid: int) -> bool:
    """Check if process is a zombie by reading /proc/[pid]/stat."""
    try:
        stat_file = Path(f"/proc/{pid}/stat")
        if not stat_file.exists():
            return False
        stat_content = stat_file.read_text()
        # State is after the command name in parens
        close_paren_idx = stat_content.rfind(")")
        if close_paren_idx > 0:
            state = stat_content[close_paren_idx + 2 : close_paren_idx + 3]
            return state == "Z"
    except OSError:
        pass
    return False


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is actually running (not zombie)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 checks existence
    except OSError:
        return False
    return not _is_zombie_process(pid)


def _get_process_start_ticks(pid: int) -> int | None:
    """Get process start time in clock ticks from /proc/[pid]/stat."""
    stat_file = Path(f"/proc/{pid}/stat")
    if not stat_file.exists():
        return None
    stat_content = stat_file.read_text()
    # Handle process names with spaces/parens by finding last ')'
    close_paren_idx = stat_content.rfind(")")
    fields = stat_content[close_paren_idx + 2 :].split()
    return int(fields[19])  # Field 22, adjusted for split offset


def _get_process_uptime_from_proc(pid: int) -> float | None:  # length-ok
    """Get process uptime by reading /proc filesystem."""
    try:
        uptime_file = Path("/proc/uptime")
        if not uptime_file.exists():
            return None

        start_ticks = _get_process_start_ticks(pid)
        if start_ticks is None:
            return None

        uptime_secs = float(uptime_file.read_text().split()[0])
        boot_time = time.time() - uptime_secs
        clock_ticks = os.sysconf("SC_CLK_TCK")
        start_time = boot_time + (start_ticks / clock_ticks)

        uptime = time.time() - start_time
        return uptime if uptime >= 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _get_uptime_from_pid_file() -> float | None:
    """Get uptime estimate from PID file modification time.

    Returns uptime in seconds, or None if unable to determine.
    """
    try:
        pid_file = get_pid_file()
        if pid_file.exists():
            uptime = time.time() - pid_file.stat().st_mtime
            return uptime if uptime >= 0 else None
    except OSError:
        pass
    return None


def _get_uptime(pid: int) -> float | None:
    """Get process uptime, trying /proc first then PID file mtime."""
    uptime = _get_process_uptime_from_proc(pid)
    if uptime is None:
        uptime = _get_uptime_from_pid_file()
    return uptime


def _add_uptime_to_status(status: dict, pid: int) -> None:
    """Add uptime fields to status dict if available."""
    uptime = _get_uptime(pid)
    if uptime is not None:
        status["uptime_seconds"] = int(uptime)
        status["uptime"] = format_uptime(uptime)


def get_daemon_status() -> dict:
    """Get the current daemon status."""
    pid = read_pid_file()
    running = pid is not None and is_process_running(pid)
    status = {
        "running": running,
        "pid": pid if running else None,
        "pid_file": str(get_pid_file()),
    }
    if running and pid:
        _add_uptime_to_status(status, pid)
    return status


_TIME_UNITS = [
    (86400, "d"),  # days
    (3600, "h"),  # hours
    (60, "m"),  # minutes
    (1, "s"),  # seconds
]


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable form (e.g., '2d 5h', '3h 45m')."""
    total = int(seconds)
    parts = []
    for divisor, suffix in _TIME_UNITS:
        if total >= divisor:
            value = total // divisor
            parts.append(f"{value}{suffix}")
            total %= divisor
        if len(parts) == 2:
            break
    return " ".join(parts) if parts else "0s"


def _create_file_handler() -> logging.Handler:
    """Create a file handler for logging."""
    handler = logging.FileHandler(get_log_file())
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    return handler


def _create_stream_handler() -> logging.Handler:
    """Create a stream handler for console logging."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    return handler


def setup_logging(log_to_file: bool = True, debug: bool = False) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if debug else logging.INFO
    handler = _create_file_handler() if log_to_file else _create_stream_handler()
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _redirect_stdio_to_devnull() -> None:
    """Redirect stdin, stdout, stderr to /dev/null."""
    sys.stdout.flush()
    sys.stderr.flush()
    with open(os.devnull) as devnull_in:
        os.dup2(devnull_in.fileno(), sys.stdin.fileno())
    with open(os.devnull, "a+") as devnull_out:
        os.dup2(devnull_out.fileno(), sys.stdout.fileno())
        os.dup2(devnull_out.fileno(), sys.stderr.fileno())


def daemonize() -> int:
    """Fork the process to run as a daemon. Returns child PID to parent, 0 to child."""
    pid = os.fork()
    if pid > 0:
        return pid

    # Child: create new session, detach from terminal
    os.setsid()
    os.chdir("/")
    _redirect_stdio_to_devnull()
    return 0


class Daemon:
    """OpenPaws daemon process."""

    def __init__(self, config_path: Path | str | None = None):
        self.config_path = Path(config_path) if config_path else None
        self.config: Config | None = None
        self.scheduler: Scheduler | None = None
        self.storage: Storage | None = None
        self.state: DaemonState | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._channel_adapters: list[ChannelAdapter] = []
        self._channel_adapters_by_type: dict[str, ChannelAdapter] = {}
        self._runner: ConversationRunner | None = None
        self._queue_manager: QueueManager | None = None
        self._heartbeat_task: asyncio.Task | None = None
        self._agent_server_manager: AgentServerManager | None = None

    @property
    def queue_manager(self) -> QueueManager | None:
        """Get the queue manager instance."""
        return self._queue_manager

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        def signal_handler(signum):
            logger.info(f"Received signal {signum}, shutting down...")
            if self._shutdown_event:
                self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    def _get_task_adapter(self, task) -> tuple | None:
        """Get the adapter and group for a task, or None if unavailable."""
        group = self.config.groups.get(task.config.group)
        if not group:
            logger.warning(f"Task '{task.config.name}' references unknown group")
            return None
        adapter = self._channel_adapters_by_type.get(group.channel)
        if not adapter or not adapter.is_running():
            # Error level because task result will be lost if adapter is down
            logger.error(
                f"Adapter '{group.channel}' not running, task result lost "
                f"for '{task.config.name}'"
            )
            return None
        return adapter, group

    async def _send_task_result_to_channel(self, task, message: str) -> None:
        """Send task result to the task's configured channel."""
        result = self._get_task_adapter(task)
        if not result:
            return
        adapter, group = result
        try:
            outgoing = OutgoingMessage(channel_id=group.chat_id, text=message)
            await adapter.send_message(outgoing)
            name = task.config.name
            logger.info(f"Task '{name}' sent to {group.channel}:{group.chat_id}")
        except Exception as e:
            logger.error(f"Failed to send task result to channel: {e}")

    async def _execute_task(self, task) -> str:
        """Execute a scheduled task and send result to configured channel."""
        logger.info(f"Executing task: {task.config.name}")
        if self._runner:
            result = await self._runner.run_task(task)
            if result.success and result.message:
                await self._send_task_result_to_channel(task, result.message)
            return result.message
        return f"Task '{task.config.name}' executed at {datetime.now()}"

    def _load_config(self) -> None:
        """Load configuration from file."""
        try:
            self.config = load_config(self.config_path)
            logger.info("Configuration loaded successfully")
        except FileNotFoundError as e:
            logger.warning(f"Config file not found: {e}. Running with defaults.")
            self.config = Config()

    def _setup_storage(self) -> None:
        """Initialize SQLite storage for state persistence."""
        self.storage = Storage()
        logger.info(f"Storage initialized: {self.storage.db_path}")

    def _setup_agent_server_manager(self) -> None:
        """Initialize agent server manager if remote servers are enabled."""
        if not self.config.remote_servers.enabled:
            logger.info("Remote servers disabled, using in-process conversations")
            return

        base_dir = get_openpaws_dir() / "agent_servers"
        self._agent_server_manager = AgentServerManager(
            base_dir=base_dir,
            port_start=self.config.remote_servers.port_start,
            port_end=self.config.remote_servers.port_end,
        )
        port_range = self.config.remote_servers
        logger.info(
            f"AgentServerManager configured (ports {port_range.port_start}"
            f"-{port_range.port_end})"
        )

    def _setup_runner(self) -> None:
        """Initialize the conversation runner."""
        # Queue callback is set up later in _setup_queue_manager
        self._runner = ConversationRunner(
            self.config,
            server_manager=self._agent_server_manager,
        )
        logger.info("Conversation runner initialized")

    def _setup_scheduler(self) -> None:
        """Initialize scheduler with configured tasks."""
        self.scheduler = Scheduler(storage=self.storage)
        for task_config in self.config.tasks.values():
            if not task_config.enabled:
                logger.info(f"Skipping disabled task: {task_config.name}")
                continue
            self.scheduler.add_task(task_config)
            logger.info(f"Scheduled task: {task_config.name}")

    async def _queue_callback(
        self,
        prompt: str,
        group_name: str,
        context: dict | None,
        priority: int,
        workflow_id: str | None,
    ) -> str:
        """Queue a follow-up conversation. Used as callback for QueueNextTool."""
        if not self._queue_manager:
            raise RuntimeError("Queue manager not initialized")
        return await self._queue_manager.enqueue(
            prompt=prompt,
            group_name=group_name,
            context=context,
            priority=priority,
            workflow_id=workflow_id,
        )

    def _setup_queue_manager(self) -> None:  # length-ok
        """Initialize queue manager for multi-conversation orchestration."""
        if not self.config.queue.enabled:
            logger.info("Queue dispatch disabled in config")
            return
        self._runner.set_queue_callback(self._queue_callback)
        self._queue_manager = QueueManager(
            storage=self.storage,
            runner=self._runner,
            config=self.config.queue,
        )
        interval = self.config.queue.heartbeat_interval
        max_d = self.config.queue.max_dispatch
        logger.info(f"Queue manager: interval={interval}s, max_dispatch={max_d}")

    async def _process_queue_batch(self) -> None:  # length-ok
        """Process one batch and log results."""
        if self._shutdown_event and self._shutdown_event.is_set():
            return
        processed = await self._queue_manager.process_batch()
        if processed > 0:
            logger.info(f"Heartbeat: processed {processed} queued item(s)")

    async def _heartbeat_loop(self) -> None:  # length-ok
        """Process queued conversations at configured interval."""
        if not self._queue_manager:
            return
        interval = self.config.queue.heartbeat_interval
        logger.info(f"Starting heartbeat loop with {interval}s interval")
        while True:
            try:
                await self._process_queue_batch()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info("Heartbeat loop cancelled")
                break
            except Exception as e:
                logger.exception(f"Error in heartbeat loop: {e}")

    def _find_group_for_message(self, message: IncomingMessage) -> str | None:
        """Find the group name that matches the incoming message."""
        for name, group in self.config.groups.items():
            if (
                group.channel == message.channel_type
                and group.chat_id == message.channel_id
            ):
                return name
        return None

    async def _signal_processing_start(self, message: IncomingMessage) -> None:
        """Signal that we're starting to process (e.g., add 👀 reaction)."""
        if not message.on_processing_start:
            logger.debug("No on_processing_start callback set")
            return
        logger.debug("Calling on_processing_start callback")
        try:
            await message.on_processing_start()
            logger.debug("on_processing_start callback completed")
        except Exception as e:
            logger.warning(f"Failed to signal processing start: {e}")

    async def _run_message_conversation(
        self, group_name: str, message: IncomingMessage
    ) -> str | None:
        """Run the conversation and return the response."""
        try:
            result = await self._runner.run_message(
                group_name=group_name,
                message=message.text,
                sender=message.user_name,
                send_callback=message.send_status,
            )
            if result.success:
                logger.info(f"Response generated for {group_name}")
                return result.message
            logger.error(f"Conversation failed: {result.error}")
            return f"Sorry, I encountered an error: {result.error}"
        except Exception as e:
            logger.exception(f"Error handling message: {e}")
            return f"Sorry, something went wrong: {e}"

    async def _handle_message(self, message: IncomingMessage) -> str | None:
        """Handle incoming messages from channel adapters."""
        logger.info(
            f"Message from {message.channel_type}:{message.channel_id} "
            f"user={message.user_id}: {message.text[:50]}..."
        )

        if not self._runner:
            logger.error("Conversation runner not initialized")
            return "Error: Bot not fully initialized"

        group_name = self._find_group_for_message(message)
        if not group_name:
            logger.warning(
                f"No group configured for {message.channel_type}:{message.channel_id}"
            )
            return None

        await self._signal_processing_start(message)
        return await self._run_message_conversation(group_name, message)

    def _create_slack_adapter(self, channel_config) -> SlackAdapter | None:
        """Create a Slack adapter from channel config."""
        app_token = channel_config.app_token
        bot_token = channel_config.bot_token

        if not app_token or not bot_token:
            logger.warning("Slack channel missing app_token or bot_token, skipping")
            return None

        try:
            slack_config = SlackConfig(app_token=app_token, bot_token=bot_token)
            return SlackAdapter(slack_config)
        except ValueError as e:
            logger.error(f"Invalid Slack config: {e}")
            return None

    def _build_gmail_config(self, channel_config) -> GmailConfig:
        """Build GmailConfig from channel config."""
        return GmailConfig(
            credentials_file=channel_config.credentials_file,
            token_file=channel_config.token_file,
            mode=channel_config.mode or "channel",
            poll_interval=channel_config.poll_interval,
            filter_label=channel_config.filter_label,
        )

    def _create_gmail_adapter(self, channel_config) -> GmailAdapter | None:
        """Create a Gmail adapter from channel config."""
        if not channel_config.credentials_file:
            logger.warning("Gmail channel missing credentials_file, skipping")
            return None
        try:
            return GmailAdapter(self._build_gmail_config(channel_config))
        except ValueError as e:
            logger.error(f"Invalid Gmail config: {e}")
            return None

    def _validate_campfire_config(self, cfg) -> bool:
        """Validate campfire channel config has required fields."""
        if not cfg.base_url:
            logger.warning("Campfire channel missing base_url, skipping")
            return False
        if not cfg.bot_key:
            logger.warning("Campfire channel missing bot_key, skipping")
            return False
        return True

    def _build_campfire_config(self, channel_config) -> CampfireConfig:
        """Build CampfireConfig from channel config."""
        return CampfireConfig(
            base_url=channel_config.base_url,
            bot_key=channel_config.bot_key,
            webhook_port=channel_config.webhook_port,
            webhook_path=channel_config.webhook_path,
            webhook_host=channel_config.webhook_host,
            context_messages=channel_config.context_messages,
        )

    def _create_campfire_adapter(self, channel_config) -> CampfireAdapter | None:
        """Create a Campfire adapter from channel config."""
        if not self._validate_campfire_config(channel_config):
            return None
        try:
            return CampfireAdapter(self._build_campfire_config(channel_config))
        except ValueError as e:
            logger.error(f"Invalid Campfire config: {e}")
            return None

    def _register_adapter(self, adapter: ChannelAdapter, name: str) -> None:
        """Register an adapter in both the list and type lookup dict."""
        self._channel_adapters.append(adapter)
        self._channel_adapters_by_type[adapter.channel_type] = adapter
        logger.info(f"Configured {adapter.channel_type} channel: {name}")

    def _create_adapter_for_type(self, channel_config):
        """Create an adapter based on channel type."""
        factories = {
            "slack": self._create_slack_adapter,
            "gmail": self._create_gmail_adapter,
            "campfire": self._create_campfire_adapter,
        }
        factory = factories.get(channel_config.type)
        return factory(channel_config) if factory else None

    def _setup_channel_adapters(self) -> None:
        """Initialize channel adapters from configuration."""
        self._channel_adapters = []
        self._channel_adapters_by_type = {}
        for name, cfg in self.config.channels.items():
            if adapter := self._create_adapter_for_type(cfg):
                self._register_adapter(adapter, name)

    async def _start_channel_adapters(self) -> None:
        """Start all configured channel adapters."""
        for adapter in self._channel_adapters:
            try:
                await adapter.start(self._handle_message)
                logger.info(f"Started {adapter.channel_type} adapter")
            except Exception as e:
                logger.error(f"Failed to start {adapter.channel_type} adapter: {e}")

    async def _stop_channel_adapters(self) -> None:
        """Stop all running channel adapters."""
        for adapter in self._channel_adapters:
            try:
                await adapter.stop()
                logger.info(f"Stopped {adapter.channel_type} adapter")
            except Exception as e:
                logger.error(f"Error stopping {adapter.channel_type} adapter: {e}")

    def _log_startup_info(self) -> None:
        """Log daemon startup information."""
        logger.info(f"OpenPaws daemon started with PID {os.getpid()}")
        logger.info(f"Loaded {len(self.config.tasks)} tasks")
        logger.info(f"Configured {len(self._channel_adapters)} channel adapters")

    async def _shutdown(self) -> None:
        """Perform graceful shutdown of all components."""
        self._stop_heartbeat()
        await self._stop_channel_adapters()
        if self.scheduler:
            self.scheduler.stop()
        # Shutdown agent server manager (pauses conversations, servers keep running)
        if self._agent_server_manager:
            try:
                await self._agent_server_manager.shutdown(pause_conversations=True)
            except Exception as e:
                logger.error(f"Error during AgentServerManager shutdown: {e}", exc_info=True)
        logger.info("OpenPaws daemon stopped")

    def _initialize(self) -> None:
        """Initialize daemon state and components."""
        self._shutdown_event = asyncio.Event()
        self.state = DaemonState(
            started_at=datetime.now(), config_path=self.config_path
        )
        self._load_config()
        self._setup_storage()
        self._setup_agent_server_manager()  # Must be before _setup_runner
        self._setup_runner()
        self._setup_scheduler()
        self._setup_queue_manager()
        self._setup_channel_adapters()

    def _start_heartbeat_if_enabled(self) -> None:
        """Start the heartbeat loop if queue manager is enabled."""
        if self._queue_manager:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            logger.info("Heartbeat task started")

    def _stop_heartbeat(self) -> None:
        """Stop the heartbeat loop if running."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            logger.info("Heartbeat task cancelled")

    async def _start_agent_server_manager(self) -> None:
        """Start the agent server manager and reconnect to existing servers."""
        if self._agent_server_manager:
            try:
                await self._agent_server_manager.startup()
                logger.info("AgentServerManager started")
            except Exception as e:
                logger.error(f"Failed to start AgentServerManager: {e}", exc_info=True)
                logger.warning("Continuing without remote servers (falling back to local mode)")
                self._agent_server_manager = None

    async def run(self) -> None:
        """Main daemon run loop."""
        self._initialize()
        self._setup_signal_handlers()
        self._log_startup_info()

        await self._start_agent_server_manager()
        self.scheduler.start(self._execute_task)
        self._start_heartbeat_if_enabled()
        await self._start_channel_adapters()
        await self._shutdown_event.wait()
        await self._shutdown()

    def _daemonize_and_check(self) -> int | None:
        """Fork into background. Returns exit code for parent, None for child."""
        child_pid = daemonize()
        if child_pid > 0:
            # Parent: wait briefly for child to start
            time.sleep(0.1)
            return 0 if is_process_running(child_pid) else 1
        # Child continues
        setup_logging(log_to_file=True)
        return None

    def start(self, foreground: bool = False) -> int:  # length-ok
        """Start the daemon."""
        existing_pid = read_pid_file()
        if existing_pid and is_process_running(existing_pid):
            logger.error(f"Daemon already running with PID {existing_pid}")
            return 1

        if not foreground:
            result = self._daemonize_and_check()
            if result is not None:
                return result
        else:
            setup_logging(log_to_file=False)

        write_pid_file()
        try:
            asyncio.run(self.run())
        finally:
            remove_pid_file()
        return 0

    @staticmethod
    def _wait_for_process_exit(pid: int, timeout: int) -> bool:
        """Wait for process to exit. Returns True if exited."""
        for _ in range(timeout * 10):
            if not is_process_running(pid):
                return True
            time.sleep(0.1)
        return False

    @staticmethod
    def _send_signal(pid: int, sig: signal.Signals) -> bool:
        """Send a signal to a process. Returns True on success."""
        try:
            os.kill(pid, sig)
            return True
        except OSError as e:
            logger.error(f"Failed to send signal: {e}")
            return False

    @staticmethod
    def _force_kill(pid: int) -> bool:
        """Send SIGKILL and wait briefly. Returns True if process died."""
        if not Daemon._send_signal(pid, signal.SIGKILL):
            return False
        time.sleep(0.1)
        return not is_process_running(pid)

    @staticmethod
    def _stop_running_process(pid: int, timeout: int) -> int:
        """Stop a running process with SIGTERM, then SIGKILL if needed."""
        logger.info(f"Sending SIGTERM to PID {pid}")
        if not Daemon._send_signal(pid, signal.SIGTERM):
            return 1

        if Daemon._wait_for_process_exit(pid, timeout):
            logger.info("Daemon stopped successfully")
            remove_pid_file()
            return 0

        logger.warning("Process did not exit gracefully, sending SIGKILL")
        if Daemon._force_kill(pid):
            logger.info("Daemon killed")
            remove_pid_file()
            return 0

        logger.error("Failed to stop daemon")
        return 1

    @staticmethod
    def stop(timeout: int = 5) -> int:
        """Stop the running daemon."""
        pid = read_pid_file()
        if pid is None:
            logger.info("No PID file found, daemon may not be running")
            return 0

        if not is_process_running(pid):
            logger.info("Process not running, cleaning up stale PID file")
            remove_pid_file()
            return 0

        return Daemon._stop_running_process(pid, timeout)
