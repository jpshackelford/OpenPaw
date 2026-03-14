"""Daemon process management for OpenPaws."""

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
    """Get the OpenPaws directory, creating it if needed."""
    openpaws_dir = DEFAULT_OPENPAWS_DIR
    openpaws_dir.mkdir(parents=True, exist_ok=True)
    return openpaws_dir


def get_pid_file() -> Path:
    """Get the path to the PID file."""
    return get_openpaws_dir() / PID_FILE_NAME


def get_log_file() -> Path:
    """Get the path to the log file."""
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


def is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is actually running (not zombie)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)  # Signal 0 just checks existence
    except OSError:
        return False

    # Check for zombie state on Linux
    try:
        stat_file = Path(f"/proc/{pid}/stat")
        if stat_file.exists():
            stat_content = stat_file.read_text()
            # State is after the command name in parens
            close_paren_idx = stat_content.rfind(")")
            if close_paren_idx > 0:
                state = stat_content[close_paren_idx + 2 : close_paren_idx + 3]
                if state == "Z":  # Zombie
                    return False
    except OSError:
        pass

    return True


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
        # Try to get process start time using PID file mtime as fallback
        uptime = None
        try:
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.exists():
                # Get system boot time
                uptime_file = Path("/proc/uptime")
                if uptime_file.exists():
                    uptime_secs = float(uptime_file.read_text().split()[0])
                    boot_time = time.time() - uptime_secs

                    # Get process start time (field 22 in /proc/pid/stat)
                    stat_content = stat_file.read_text()
                    # Handle process names with spaces/parens
                    close_paren_idx = stat_content.rfind(")")
                    fields = stat_content[close_paren_idx + 2 :].split()
                    start_ticks = int(fields[19])  # Field 22, but offset due to split
                    clock_ticks = os.sysconf("SC_CLK_TCK")
                    start_time = boot_time + (start_ticks / clock_ticks)
                    uptime = time.time() - start_time
        except (OSError, ValueError, IndexError):
            pass

        # Fallback to PID file mtime if proc method failed or gave invalid result
        if uptime is None or uptime < 0:
            try:
                pid_file = get_pid_file()
                if pid_file.exists():
                    uptime = time.time() - pid_file.stat().st_mtime
            except OSError:
                pass

        if uptime is not None and uptime >= 0:
            status["uptime_seconds"] = int(uptime)
            status["uptime"] = format_uptime(uptime)

    return status


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable form."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s"
    elif seconds < 86400:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"
    else:
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        return f"{days}d {hours}h"


def setup_logging(log_to_file: bool = True, debug: bool = False) -> None:
    """Setup logging configuration."""
    level = logging.DEBUG if debug else logging.INFO

    handlers: list[logging.Handler] = []

    if log_to_file:
        log_file = get_log_file()
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        handlers.append(file_handler)

    # Also log to stderr when not daemonized
    if not log_to_file:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        handlers.append(stream_handler)

    logging.basicConfig(level=level, handlers=handlers, force=True)


def daemonize() -> int:
    """Fork the process to run as a daemon. Returns child PID to parent, 0 to child."""
    # Single fork approach - more compatible with containers
    pid = os.fork()
    if pid > 0:
        # Parent - return child PID
        return pid

    # Child process
    # Create new session and process group
    os.setsid()

    # Change working directory to prevent blocking unmount
    os.chdir("/")

    # Close standard file descriptors
    sys.stdout.flush()
    sys.stderr.flush()

    # Redirect stdin, stdout, stderr to /dev/null
    with open(os.devnull) as devnull_in:
        os.dup2(devnull_in.fileno(), sys.stdin.fileno())
    with open(os.devnull, "a+") as devnull_out:
        os.dup2(devnull_out.fileno(), sys.stdout.fileno())
        os.dup2(devnull_out.fileno(), sys.stderr.fileno())

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

    async def run(self) -> None:
        """Main daemon run loop."""
        self._shutdown_event = asyncio.Event()
        self._setup_signal_handlers()

        self.state = DaemonState(
            started_at=datetime.now(),
            config_path=self.config_path,
        )

        # Load config
        try:
            self.config = load_config(self.config_path)
            logger.info("Configuration loaded successfully")
        except FileNotFoundError as e:
            logger.warning(f"Config file not found: {e}. Running with defaults.")
            self.config = Config()

        # Setup scheduler
        self.scheduler = Scheduler()
        for task_config in self.config.tasks.values():
            self.scheduler.add_task(task_config)
            logger.info(f"Scheduled task: {task_config.name}")

        logger.info(f"OpenPaws daemon started with PID {os.getpid()}")
        logger.info(f"Loaded {len(self.config.tasks)} tasks")

        # Start scheduler
        self.scheduler.start(self._execute_task)

        # Wait for shutdown signal
        await self._shutdown_event.wait()

        # Cleanup
        if self.scheduler:
            self.scheduler.stop()
        logger.info("OpenPaws daemon stopped")

    def start(self, foreground: bool = False) -> int:
        """Start the daemon."""
        # Check if already running
        existing_pid = read_pid_file()
        if existing_pid and is_process_running(existing_pid):
            logger.error(f"Daemon already running with PID {existing_pid}")
            return 1

        if not foreground:
            # Daemonize - fork and return
            child_pid = daemonize()
            if child_pid > 0:
                # Parent process - wait briefly for child to start, then exit
                time.sleep(0.5)
                # Check if child wrote PID file and is running
                if is_process_running(child_pid):
                    return 0
                else:
                    return 1

            # Child process continues
            setup_logging(log_to_file=True)
        else:
            setup_logging(log_to_file=False)

        # Write PID file
        write_pid_file()

        try:
            asyncio.run(self.run())
        finally:
            remove_pid_file()

        return 0

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

        # Send SIGTERM for graceful shutdown
        logger.info(f"Sending SIGTERM to PID {pid}")
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as e:
            logger.error(f"Failed to send signal: {e}")
            return 1

        # Wait for process to exit
        for _ in range(timeout * 10):
            if not is_process_running(pid):
                logger.info("Daemon stopped successfully")
                remove_pid_file()
                return 0
            time.sleep(0.1)

        # Force kill if still running
        logger.warning("Process did not exit gracefully, sending SIGKILL")
        try:
            os.kill(pid, signal.SIGKILL)
            time.sleep(0.5)
            if not is_process_running(pid):
                logger.info("Daemon killed")
                remove_pid_file()
                return 0
        except OSError:
            pass

        logger.error("Failed to stop daemon")
        return 1
