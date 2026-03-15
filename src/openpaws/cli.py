"""OpenPaws CLI."""

import asyncio
import subprocess
import sys
from pathlib import Path

import click

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


if __name__ == "__main__":
    main()
