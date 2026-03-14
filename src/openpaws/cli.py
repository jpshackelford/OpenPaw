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
