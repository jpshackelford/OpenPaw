"""OpenPaws CLI."""

import click


@click.group()
@click.version_option()
def main():
    """OpenPaws - A lightweight, always-on AI assistant."""
    pass


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def start(config):
    """Start the OpenPaws daemon."""
    click.echo("🐾 Starting OpenPaws...")
    click.echo("🚧 Not yet implemented")


@main.command()
def stop():
    """Stop the OpenPaws daemon."""
    click.echo("🐾 Stopping OpenPaws...")
    click.echo("🚧 Not yet implemented")


@main.command()
def status():
    """Show OpenPaws status."""
    click.echo("🐾 OpenPaws Status")
    click.echo("🚧 Not yet implemented")


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
