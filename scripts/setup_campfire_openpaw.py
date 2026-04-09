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


def check_pyyaml_available() -> bool:
    """Check if PyYAML is available via uv run."""
    if not command_exists("uv"):
        return False
    result = subprocess.run(
        ["uv", "run", "--with", "pyyaml", "python", "-c", "import yaml"],
        capture_output=True,
    )
    return result.returncode == 0


def run_yaml_merge(config_file: Path, campfire_url: str) -> bool:
    """Run YAML merge using uv to ensure PyYAML is available.

    Uses 'uv run --with pyyaml' to execute in an environment where PyYAML
    is guaranteed to be available, regardless of the current Python's packages.

    Returns True if successful, False otherwise.
    """
    # Use raw string (r''') to avoid issues with escape sequences in the embedded script
    merge_script = r'''
import sys
import yaml

config_path = sys.argv[1]
campfire_url = sys.argv[2]

with open(config_path) as f:
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
    print(f"Added group '{group_name}' for Campfire", file=sys.stderr)

header = "# OpenPaws Configuration\n# See docs/CAMPFIRE_SETUP.md for Campfire setup instructions\n\n"
with open(config_path, "w") as f:
    f.write(header)
    yaml.dump(config, f, default_flow_style=False, sort_keys=False)
'''

    # Use uv run --with pyyaml to ensure PyYAML is available
    # This works regardless of the current Python's installed packages
    if command_exists("uv"):
        result = subprocess.run(
            [
                "uv", "run", "--with", "pyyaml",
                "python", "-c", merge_script, str(config_file), campfire_url
            ],
            capture_output=True,
            text=True,
        )
    else:
        # Fallback to direct Python execution (may fail if PyYAML not installed)
        result = subprocess.run(
            [sys.executable, "-c", merge_script, str(config_file), campfire_url],
            capture_output=True,
            text=True,
        )

    if result.returncode == 0:
        if result.stderr:
            log_info(result.stderr.strip())
        return True
    else:
        log_error(f"YAML merge failed: {result.stderr}")
        return False


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


# Default Campfire image - use fork with bot read API until PR is merged
# PR: https://github.com/basecamp/once-campfire/pull/190
# Once merged, switch back to: ghcr.io/basecamp/once-campfire
CAMPFIRE_IMAGE_DEFAULT = "ghcr.io/jpshackelford/once-campfire:bot-api-v2"
CAMPFIRE_IMAGE_OFFICIAL = "ghcr.io/basecamp/once-campfire"


def check_hostname_resolves(hostname: str) -> bool:
    """Check if a hostname resolves to an IP address."""
    import socket

    try:
        socket.gethostbyname(hostname)
        return True
    except socket.gaierror:
        return False


def ensure_hosts_entry(hostname: str) -> bool:
    """Ensure the hostname resolves, adding /etc/hosts entry if needed.

    Returns True if hostname resolves (either already or after adding entry).
    Returns False if we couldn't make it resolve.
    """
    if check_hostname_resolves(hostname):
        log_success(f"Hostname {hostname} resolves correctly")
        return True

    log_info(f"Hostname {hostname} does not resolve - adding to /etc/hosts...")

    # Check if entry already exists in hosts file (but DNS not resolving yet)
    result = run_command(
        ["grep", "-q", hostname, "/etc/hosts"], check=False, capture=True
    )
    if result.returncode == 0:
        # Entry exists but not resolving - might be a DNS cache issue
        log_warn(f"Entry for {hostname} exists in /etc/hosts but not resolving")
        log_info("Try flushing DNS cache: sudo dscacheutil -flushcache")
        return False

    # Add the entry (requires sudo)
    # Use shell command with echo to avoid terminal issues with sudo password prompt
    # When using subprocess with input= and capture_output=, the terminal may have
    # issues with sudo password input (e.g., Enter key not working)
    log_info("Adding hosts entry (may require password)...")
    add_result = subprocess.run(
        ["sudo", "sh", "-c", f"echo '127.0.0.1 {hostname}' >> /etc/hosts"],
    )

    if add_result.returncode == 0:
        log_success(f"Added {hostname} to /etc/hosts")
        # Verify it now resolves
        if check_hostname_resolves(hostname):
            return True
        else:
            log_warn("Entry added but hostname still not resolving")
            log_info("Try flushing DNS cache: sudo dscacheutil -flushcache")
            return True  # Entry is there, should work after cache flush
    else:
        log_error("Could not add hosts entry")
        log_info(f"Please run manually: echo '127.0.0.1 {hostname}' | sudo tee -a /etc/hosts")
        return False


def deploy_campfire(hostname: str, disable_tls: bool, campfire_image: str) -> None:
    """Deploy Campfire using ONCE CLI."""
    log_info("Deploying Campfire...")

    # Check if already deployed
    result = run_command(["once", "list"], check=False, capture=True)
    if result.returncode == 0 and "campfire" in result.stdout.lower():
        log_warn("Campfire appears to already be deployed")
        log_info("Use 'once' command to manage existing installation")
        return

    # Hostname resolution is handled in check_prerequisites for .localhost domains

    # Build deploy command with full image path
    # The ONCE CLI requires the full ghcr.io path for the Campfire image
    cmd = ["once", "deploy", campfire_image, "--host", hostname]
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
    """Merge Campfire settings into existing config.

    Uses subprocess to run YAML operations, which ensures PyYAML is available
    even if it was just installed and can't be imported in the current process.
    """
    log_info("Existing config found, adding Campfire channel...")

    # Try subprocess merge first (works even if PyYAML was just installed)
    if run_yaml_merge(config_file, campfire_url):
        return

    # Fallback: backup and create new config
    log_warn("Could not merge config, creating backup and new config")
    backup = config_file.with_suffix(".yaml.bak")
    shutil.copy(config_file, backup)
    log_info(f"Backup saved to: {backup}")
    create_new_config(config_file, campfire_url)


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


def run_setup_wizard(hostname: str, disable_tls: bool) -> bool:
    """Run the interactive Campfire setup wizard.

    Returns True if wizard completed successfully, False otherwise.
    """
    campfire_url = f"{'http' if disable_tls else 'https'}://{hostname}"

    log_info("Running Campfire setup wizard...")
    print()

    try:
        result = subprocess.run(
            ["openpaws", "setup", "campfire", "--url", campfire_url],
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        log_error("openpaws command not found")
        log_info("Try running: openpaws setup campfire")
        return False
    except KeyboardInterrupt:
        print()
        log_info("Setup wizard cancelled")
        return False


def print_next_steps(hostname: str, disable_tls: bool, wizard_completed: bool) -> None:
    """Print post-installation instructions."""
    campfire_url = f"{'http' if disable_tls else 'https'}://{hostname}"

    print()
    print(f"{GREEN}═══════════════════════════════════════════════════════════════{NC}")
    print(f"{GREEN}                    Installation Complete!                      {NC}")
    print(f"{GREEN}═══════════════════════════════════════════════════════════════{NC}")
    print()

    if wizard_completed:
        # Wizard already ran - simpler next steps
        print(f"{BLUE}Next Steps:{NC}")
        print()
        print(f"1. {YELLOW}Start OpenPaws:{NC}")
        print("   openpaws start")
        print()
        print(f"2. {YELLOW}Test the integration:{NC}")
        print("   In Campfire, @mention your bot: @OpenPaws hello")
        print()
    else:
        # Wizard didn't run - full instructions
        print(f"{BLUE}Next Steps:{NC}")
        print()
        print(f"1. {YELLOW}Set up Campfire:{NC}")
        print(f"   Open: {campfire_url}")
        print("   Complete the initial setup wizard")
        print()
        print(f"2. {YELLOW}Run the bot setup wizard:{NC}")
        print("   openpaws setup campfire")
        print()
        print("   This will guide you through:")
        print("   • Creating a bot in Campfire")
        print("   • Configuring the webhook")
        print("   • Testing the connection")
        print()
        print(f"3. {YELLOW}Start OpenPaws:{NC}")
        print("   openpaws start")
        print()
        print(f"4. {YELLOW}Test the integration:{NC}")
        print("   In Campfire, @mention your bot: @OpenPaws hello")
        print()

    print(f"{BLUE}Useful Commands:{NC}")
    print("   openpaws setup campfire  # Configure Campfire bot")
    print("   openpaws status          # Check daemon status")
    print("   openpaws logs            # View logs")
    print("   openpaws stop            # Stop the daemon")
    print("   once                     # Manage Campfire (TUI)")
    print("   once list                # List deployed apps")
    print()
    print(f"{BLUE}Documentation:{NC}")
    print("   • Campfire Setup: docs/CAMPFIRE_SETUP.md")
    print("   • OpenPaws Config: ~/.openpaws/config.yaml")
    print()


def check_prerequisites(
    skip_campfire: bool, skip_openpaws: bool, hostname: str
) -> bool:
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
    # We use 'uv run --with pyyaml' to ensure it's available
    config_file = Path.home() / ".openpaws" / "config.yaml"
    if config_file.exists():
        if check_pyyaml_available():
            log_success("PyYAML available via uv (for config merging)")
        elif command_exists("uv"):
            log_warn("PyYAML check failed - config merge may fall back to backup")
        else:
            log_warn("uv not available - existing config will be backed up")

    # Hostname resolution for .localhost domains (requires /etc/hosts entry)
    if not skip_campfire and (
        hostname.endswith(".localhost") or hostname == "localhost"
    ):
        if not ensure_hosts_entry(hostname):
            all_ok = False

    print()
    return all_ok


def prompt_yes_no(prompt: str, default: bool = True) -> bool:
    """Prompt for yes/no input."""
    import sys
    import tty
    import termios

    suffix = " [Y/n] " if default else " [y/N] "
    sys.stdout.write(prompt + suffix)
    sys.stdout.flush()

    # Save terminal settings and set to raw mode to read single char
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    # Print newline after input
    sys.stdout.write("\n")
    sys.stdout.flush()

    # Handle Enter key (CR or LF) as accepting default
    if ch in ("\r", "\n", ""):
        return default
    # Handle y/Y as yes
    if ch.lower() == "y":
        return True
    # Handle n/N as no
    if ch.lower() == "n":
        return False
    # Any other key: return default
    return default


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

  # Install without running the bot setup wizard
  python scripts/setup_campfire_openpaw.py --disable-tls --no-wizard
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
    parser.add_argument(
        "--no-wizard",
        action="store_true",
        help="Skip the interactive bot setup wizard",
    )
    parser.add_argument(
        "--campfire-image",
        default=CAMPFIRE_IMAGE_DEFAULT,
        help=f"Docker image for Campfire (default: {CAMPFIRE_IMAGE_DEFAULT})",
    )
    parser.add_argument(
        "--use-official-campfire",
        action="store_true",
        help=f"Use official Campfire image ({CAMPFIRE_IMAGE_OFFICIAL}) instead of fork",
    )

    args = parser.parse_args()

    # Resolve campfire image
    campfire_image = CAMPFIRE_IMAGE_OFFICIAL if args.use_official_campfire else args.campfire_image

    print()
    print(f"{BLUE}╔═══════════════════════════════════════════════════════════════╗{NC}")
    print(f"{BLUE}║        Campfire + OpenPaws Setup Script                       ║{NC}")
    print(f"{BLUE}╚═══════════════════════════════════════════════════════════════╝{NC}")
    print()

    log_info("Configuration:")
    log_info(f"  Campfire hostname: {args.hostname}")
    log_info(f"  Campfire image: {campfire_image}")
    log_info(f"  TLS disabled: {args.disable_tls}")
    log_info(f"  OpenPaws branch: {args.openpaws_branch}")
    log_info(f"  Skip Campfire: {args.skip_campfire}")
    log_info(f"  Skip OpenPaws: {args.skip_openpaws}")
    log_info(f"  Run wizard: {not args.no_wizard}")
    print()

    # Fail fast: check all prerequisites upfront
    if not check_prerequisites(args.skip_campfire, args.skip_openpaws, args.hostname):
        log_error("Prerequisites not met. Please fix the issues above and try again.")
        sys.exit(1)

    # Campfire installation
    if not args.skip_campfire:
        install_once_cli()
        deploy_campfire(args.hostname, args.disable_tls, campfire_image)

    # OpenPaws installation
    if not args.skip_openpaws:
        check_uv()  # This will install uv if missing
        install_openpaws(args.openpaws_branch)

    # Configuration (basic config file setup)
    configure_openpaws(args.hostname, args.disable_tls)

    # Interactive bot setup wizard
    wizard_completed = False
    if not args.no_wizard and not args.skip_openpaws:
        print()
        print(f"{BLUE}{'─' * 63}{NC}")
        print()
        if prompt_yes_no("Would you like to configure the Campfire bot now?"):
            wizard_completed = run_setup_wizard(args.hostname, args.disable_tls)
        else:
            log_info("Skipping bot setup wizard")
            log_info("You can run it later with: openpaws setup campfire")

    # Next steps
    print_next_steps(args.hostname, args.disable_tls, wizard_completed)


if __name__ == "__main__":
    main()
