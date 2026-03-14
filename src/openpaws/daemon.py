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

from openpaws.config import Config, load_config
from openpaws.scheduler import Scheduler

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
    (3600, "h"),   # hours
    (60, "m"),     # minutes
    (1, "s"),      # seconds
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
        self.state: DaemonState | None = None
        self._shutdown_event: asyncio.Event | None = None

    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        loop = asyncio.get_running_loop()

        def signal_handler(signum):
            logger.info(f"Received signal {signum}, shutting down...")
            if self._shutdown_event:
                self._shutdown_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: signal_handler(s))

    async def _execute_task(self, task) -> str:
        """Execute a scheduled task."""
        logger.info(f"Executing task: {task.config.name}")
        # TODO: Integrate with software-agent-sdk conversation
        # For now, just log and return
        return f"Task '{task.config.name}' executed at {datetime.now()}"

    def _load_config(self) -> None:
        """Load configuration from file."""
        try:
            self.config = load_config(self.config_path)
            logger.info("Configuration loaded successfully")
        except FileNotFoundError as e:
            logger.warning(f"Config file not found: {e}. Running with defaults.")
            self.config = Config()

    def _setup_scheduler(self) -> None:
        """Initialize scheduler with configured tasks."""
        self.scheduler = Scheduler()
        for task_config in self.config.tasks.values():
            self.scheduler.add_task(task_config)
            logger.info(f"Scheduled task: {task_config.name}")

    async def run(self) -> None:
        """Main daemon run loop."""
        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()
        self.state = DaemonState(
            started_at=datetime.now(), config_path=self.config_path
        )

        self._load_config()
        self._setup_scheduler()

        logger.info(f"OpenPaws daemon started with PID {os.getpid()}")
        logger.info(f"Loaded {len(self.config.tasks)} tasks")

        self.scheduler.start(self._execute_task)
        await self._shutdown_event.wait()

        if self.scheduler:
            self.scheduler.stop()
        logger.info("OpenPaws daemon stopped")

    def _daemonize_and_check(self) -> int | None:
        """Fork into background. Returns exit code for parent, None for child."""
        child_pid = daemonize()
        if child_pid > 0:
            # Parent: wait for child to start
            time.sleep(0.5)
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
        time.sleep(0.5)
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
    def stop(timeout: int = 10) -> int:
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
