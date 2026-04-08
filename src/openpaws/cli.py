"""OpenPaws CLI."""

import asyncio
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

import click
import yaml

from openpaws.config import Config, TaskConfig, load_config
from openpaws.daemon import Daemon, get_daemon_status, get_log_file
from openpaws.scheduler import ScheduledTask
from openpaws.storage import Storage, TaskState


@click.group()
@click.version_option()
def main():
    """OpenPaws - A lightweight, always-on AI assistant."""
    pass


def _get_config_or_empty(config_path: str | None = None) -> Config:
    """Load config, returning empty Config if not found."""
    try:
        return load_config(config_path)
    except FileNotFoundError:
        return Config()


def _exit_if_running(status: dict) -> None:
    """Exit with error if daemon is already running."""
    if status["running"]:
        click.echo(f"🐾 OpenPaws is already running (PID {status['pid']})")
        sys.exit(1)


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground")
def start(config, foreground):
    """Start the OpenPaws daemon."""
    _exit_if_running(get_daemon_status())

    if foreground:
        click.echo("🐾 Starting OpenPaws in foreground...")
    else:
        click.echo("🐾 Starting OpenPaws daemon...")

    daemon = Daemon(config_path=config)
    exit_code = daemon.start(foreground=foreground)
    if exit_code == 0 and not foreground:
        click.echo("✅ OpenPaws daemon started")
    sys.exit(exit_code)


@main.command()
def stop():
    """Stop the OpenPaws daemon."""
    daemon_status = get_daemon_status()
    if not daemon_status["running"]:
        click.echo("🐾 OpenPaws is not running")
        sys.exit(0)

    click.echo(f"🐾 Stopping OpenPaws (PID {daemon_status['pid']})...")
    exit_code = Daemon.stop()

    if exit_code == 0:
        click.echo("✅ OpenPaws daemon stopped")
    else:
        click.echo("❌ Failed to stop OpenPaws daemon")
    sys.exit(exit_code)


@main.command()
def status():
    """Show OpenPaws status."""
    daemon_status = get_daemon_status()

    click.echo("🐾 OpenPaws Status")
    click.echo("─" * 30)

    if daemon_status["running"]:
        click.echo("  Status:   Running ✅")
        click.echo(f"  PID:      {daemon_status['pid']}")
        if "uptime" in daemon_status:
            click.echo(f"  Uptime:   {daemon_status['uptime']}")
    else:
        click.echo("  Status:   Stopped 🔴")

    click.echo(f"  PID file: {daemon_status['pid_file']}")


@main.group()
def tasks():
    """Manage scheduled tasks."""
    pass


def _format_datetime(dt) -> str:
    """Format datetime for display, or '-' if None."""
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _task_config_to_state(task_cfg: TaskConfig) -> TaskState:
    """Create TaskState from a TaskConfig (for tasks not in storage)."""
    task = ScheduledTask(config=task_cfg)
    task.compute_next_run()
    return TaskState(
        name=task_cfg.name,
        schedule=_get_schedule_string(task_cfg),
        group_name=task_cfg.group,
        prompt=task_cfg.prompt,
        status=task.status,
        next_run=task.next_run,
    )


def _get_merged_tasks() -> list[TaskState]:
    """Get tasks from config merged with storage state."""
    config = _get_config_or_empty()
    storage = Storage()
    stored_tasks = {t.name: t for t in storage.load_all_tasks()}
    result = []

    for name, task_cfg in config.tasks.items():
        if name in stored_tasks:
            result.append(stored_tasks.pop(name))
        else:
            result.append(_task_config_to_state(task_cfg))

    result.extend(stored_tasks.values())
    return result


def _print_task_row(task: TaskState) -> None:
    """Print a single task row for the task list."""
    schedule = (task.schedule or "-")[:15]
    status = task.status or "active"
    next_run = _format_datetime(task.next_run) if task.status != "paused" else "-"
    click.echo(f"{task.name:<20} {schedule:<16} {status:<10} {next_run:<16}")


@tasks.command("list")
def tasks_list():
    """List all scheduled tasks."""
    all_tasks = _get_merged_tasks()

    if not all_tasks:
        click.echo("📋 No scheduled tasks found")
        return

    click.echo("📋 Scheduled Tasks")
    click.echo("─" * 65)
    click.echo(f"{'NAME':<20} {'SCHEDULE':<16} {'STATUS':<10} {'NEXT RUN':<16}")
    click.echo("─" * 65)

    for task in sorted(all_tasks, key=lambda t: t.name):
        _print_task_row(task)


def _validate_schedule_options(schedule, every, once) -> tuple[str, str]:
    """Validate and return the single schedule type and value."""
    options = [("schedule", schedule), ("every", every), ("once", once)]
    present = [(n, v) for n, v in options if v is not None]

    if len(present) == 0:
        click.echo("❌ Error: Must specify one of --schedule, --every, or --once")
        sys.exit(1)
    if len(present) > 1:
        names = [n for n, _ in present]
        click.echo(f"❌ Error: Specify only one schedule type, got: {', '.join(names)}")
        sys.exit(1)
    return present[0]


@tasks.command("add")
@click.option("--schedule", "-s", help="Cron schedule (e.g., '0 9 * * *')")
@click.option("--every", "-e", help="Interval (e.g., '1h', '30m', '60s')")
@click.option("--once", "-o", help="One-time timestamp (e.g., '2024-03-15 09:00')")
@click.option("--group", "-g", required=True, help="Group to send prompt to")
@click.option("--prompt", "-p", required=True, help="Task prompt")
@click.argument("name")
def tasks_add(schedule, every, once, group, prompt, name):
    """Add a new scheduled task.

    Specify exactly one of --schedule, --every, or --once.

    \b
    Examples:
      openpaws tasks add --every 1h -g main -p "Check health" heartbeat
      openpaws tasks add --once "2024-03-15 09:00" -g main -p "Reminder" remind
      openpaws tasks add --schedule "0 9 * * *" -g main -p "Daily summary" daily
    """
    schedule_type, schedule_value = _validate_schedule_options(schedule, every, once)
    click.echo(f"📝 Adding task '{name}'...")
    click.echo(f"   Type: {schedule_type}, Value: {schedule_value}")
    click.echo(f"   Group: {group}, Prompt: {prompt}")
    click.echo("🚧 Task persistence not yet implemented")


def _find_task_config(name: str) -> TaskConfig | None:
    """Find task config by name."""
    config = _get_config_or_empty()
    return config.tasks.get(name)


def _get_schedule_string(task_cfg: TaskConfig) -> str:
    """Build schedule string from task config."""
    if task_cfg.schedule:
        return task_cfg.schedule
    if task_cfg.interval:
        return f"every {task_cfg.interval}s"
    return task_cfg.once or ""


def _run_task_sync(config: Config, task: ScheduledTask) -> None:
    """Run a task synchronously."""
    from openpaws.runner import ConversationRunner

    runner = ConversationRunner(config)
    result = asyncio.run(runner.run_task(task))

    if result.success:
        click.echo("✅ Task completed")
        click.echo(f"📝 Response: {result.message}")
    else:
        click.echo(f"❌ Task failed: {result.error}")
        sys.exit(1)


@tasks.command("run")
@click.argument("name")
def tasks_run(name):
    """Run a task immediately."""
    task_cfg = _find_task_config(name)
    if task_cfg is None:
        click.echo(f"❌ Task '{name}' not found")
        sys.exit(1)

    click.echo(f"🚀 Running task '{name}'...")
    config = _get_config_or_empty()
    task = ScheduledTask(config=task_cfg)
    _run_task_sync(config, task)


def _create_paused_task_state(
    task_cfg: TaskConfig, stored: TaskState | None
) -> TaskState:
    """Create a paused TaskState."""
    return TaskState(
        name=task_cfg.name,
        schedule=_get_schedule_string(task_cfg),
        group_name=task_cfg.group,
        prompt=task_cfg.prompt,
        status="paused",
        next_run=None,
        last_run=stored.last_run if stored else None,
        last_result=stored.last_result if stored else None,
    )


@tasks.command("pause")
@click.argument("name")
def tasks_pause(name):
    """Pause a scheduled task."""
    task_cfg = _find_task_config(name)
    if task_cfg is None:
        click.echo(f"❌ Task '{name}' not found")
        sys.exit(1)

    storage = Storage()
    stored = storage.load_task(name)

    if stored and stored.status == "paused":
        click.echo(f"⏸️  Task '{name}' is already paused")
        return

    storage.save_task(_create_paused_task_state(task_cfg, stored))
    click.echo(f"⏸️  Task '{name}' paused")


def _compute_next_run_from_stored(task_cfg: TaskConfig, stored: TaskState):
    """Create ScheduledTask with stored state and compute next run."""
    task = ScheduledTask(config=task_cfg)
    task.last_run = stored.last_run
    task.last_result = stored.last_result
    task.compute_next_run()
    return task


def _create_resumed_task_state(task_cfg: TaskConfig, stored: TaskState) -> TaskState:
    """Create a resumed TaskState with recomputed next_run."""
    task = _compute_next_run_from_stored(task_cfg, stored)
    return TaskState(
        name=task_cfg.name,
        schedule=_get_schedule_string(task_cfg),
        group_name=task_cfg.group,
        prompt=task_cfg.prompt,
        status="active",
        next_run=task.next_run,
        last_run=stored.last_run,
        last_result=stored.last_result,
    )


def _save_resumed_task(task_cfg: TaskConfig, stored: TaskState) -> str:
    """Save resumed task and return formatted next_run time."""
    task_state = _create_resumed_task_state(task_cfg, stored)
    Storage().save_task(task_state)
    return _format_datetime(task_state.next_run)


@tasks.command("resume")
@click.argument("name")
def tasks_resume(name):
    """Resume a paused task."""
    task_cfg = _find_task_config(name)
    if task_cfg is None:
        click.echo(f"❌ Task '{name}' not found")
        sys.exit(1)

    stored = Storage().load_task(name)
    if stored is None or stored.status != "paused":
        click.echo(f"▶️  Task '{name}' is not paused")
        return

    next_run = _save_resumed_task(task_cfg, stored)
    click.echo(f"▶️  Task '{name}' resumed, next run: {next_run}")


def _tail_log_file(log_path: Path, lines: int = 50) -> list[str]:
    """Read last N lines from log file."""
    if not log_path.exists():
        return []
    with open(log_path) as f:
        return f.readlines()[-lines:]


def _filter_log_lines(lines: list[str], pattern: str | None) -> list[str]:
    """Filter log lines by pattern (case-insensitive)."""
    if pattern is None:
        return lines
    pattern_lower = pattern.lower()
    return [line for line in lines if pattern_lower in line.lower()]


def _start_tail_process(log_path: Path) -> subprocess.Popen:
    """Start a tail -f process."""
    return subprocess.Popen(
        ["tail", "-f", str(log_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _run_tail_with_grep(log_path: Path, pattern: str) -> None:
    """Run tail piped to grep for filtering."""
    tail_proc = _start_tail_process(log_path)
    grep_proc = subprocess.Popen(
        ["grep", "-i", "--line-buffered", pattern],
        stdin=tail_proc.stdout,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    tail_proc.stdout.close()
    grep_proc.wait()


def _follow_logs_with_filter(log_path: Path, pattern: str) -> None:
    """Follow logs with grep filtering."""
    click.echo(f"📄 Following logs (filter: {pattern})...")
    try:
        _run_tail_with_grep(log_path, pattern)
    except KeyboardInterrupt:
        pass


def _follow_logs(log_path: Path) -> None:
    """Follow logs without filtering."""
    click.echo(f"📄 Following logs ({log_path})...")
    try:
        subprocess.run(["tail", "-f", str(log_path)])
    except KeyboardInterrupt:
        pass


def _show_recent_logs(log_path: Path, lines: int, pattern: str | None) -> None:
    """Show recent log lines with optional filtering."""
    log_lines = _tail_log_file(log_path, lines)
    filtered = _filter_log_lines(log_lines, pattern)

    if not filtered:
        msg = f"No log entries matching '{pattern}'" if pattern else "Log file is empty"
        click.echo(f"📄 {msg}")
        return

    header = f"filter: {pattern}" if pattern else str(log_path)
    click.echo(f"📄 Recent logs ({header}):")
    click.echo("─" * 60)
    for line in filtered:
        click.echo(line.rstrip())


def _handle_missing_log_file(log_path: Path) -> bool:
    """Print message if log file missing. Returns True if missing."""
    if log_path.exists():
        return False
    click.echo(f"📄 Log file not found: {log_path}")
    click.echo("   Start the daemon to create logs: openpaws start")
    return True


@main.command()
@click.option("--group", "-g", help="Filter by group")
@click.option("--task", "-t", help="Filter by task")
@click.option("--lines", "-n", default=50, help="Number of lines to show")
@click.option("--follow", "-f", is_flag=True, help="Follow log output (like tail -f)")
def logs(group, task, lines, follow):
    """View OpenPaws logs."""
    log_path = get_log_file()
    if _handle_missing_log_file(log_path):
        return

    filter_pattern = group or task
    if follow:
        if filter_pattern:
            _follow_logs_with_filter(log_path, filter_pattern)
        else:
            _follow_logs(log_path)
    else:
        _show_recent_logs(log_path, lines, filter_pattern)


@main.group()
def setup():
    """Setup wizards for channels and integrations."""
    pass


def _get_config_dir() -> Path:
    """Get the OpenPaws config directory."""
    import os

    base = os.environ.get("OPENPAWS_DIR", str(Path.home() / ".openpaws"))
    return Path(base)


def _get_config_file() -> Path:
    """Get the config file path."""
    return _get_config_dir() / "config.yaml"


def _load_config_yaml() -> dict:
    """Load config as raw YAML dict."""
    config_file = _get_config_file()
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    return {}


def _save_config_yaml(config: dict) -> None:
    """Save config dict to YAML file."""
    config_dir = _get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"

    with open(config_file, "w") as f:
        f.write("# OpenPaws Configuration\n")
        f.write("# See docs/CAMPFIRE_SETUP.md for setup instructions\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def _parse_campfire_curl(curl_cmd: str) -> tuple[str, str, str] | None:
    """Parse Campfire bot curl command to extract base_url, room_id, bot_key.

    Expected format:
        curl -d 'Hello!' http://campfire.localhost/rooms/1/2-rk2SGfi9lZW0/messages

    Returns (base_url, room_id, bot_key) or None if parsing fails.
    """
    # Match URL pattern: http(s)://host/rooms/{room_id}/{bot_key}/messages
    pattern = r"(https?://[^/]+)/rooms/(\d+)/([^/]+)/messages"
    match = re.search(pattern, curl_cmd)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


def _check_campfire_reachable(base_url: str) -> bool:
    """Check if Campfire is reachable."""
    import urllib.request
    import urllib.error

    try:
        req = urllib.request.Request(f"{base_url}/session/new", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def _check_campfire_setup_complete(base_url: str) -> bool:
    """Check if Campfire initial setup is complete.

    Returns True if setup is complete (sign-in page shows), False if setup needed.
    """
    import urllib.request

    try:
        req = urllib.request.Request(f"{base_url}/session/new", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            # If "Sign in" title is present, setup is complete
            return "<title>Sign in</title>" in content
    except Exception:
        return False


def _test_campfire_bot_key(base_url: str, room_id: str, bot_key: str) -> tuple[bool, str]:
    """Test if a bot key is valid and room exists.

    Returns (success, message) tuple.
    - (True, "success") if bot key is valid and room exists
    - (False, "invalid_key") if bot key is invalid
    - (False, "invalid_room") if bot key is valid but room doesn't exist
    - (False, "error") for other errors
    """
    import urllib.request
    import urllib.error

    url = f"{base_url}/rooms/{room_id}/{bot_key}/messages"
    data = "🐾 OpenPaws connected successfully!".encode("utf-8")

    try:
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "text/plain; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 201:
                return True, "success"
            return False, "error"
    except urllib.error.HTTPError as e:
        if e.code == 302:
            return False, "invalid_key"
        elif e.code == 500:
            return False, "invalid_room"
        return False, f"http_{e.code}"
    except urllib.error.URLError as e:
        return False, f"connection_error: {e.reason}"
    except Exception as e:
        return False, f"error: {e}"


def _find_valid_room(base_url: str, bot_key: str, max_rooms: int = 10) -> str | None:
    """Try to find a valid room ID by testing room numbers 1 through max_rooms.

    Returns the first valid room ID found, or None if none found.
    """
    for room_id in range(1, max_rooms + 1):
        success, result = _test_campfire_bot_key(base_url, str(room_id), bot_key)
        if success:
            return str(room_id)
        elif result == "invalid_key":
            # Bot key is invalid, no point testing more rooms
            return None
    return None


@setup.command("campfire")
@click.option("--url", help="Campfire base URL (e.g., http://campfire.localhost)")
@click.option("--bot-key", help="Bot key from Campfire (e.g., 2-rk2SGfi9lZW0)")
@click.option("--room-id", help="Default room ID (e.g., 1)")
@click.option("--webhook-port", default=8765, help="Local webhook port (default: 8765)")
@click.option("--no-browser", is_flag=True, help="Don't open browser automatically")
def setup_campfire(url, bot_key, room_id, webhook_port, no_browser):
    """Interactive setup wizard for Campfire integration.

    This wizard will guide you through:
    1. Checking Campfire status
    2. Opening Campfire to create a bot (if needed)
    3. Configuring the webhook URL
    4. Extracting the bot key
    5. Testing the connection
    6. Saving the configuration
    """
    click.echo()
    click.echo("🏕️  Campfire Setup Wizard")
    click.echo("═" * 40)
    click.echo()

    # Step 1: Get Campfire URL
    if not url:
        url = click.prompt(
            "Campfire URL",
            default="http://campfire.localhost",
        )

    # Normalize URL
    url = url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"

    click.echo()
    click.echo(f"📍 Using Campfire at: {url}")

    # Step 2: Check Campfire status
    click.echo()
    click.echo("─" * 40)
    click.echo("Checking Campfire Status")
    click.echo("─" * 40)
    click.echo()

    if not _check_campfire_reachable(url):
        click.echo(f"   ❌ Cannot reach Campfire at {url}")
        click.echo()
        click.echo("   Make sure Campfire is running:")
        click.echo("     once list              # Check if deployed")
        click.echo("     once deploy campfire   # Deploy if needed")
        click.echo()
        if not click.confirm("Continue anyway?", default=False):
            sys.exit(1)
    else:
        click.echo(f"   ✅ Campfire is reachable")

        if _check_campfire_setup_complete(url):
            click.echo("   ✅ Campfire initial setup is complete")
        else:
            click.echo("   ⚠️  Campfire initial setup may not be complete")
            click.echo(f"      Open {url} to complete setup first")
            if not no_browser:
                if click.confirm("Open Campfire in browser to complete setup?", default=True):
                    webbrowser.open(url)
                    click.echo()
                    click.prompt("Press Enter when setup is complete", default="", show_default=False)

    # Step 3: Check for existing bot key in config
    existing_config = _load_config_yaml()
    existing_bot_key = (
        existing_config.get("channels", {}).get("campfire", {}).get("bot_key")
    )

    if existing_bot_key and existing_bot_key != "${CAMPFIRE_BOT_KEY}" and not bot_key:
        click.echo()
        click.echo(f"   Found existing bot key in config: {existing_bot_key[:10]}...")

        # Test if existing key still works
        test_room = room_id or "1"
        success, result = _test_campfire_bot_key(url, test_room, existing_bot_key)
        if success:
            click.echo("   ✅ Existing bot key is valid!")
            if click.confirm("Use existing bot key?", default=True):
                bot_key = existing_bot_key
                # Try to find a valid room if not specified
                if not room_id:
                    found_room = _find_valid_room(url, bot_key)
                    if found_room:
                        room_id = found_room
                        click.echo(f"   ✅ Found valid room: {room_id}")
        else:
            click.echo(f"   ⚠️  Existing bot key doesn't work ({result})")

    # Step 4: Guide user to create bot if needed
    if not bot_key:
        click.echo()
        click.echo("─" * 40)
        click.echo("Step 1: Create a Bot in Campfire")
        click.echo("─" * 40)
        click.echo()
        click.echo("You need to create a bot in Campfire's admin panel.")
        click.echo()
        click.echo("Bot settings to use:")
        click.echo(f"  • Name: OpenPaws (or your preference)")
        click.echo(f"  • Webhook URL: http://localhost:{webhook_port}/webhook")
        click.echo()

        bot_url = f"{url}/account/bots"

        if not no_browser:
            if click.confirm("Open Campfire bot settings in browser?", default=True):
                click.echo(f"   Opening {bot_url}...")
                webbrowser.open(bot_url)
                click.echo()

        click.echo("After creating the bot, Campfire will show you curl commands like:")
        click.echo()
        click.echo(
            f'  curl -d \'Hello!\' {url}/rooms/1/YOUR-BOT-KEY/messages'
        )
        click.echo()

        # Get the curl command or bot key
        click.echo("─" * 40)
        click.echo("Step 2: Enter Bot Information")
        click.echo("─" * 40)
        click.echo()
        click.echo("Paste one of the curl commands from Campfire, or just the bot key:")
        click.echo()
        user_input = click.prompt("Curl command or bot key").strip()

        # Try to parse as curl command first
        parsed = _parse_campfire_curl(user_input)
        if parsed:
            _, parsed_room_id, bot_key = parsed
            click.echo(f"   ✓ Extracted bot key: {bot_key}")
            click.echo(f"   ✓ Extracted room ID: {parsed_room_id}")
            if not room_id:
                room_id = parsed_room_id
        else:
            # Assume it's just the bot key
            bot_key = user_input
            if "-" not in bot_key:
                click.echo("   ⚠️  Bot key should be in format: ID-TOKEN (e.g., 2-rk2SGfi9lZW0)")

    # Step 5: Get/find room ID if not provided
    if not room_id:
        click.echo()
        click.echo("   Looking for available rooms...")
        found_room = _find_valid_room(url, bot_key)
        if found_room:
            click.echo(f"   ✅ Found room {found_room}")
            room_id = found_room
        else:
            room_id = click.prompt(
                "Default room ID (from Campfire URL /rooms/N)",
                default="1",
            )

    # Step 6: Test connection
    click.echo()
    click.echo("─" * 40)
    click.echo("Testing Connection")
    click.echo("─" * 40)
    click.echo()
    click.echo(f"Testing bot key with room {room_id}...")

    success, result = _test_campfire_bot_key(url, room_id, bot_key)
    if success:
        click.echo("   ✅ Connection successful! Check Campfire for the test message.")
    else:
        if result == "invalid_key":
            click.echo("   ❌ Bot key is invalid. Please check the key and try again.")
        elif result == "invalid_room":
            click.echo(f"   ❌ Room {room_id} doesn't exist. Bot key is valid though!")
            # Try to find a valid room
            click.echo("   Looking for a valid room...")
            found_room = _find_valid_room(url, bot_key)
            if found_room:
                room_id = found_room
                click.echo(f"   ✅ Found valid room: {room_id}")
                # Test again with the found room
                success, _ = _test_campfire_bot_key(url, room_id, bot_key)
                if success:
                    click.echo("   ✅ Connection successful!")
        else:
            click.echo(f"   ❌ Connection failed: {result}")

        if not success and not click.confirm("Continue with setup anyway?", default=False):
            click.echo("Setup cancelled.")
            sys.exit(1)

    # Step 5: Save configuration
    click.echo()
    click.echo("─" * 40)
    click.echo("Step 4: Saving Configuration")
    click.echo("─" * 40)
    click.echo()

    config = _load_config_yaml()

    # Ensure channels section exists
    if "channels" not in config:
        config["channels"] = {}

    # Add/update campfire channel
    config["channels"]["campfire"] = {
        "base_url": url,
        "bot_key": bot_key,
        "webhook_port": webhook_port,
        "webhook_path": "/webhook",
    }

    # Add a default group if none exists for campfire
    if "groups" not in config:
        config["groups"] = {}

    has_campfire_group = any(
        g.get("channel") == "campfire"
        for g in config.get("groups", {}).values()
        if isinstance(g, dict)
    )

    if not has_campfire_group:
        group_name = "campfire-main"
        config["groups"][group_name] = {
            "channel": "campfire",
            "chat_id": room_id,
            "trigger": "@paw",
        }
        click.echo(f"   Added group '{group_name}' for room {room_id}")

    _save_config_yaml(config)
    config_file = _get_config_file()
    click.echo(f"   ✅ Configuration saved to: {config_file}")

    # Final summary
    click.echo()
    click.echo("═" * 40)
    click.echo("🎉 Campfire Setup Complete!")
    click.echo("═" * 40)
    click.echo()
    click.echo("Configuration:")
    click.echo(f"  • Campfire URL: {url}")
    click.echo(f"  • Bot Key: {bot_key}")
    click.echo(f"  • Room ID: {room_id}")
    click.echo(f"  • Webhook: http://localhost:{webhook_port}/webhook")
    click.echo()
    click.echo("Next steps:")
    click.echo("  1. Start OpenPaws:  openpaws start")
    click.echo("  2. In Campfire, @mention your bot to test")
    click.echo()
    click.echo("Useful commands:")
    click.echo("  openpaws status    # Check if running")
    click.echo("  openpaws logs -f   # Follow logs")
    click.echo("  openpaws stop      # Stop daemon")
    click.echo()


if __name__ == "__main__":
    main()
