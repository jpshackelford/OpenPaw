"""OpenPaws CLI."""

import sys

import click

from openpaws.daemon import Daemon, get_daemon_status


@click.group()
@click.version_option()
def main():
    """OpenPaws - A lightweight, always-on AI assistant."""
    pass


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


@tasks.command("list")
def tasks_list():
    """List all scheduled tasks."""
    click.echo("🚧 Not yet implemented")


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


@tasks.command("run")
@click.argument("name")
def tasks_run(name):
    """Run a task immediately."""
    click.echo(f"🚧 Running task '{name}'... Not yet implemented")


@main.command()
@click.option("--group", "-g", help="Filter by group")
@click.option("--task", "-t", help="Filter by task")
def logs(group, task):
    """View OpenPaws logs."""
    click.echo("🚧 Not yet implemented")


if __name__ == "__main__":
    main()
