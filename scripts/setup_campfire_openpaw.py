#!/usr/bin/env python3
"""
Setup script for Campfire + OpenPaws integration.

This script:
1. Installs the ONCE CLI (for managing Campfire)
2. Deploys Campfire using ONCE
3. Installs OpenPaws from the current branch
4. Configures OpenPaws for Campfire integration

Usage:
    python scripts/setup_campfire_openpaw.py [OPTIONS]

Options:
    --hostname HOST       Hostname for Campfire (default: campfire.localhost)
    --disable-tls         Disable TLS (for local development)
    --openpaws-branch B   Git branch for OpenPaws (default: feature/campfire-adapter)
    --skip-campfire       Skip Campfire installation
    --skip-openpaws       Skip OpenPaws installation
    --help                Show this help message
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ANSI colors
RED = "\033[0;31m"
GREEN = "\033[0;32m"
YELLOW = "\033[1;33m"
BLUE = "\033[0;34m"
NC = "\033[0m"  # No Color

# Defaults
DEFAULT_HOSTNAME = "campfire.localhost"
DEFAULT_BRANCH = "feature/campfire-adapter"
OPENPAWS_REPO = "https://github.com/jpshackelford/OpenPaw.git"


def log_info(msg: str) -> None:
    print(f"{BLUE}[INFO]{NC} {msg}")


def log_success(msg: str) -> None:
    print(f"{GREEN}[OK]{NC} {msg}")


def log_warn(msg: str) -> None:
    print(f"{YELLOW}[WARN]{NC} {msg}")


def log_error(msg: str) -> None:
    print(f"{RED}[ERROR]{NC} {msg}", file=sys.stderr)


def run_command(
    cmd: list[str], check: bool = True, capture: bool = False
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(
        cmd,
        check=check,
        capture_output=capture,
        text=True,
    )


def command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(cmd) is not None


def detect_os() -> str:
    """Detect the operating system."""
    system = platform.system()
    if system == "Linux":
        return "linux"
    elif system == "Darwin":
        return "darwin"
    else:
        log_error(f"Unsupported OS: {system}")
        sys.exit(1)


def check_docker() -> None:
    """Check if Docker is available and running."""
    log_info("Checking Docker...")

    if not command_exists("docker"):
        log_error("Docker is not installed.")
        if detect_os() == "darwin":
            log_info(
                "Install Docker Desktop from: https://www.docker.com/products/docker-desktop"
            )
        else:
            log_info("Install Docker using: curl -fsSL https://get.docker.com | sh")
        sys.exit(1)

    result = run_command(["docker", "info"], check=False, capture=True)
    if result.returncode != 0:
        if detect_os() == "darwin":
            log_error(
                "Docker Desktop is installed but not running. Please start Docker Desktop."
            )
        else:
            log_warn(
                "Docker daemon not accessible. May need sudo or docker group membership."
            )
        sys.exit(1)

    log_success("Docker is available")


def check_uv() -> None:
    """Check if uv is installed, install if missing."""
    log_info("Checking uv...")

    if command_exists("uv"):
        result = run_command(["uv", "--version"], capture=True)
        log_success(f"uv is installed: {result.stdout.strip()}")
        return

    log_info("Installing uv...")
    try:
        run_command(
            ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
        )
        # Update PATH for this session
        home = Path.home()
        for bin_dir in [home / ".local/bin", home / ".cargo/bin"]:
            if bin_dir.exists():
                os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"

        if command_exists("uv"):
            log_success("uv installed successfully")
        else:
            raise RuntimeError("uv not found after installation")
    except Exception as e:
        log_error(f"Failed to install uv: {e}")
        log_info("Please install manually: https://docs.astral.sh/uv/")
        sys.exit(1)


def install_once_cli() -> None:
    """Install the ONCE CLI."""
    log_info("Installing ONCE CLI...")

    if command_exists("once"):
        result = run_command(["once", "version"], check=False, capture=True)
        version = result.stdout.strip() if result.returncode == 0 else "version unknown"
        log_success(f"ONCE CLI already installed: {version}")
        return

    try:
        env = os.environ.copy()
        env["ONCE_INTERACTIVE"] = "false"
        subprocess.run(
            ["sh", "-c", "curl -fsSL https://get.once.com | sh"],
            check=True,
            env=env,
        )

        # Check common install locations
        if command_exists("once"):
            log_success("ONCE CLI installed successfully")
        elif Path("/usr/local/bin/once").exists():
            os.environ["PATH"] = f"/usr/local/bin:{os.environ['PATH']}"
            log_success("ONCE CLI installed at /usr/local/bin/once")
        else:
            raise RuntimeError("once not found after installation")
    except Exception as e:
        log_error(f"ONCE CLI installation failed: {e}")
        sys.exit(1)


CAMPFIRE_IMAGE = "ghcr.io/basecamp/once-campfire"


def check_hostname_resolves(hostname: str) -> bool:
    """Check if a hostname resolves to an IP address."""
    import socket

    try:
        socket.gethostbyname(hostname)
        return True
    except socket.gaierror:
        return False


def ensure_hosts_entry(hostname: str) -> None:
    """Ensure the hostname resolves, suggesting /etc/hosts entry if needed."""
    if check_hostname_resolves(hostname):
        log_success(f"Hostname {hostname} resolves correctly")
        return

    log_warn(f"Hostname {hostname} does not resolve")
    log_info("For local development, add this line to /etc/hosts:")
    print(f"    127.0.0.1 {hostname}")
    log_info(f"Run: echo '127.0.0.1 {hostname}' | sudo tee -a /etc/hosts")

    # Try to add it automatically (will fail without sudo password)
    result = run_command(
        ["grep", "-q", hostname, "/etc/hosts"], check=False, capture=True
    )
    if result.returncode != 0:
        log_info("Attempting to add hosts entry (may require password)...")
        add_result = subprocess.run(
            f"echo '127.0.0.1 {hostname}' | sudo tee -a /etc/hosts",
            shell=True,
            capture_output=True,
            text=True,
        )
        if add_result.returncode == 0:
            log_success(f"Added {hostname} to /etc/hosts")
        else:
            log_warn("Could not add hosts entry automatically")
            log_info("Please add the entry manually and re-run this script")


def deploy_campfire(hostname: str, disable_tls: bool) -> None:
    """Deploy Campfire using ONCE CLI."""
    log_info("Deploying Campfire...")

    # Check if already deployed
    result = run_command(["once", "list"], check=False, capture=True)
    if result.returncode == 0 and "campfire" in result.stdout.lower():
        log_warn("Campfire appears to already be deployed")
        log_info("Use 'once' command to manage existing installation")
        return

    # For local development, ensure hostname resolves
    if hostname.endswith(".localhost") or hostname == "localhost":
        ensure_hosts_entry(hostname)

    # Build deploy command with full image path
    # The ONCE CLI requires the full ghcr.io path for the Campfire image
    cmd = ["once", "deploy", CAMPFIRE_IMAGE, "--host", hostname]
    if disable_tls:
        cmd.append("--disable-tls")

    log_info(f"Running: {' '.join(cmd)}")

    # On Linux, may need sudo if not in docker group
    if detect_os() == "linux":
        docker_result = run_command(["docker", "info"], check=False, capture=True)
        if docker_result.returncode != 0:
            cmd = ["sudo"] + cmd

    run_command(cmd)

    protocol = "http" if disable_tls else "https"
    log_success(f"Campfire deployed at: {protocol}://{hostname}")


def install_openpaws(branch: str) -> None:
    """Install OpenPaws from git using uv."""
    log_info(f"Installing OpenPaws from {OPENPAWS_REPO}@{branch}...")

    git_url = f"git+{OPENPAWS_REPO}@{branch}"

    # Check if already installed
    result = run_command(["uv", "tool", "list"], check=False, capture=True)
    if result.returncode == 0 and "openpaws" in result.stdout:
        log_info("Upgrading existing OpenPaws installation...")
        run_command(
            ["uv", "tool", "install", f"openpaws @ {git_url}", "--force"],
            check=False,
        )
    else:
        run_command(["uv", "tool", "install", f"openpaws @ {git_url}"])

    # Verify installation
    if command_exists("openpaws"):
        log_success("OpenPaws installed successfully")
        run_command(["openpaws", "--version"])
    else:
        # Try adding uv tools to PATH
        home = Path.home()
        os.environ["PATH"] = f"{home / '.local/bin'}:{os.environ['PATH']}"
        if command_exists("openpaws"):
            log_success("OpenPaws installed (add ~/.local/bin to PATH)")
        else:
            log_error("OpenPaws installation verification failed")
            log_info(f"Try running: uv tool install 'openpaws @ {git_url}'")
            sys.exit(1)


def configure_openpaws(hostname: str, disable_tls: bool) -> None:
    """Configure OpenPaws for Campfire integration."""
    log_info("Configuring OpenPaws for Campfire...")

    config_dir = Path.home() / ".openpaws"
    config_file = config_dir / "config.yaml"

    config_dir.mkdir(parents=True, exist_ok=True)

    campfire_url = f"{'http' if disable_tls else 'https'}://{hostname}"

    if config_file.exists():
        merge_campfire_config(config_file, campfire_url)
        log_success(f"Campfire channel added to existing config: {config_file}")
    else:
        create_new_config(config_file, campfire_url)
        log_success(f"Configuration created: {config_file}")


def merge_campfire_config(config_file: Path, campfire_url: str) -> None:
    """Merge Campfire settings into existing config."""
    log_info("Existing config found, adding Campfire channel...")

    try:
        import yaml
    except ImportError:
        log_error("PyYAML not available for config merging")
        log_info("Creating backup and generating new config instead")
        backup = config_file.with_suffix(".yaml.bak")
        shutil.copy(config_file, backup)
        log_info(f"Backup saved to: {backup}")
        create_new_config(config_file, campfire_url)
        return

    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

    # Ensure channels section exists
    if "channels" not in config:
        config["channels"] = {}

    # Add/update campfire channel (preserve existing bot_key if set)
    existing_campfire = config["channels"].get("campfire", {})
    existing_bot_key = existing_campfire.get("bot_key", "${CAMPFIRE_BOT_KEY}")

    config["channels"]["campfire"] = {
        "base_url": campfire_url,
        "bot_key": existing_bot_key,
        "webhook_port": existing_campfire.get("webhook_port", 8765),
        "webhook_path": existing_campfire.get("webhook_path", "/webhook"),
    }

    # Add a campfire group if no campfire groups exist
    if "groups" not in config:
        config["groups"] = {}

    has_campfire_group = any(
        g.get("channel") == "campfire"
        for g in config.get("groups", {}).values()
        if isinstance(g, dict)
    )

    if not has_campfire_group:
        group_name = "campfire-main"
        counter = 1
        while group_name in config["groups"]:
            group_name = f"campfire-main-{counter}"
            counter += 1

        config["groups"][group_name] = {
            "channel": "campfire",
            "chat_id": "1",
            "trigger": "@paw",
        }
        log_info(f"Added group '{group_name}' for Campfire")

    with open(config_file, "w") as f:
        f.write("# OpenPaws Configuration\n")
        f.write("# See docs/CAMPFIRE_SETUP.md for Campfire setup instructions\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def create_new_config(config_file: Path, campfire_url: str) -> None:
    """Create a new config file with Campfire settings."""
    log_info("Creating new config file...")

    from datetime import datetime

    config_content = f"""\
# OpenPaws Configuration
# Generated by setup_campfire_openpaw.py on {datetime.now().isoformat()}
#
# See docs/CAMPFIRE_SETUP.md for Campfire setup instructions

channels:
  campfire:
    # Your Campfire instance URL
    base_url: {campfire_url}

    # Bot key from Campfire Admin → Bots
    # Format: {{id}}-{{token}} (e.g., 123-abc123xyz456)
    bot_key: ${{CAMPFIRE_BOT_KEY}}

    # Webhook settings (OpenPaws receives messages here)
    webhook_port: 8765
    webhook_path: /webhook

groups:
  campfire-main:
    channel: campfire
    # Room ID from Campfire URL (e.g., /rooms/1 → chat_id: "1")
    chat_id: "1"
    trigger: "@paw"

tasks:
  # Example scheduled task
  # daily-summary:
  #   schedule: "0 9 * * *"  # 9 AM daily
  #   group: campfire-main
  #   prompt: "Summarize yesterday's discussion"

agent:
  # Model to use (requires appropriate API key in environment)
  model: anthropic/claude-sonnet-4-20250514
"""
    config_file.write_text(config_content)


def print_next_steps(hostname: str, disable_tls: bool) -> None:
    """Print post-installation instructions."""
    campfire_url = f"{'http' if disable_tls else 'https'}://{hostname}"

    print()
    print(f"{GREEN}═══════════════════════════════════════════════════════════════{NC}")
    print(f"{GREEN}                    Installation Complete!                      {NC}")
    print(f"{GREEN}═══════════════════════════════════════════════════════════════{NC}")
    print()
    print(f"{BLUE}Next Steps:{NC}")
    print()
    print(f"1. {YELLOW}Set up Campfire:{NC}")
    print(f"   Open: {campfire_url}")
    print("   Complete the initial setup wizard")
    print()
    print(f"2. {YELLOW}Create a bot in Campfire:{NC}")
    print("   • Go to Account → Bots")
    print("   • Click 'New bot'")
    print("   • Name: OpenPaws (or your preference)")
    print("   • Webhook URL: http://localhost:8765/webhook")
    print("   • Copy the bot key (format: 123-abc123xyz)")
    print()
    print(f"3. {YELLOW}Configure OpenPaws:{NC}")
    print("   export CAMPFIRE_BOT_KEY='your-bot-key-here'")
    print("   # Or add to ~/.openpaws/.env")
    print()
    print(f"4. {YELLOW}Start OpenPaws:{NC}")
    print("   openpaws start")
    print()
    print(f"5. {YELLOW}Test the integration:{NC}")
    print("   In Campfire, @mention your bot: @OpenPaws hello")
    print()
    print(f"{BLUE}Useful Commands:{NC}")
    print("   openpaws status     # Check daemon status")
    print("   openpaws logs       # View logs")
    print("   openpaws stop       # Stop the daemon")
    print("   once                # Manage Campfire (TUI)")
    print("   once list           # List deployed apps")
    print()
    print(f"{BLUE}Documentation:{NC}")
    print("   • Campfire Setup: docs/CAMPFIRE_SETUP.md")
    print("   • OpenPaws Config: ~/.openpaws/config.yaml")
    print()


def check_prerequisites(skip_campfire: bool, skip_openpaws: bool) -> bool:
    """Check all prerequisites upfront. Returns True if all checks pass."""
    log_info("Checking prerequisites...")
    all_ok = True

    # Python version
    if sys.version_info < (3, 10):
        log_error(f"Python 3.10+ required, found {sys.version}")
        all_ok = False
    else:
        log_success(f"Python {sys.version_info.major}.{sys.version_info.minor}")

    # Docker (only if installing Campfire)
    if not skip_campfire:
        if not command_exists("docker"):
            log_error("Docker is not installed")
            if detect_os() == "darwin":
                log_info(
                    "  Install from: https://www.docker.com/products/docker-desktop"
                )
            else:
                log_info("  Install with: curl -fsSL https://get.docker.com | sh")
            all_ok = False
        else:
            result = run_command(["docker", "info"], check=False, capture=True)
            if result.returncode != 0:
                log_error("Docker is installed but not running")
                if detect_os() == "darwin":
                    log_info("  Please start Docker Desktop")
                else:
                    log_info("  Start with: sudo systemctl start docker")
                    log_info("  Or add yourself to docker group: sudo usermod -aG docker $USER")
                all_ok = False
            else:
                log_success("Docker is running")

    # curl (needed for installing uv and once)
    if not command_exists("curl"):
        log_error("curl is not installed")
        log_info("  Install curl using your package manager")
        all_ok = False
    else:
        log_success("curl is available")

    # uv (will be installed if missing, but warn if not present)
    if not skip_openpaws:
        if command_exists("uv"):
            log_success("uv is installed")
        else:
            log_warn("uv not found (will be installed automatically)")

    # PyYAML (needed for config merging if config exists)
    config_file = Path.home() / ".openpaws" / "config.yaml"
    if config_file.exists():
        try:
            import yaml  # noqa: F401
            log_success("PyYAML available (for config merging)")
        except ImportError:
            log_warn("PyYAML not installed - existing config will be backed up, not merged")

    print()
    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Setup script for Campfire + OpenPaws integration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # Local development setup (no TLS)
  python scripts/setup_campfire_openpaw.py --disable-tls

  # Production setup with custom hostname
  python scripts/setup_campfire_openpaw.py --hostname chat.example.com

  # Only install OpenPaws (Campfire already running)
  python scripts/setup_campfire_openpaw.py --skip-campfire
""",
    )
    parser.add_argument(
        "--hostname",
        default=DEFAULT_HOSTNAME,
        help=f"Hostname for Campfire (default: {DEFAULT_HOSTNAME})",
    )
    parser.add_argument(
        "--disable-tls",
        action="store_true",
        help="Disable TLS (for local development)",
    )
    parser.add_argument(
        "--openpaws-branch",
        default=DEFAULT_BRANCH,
        help=f"Git branch for OpenPaws (default: {DEFAULT_BRANCH})",
    )
    parser.add_argument(
        "--skip-campfire",
        action="store_true",
        help="Skip Campfire installation",
    )
    parser.add_argument(
        "--skip-openpaws",
        action="store_true",
        help="Skip OpenPaws installation",
    )

    args = parser.parse_args()

    print()
    print(f"{BLUE}╔═══════════════════════════════════════════════════════════════╗{NC}")
    print(f"{BLUE}║        Campfire + OpenPaws Setup Script                       ║{NC}")
    print(f"{BLUE}╚═══════════════════════════════════════════════════════════════╝{NC}")
    print()

    log_info("Configuration:")
    log_info(f"  Campfire hostname: {args.hostname}")
    log_info(f"  TLS disabled: {args.disable_tls}")
    log_info(f"  OpenPaws branch: {args.openpaws_branch}")
    log_info(f"  Skip Campfire: {args.skip_campfire}")
    log_info(f"  Skip OpenPaws: {args.skip_openpaws}")
    print()

    # Fail fast: check all prerequisites upfront
    if not check_prerequisites(args.skip_campfire, args.skip_openpaws):
        log_error("Prerequisites not met. Please fix the issues above and try again.")
        sys.exit(1)

    # Campfire installation
    if not args.skip_campfire:
        install_once_cli()
        deploy_campfire(args.hostname, args.disable_tls)

    # OpenPaws installation
    if not args.skip_openpaws:
        check_uv()  # This will install uv if missing
        install_openpaws(args.openpaws_branch)

    # Configuration
    configure_openpaws(args.hostname, args.disable_tls)

    # Next steps
    print_next_steps(args.hostname, args.disable_tls)


if __name__ == "__main__":
    main()
