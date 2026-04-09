"""Campfire setup wizard for configuring the Campfire channel adapter.

This module provides an interactive wizard for setting up the Campfire
integration, including bot creation guidance, connection testing, and
configuration saving.

Usage:
    # Interactive setup (production)
    wizard = CampfireSetupWizard()
    wizard.run()

    # Testing with scripted input
    terminal = TestTerminalInput(["y", "http://campfire.localhost", ...])
    wizard = CampfireSetupWizard(terminal=terminal)
    wizard.run()
"""

import os
import re
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

import click
import yaml

from openpaws.terminal import RealTerminalInput, TerminalInput


@dataclass
class CampfireConfig:
    """Configuration for Campfire setup."""

    url: str = ""
    bot_key: str = ""
    room_id: str = ""
    webhook_port: int = 8765
    webhook_path: str = "/webhook"


@dataclass
class WizardOptions:
    """Options controlling wizard behavior."""

    no_browser: bool = False
    skip_connection_test: bool = False


@dataclass
class WizardState:
    """Mutable state during wizard execution."""

    config: CampfireConfig = field(default_factory=CampfireConfig)
    connection_tested: bool = False
    connection_success: bool = False


def get_config_dir() -> Path:
    """Get the OpenPaws config directory."""
    base = os.environ.get("OPENPAWS_DIR", str(Path.home() / ".openpaws"))
    return Path(base)


def get_config_file() -> Path:
    """Get the config file path."""
    return get_config_dir() / "config.yaml"


def load_config_yaml() -> dict:
    """Load config as raw YAML dict."""
    config_file = get_config_file()
    if config_file.exists():
        with open(config_file) as f:
            return yaml.safe_load(f) or {}
    return {}


def save_config_yaml(config: dict) -> None:
    """Save config dict to YAML file."""
    config_dir = get_config_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.yaml"

    with open(config_file, "w") as f:
        f.write("# OpenPaws Configuration\n")
        f.write("# See docs/CAMPFIRE_SETUP.md for setup instructions\n\n")
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


def parse_campfire_curl(curl_cmd: str) -> tuple[str, str, str] | None:
    """Parse Campfire bot curl command to extract base_url, room_id, bot_key.

    Expected format:
        curl -d 'Hello!' http://campfire.localhost/rooms/1/2-rk2SGfi9lZW0/messages

    Returns (base_url, room_id, bot_key) or None if parsing fails.
    """
    pattern = r"(https?://[^/]+)/rooms/(\d+)/([^/]+)/messages"
    match = re.search(pattern, curl_cmd)
    if match:
        return match.group(1), match.group(2), match.group(3)
    return None


def normalize_url(url: str) -> str:
    """Normalize a Campfire URL (strip trailing slash, add http:// if needed)."""
    url = url.rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def check_campfire_reachable(base_url: str) -> bool:
    """Check if Campfire is reachable."""
    import urllib.request

    try:
        req = urllib.request.Request(f"{base_url}/session/new", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def check_campfire_setup_complete(base_url: str) -> bool:
    """Check if Campfire initial setup is complete.

    Returns True if setup is complete (sign-in page shows), False if setup needed.
    """
    import urllib.request

    try:
        req = urllib.request.Request(f"{base_url}/session/new", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            return "<title>Sign in</title>" in content
    except Exception:
        return False


def http_error_to_result(e) -> tuple[bool, str]:
    """Convert HTTP error to (success, message) tuple."""
    if e.code == 302:
        return False, "invalid_key"
    if e.code == 500:
        return False, "invalid_room"
    return False, f"http_{e.code}"


def build_test_request(base_url: str, room_id: str, bot_key: str):
    """Build HTTP request for testing Campfire connection."""
    import urllib.request

    url = f"{base_url}/rooms/{room_id}/{bot_key}/messages"
    data = "🐾 OpenPaws connected successfully!".encode()
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    return urllib.request.Request(url, data=data, headers=headers)


def check_bot_key(base_url: str, room_id: str, bot_key: str) -> tuple[bool, str]:
    """Test if a bot key is valid and room exists."""
    import urllib.error
    import urllib.request

    req = build_test_request(base_url, room_id, bot_key)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return (True, "success") if resp.status == 201 else (False, "error")
    except urllib.error.HTTPError as e:
        return http_error_to_result(e)
    except urllib.error.URLError as e:
        return False, f"connection_error: {e.reason}"
    except Exception as e:
        return False, f"error: {e}"


def find_valid_room(base_url: str, bot_key: str, max_rooms: int = 10) -> str | None:
    """Try to find a valid room ID by testing room numbers 1 through max_rooms."""
    for room_id in range(1, max_rooms + 1):
        success, result = check_bot_key(base_url, str(room_id), bot_key)
        if success:
            return str(room_id)
        elif result == "invalid_key":
            return None
    return None


_SUMMARY_TEMPLATE = """
{sep}
🎉 Campfire Setup Complete!
{sep}

Configuration:
  • Campfire URL: {url}
  • Bot Key: {bot_key}
  • Room ID: {room_id}
  • Webhook: http://localhost:{port}/webhook

Next steps:
  1. Start OpenPaws:  openpaws start
  2. In Campfire, @mention your bot to test

Useful commands:
  openpaws status    # Check if running
  openpaws logs -f   # Follow logs
  openpaws stop      # Stop daemon
"""


class CampfireSetupWizard:
    """Interactive wizard for setting up Campfire integration.

    This wizard guides users through:
    1. Checking Campfire reachability
    2. Creating a bot in Campfire (with instructions)
    3. Entering bot key (via curl command or directly)
    4. Finding/selecting a room
    5. Testing the connection
    6. Saving configuration

    Args:
        terminal: Terminal input implementation. Defaults to RealTerminalInput.
        output: Callable for output (defaults to click.echo).
    """

    def __init__(
        self,
        terminal: TerminalInput | None = None,
        output=None,
    ):
        self.terminal = terminal or RealTerminalInput()
        self.output = output or click.echo
        self.state = WizardState()

    def run(
        self,
        url: str | None = None,
        bot_key: str | None = None,
        room_id: str | None = None,
        webhook_port: int = 8765,
        no_browser: bool = False,
    ) -> CampfireConfig:
        """Run the setup wizard.

        Args:
            url: Pre-specified Campfire URL (will prompt if None).
            bot_key: Pre-specified bot key (will prompt if None).
            room_id: Pre-specified room ID (will auto-detect if None).
            webhook_port: Local webhook port (default: 8765).
            no_browser: If True, don't open browser automatically.

        Returns:
            The final CampfireConfig.
        """
        options = WizardOptions(no_browser=no_browser)

        # Initialize state
        self.state.config.webhook_port = webhook_port

        # Step 1: Get and normalize URL
        url = self._get_url(url)
        self.state.config.url = url

        # Step 2: Check Campfire status
        self._check_status(url, options)

        # Step 3: Check for existing bot key or get new one
        bot_key, room_id = self._get_bot_key(url, bot_key, room_id, options)
        self.state.config.bot_key = bot_key

        # Step 4: Find or prompt for room ID
        room_id = self._get_room_id(url, bot_key, room_id)
        self.state.config.room_id = room_id

        # Step 5: Test connection
        self._test_connection(url, room_id, bot_key, options)

        # Step 6: Save configuration
        self._save_config()

        # Step 7: Print summary
        self._print_summary()

        return self.state.config

    def _get_url(self, url: str | None) -> str:
        """Get and normalize the Campfire URL."""
        self._print_header()

        if not url:
            url = self.terminal.prompt(
                "Campfire URL", default="http://campfire.localhost"
            )

        url = normalize_url(url)
        self.output(f"\n📍 Using Campfire at: {url}\n")
        return url

    def _print_header(self) -> None:
        """Print the wizard header."""
        self.output("\n🏕️  Campfire Setup Wizard")
        self.output("═" * 40)
        self.output()

    def _check_status(self, url: str, options: WizardOptions) -> None:
        """Check Campfire reachability and setup status."""
        self._print_section("Checking Campfire Status")

        if not check_campfire_reachable(url):
            self.output(f"   ❌ Cannot reach Campfire at {url}")
            self.output()
            self.output("   Make sure Campfire is running:")
            self.output("     once list              # Check if deployed")
            self.output("     once deploy campfire   # Deploy if needed")
            self.output()
            if not self.terminal.confirm("Continue anyway?", default=False):
                sys.exit(1)
            return

        self.output("   ✅ Campfire is reachable")

        if check_campfire_setup_complete(url):
            self.output("   ✅ Campfire initial setup is complete")
        else:
            self.output("   ⚠️  Campfire initial setup may not be complete")
            self.output(f"      Open {url} to complete setup first")
            if not options.no_browser and self.terminal.confirm(
                "Open Campfire in browser to complete setup?", default=True
            ):
                webbrowser.open(url)
                self.output()
                self.terminal.prompt("Press Enter when setup is complete")

    def _get_bot_key(
        self,
        url: str,
        bot_key: str | None,
        room_id: str | None,
        options: WizardOptions,
    ) -> tuple[str, str | None]:
        """Get bot key from args, existing config, or user prompt."""
        # If bot_key provided via args, use it
        if bot_key:
            return bot_key, room_id

        # Check for existing config
        bot_key, room_id = self._check_existing_key(url, room_id)
        if bot_key:
            return bot_key, room_id

        # Guide user through bot creation
        self._guide_bot_creation(url, options)

        # Prompt for bot key
        return self._prompt_bot_key(room_id)

    def _check_existing_key(
        self, url: str, room_id: str | None
    ) -> tuple[str | None, str | None]:
        """Check for and validate existing bot key in config."""
        existing_config = load_config_yaml()
        existing_bot_key = (
            existing_config.get("channels", {}).get("campfire", {}).get("bot_key")
        )

        if not existing_bot_key or existing_bot_key == "${CAMPFIRE_BOT_KEY}":
            return None, room_id

        self.output()
        self.output(f"   Found existing bot key in config: {existing_bot_key[:10]}...")

        test_room = room_id or "1"
        success, result = check_bot_key(url, test_room, existing_bot_key)
        if not success:
            self.output(f"   ⚠️  Existing bot key doesn't work ({result})")
            return None, room_id

        self.output("   ✅ Existing bot key is valid!")
        if not self.terminal.confirm("Use existing bot key?", default=True):
            return None, room_id

        # Try to find a valid room if not specified
        if not room_id:
            found_room = find_valid_room(url, existing_bot_key)
            if found_room:
                room_id = found_room
                self.output(f"   ✅ Found valid room: {room_id}")

        return existing_bot_key, room_id

    def _guide_bot_creation(self, url: str, options: WizardOptions) -> None:
        """Display instructions for creating a bot in Campfire."""
        self.output()
        self._print_section("Step 1: Create a Bot in Campfire")
        self.output()
        self.output("You need to create a bot in Campfire's admin panel.")
        self.output()
        self.output("Bot settings to use:")
        self.output("  • Name: OpenPaws (or your preference)")
        self.output(
            f"  • Webhook URL: http://localhost:{self.state.config.webhook_port}/webhook"
        )
        self.output()

        bot_url = f"{url}/account/bots"
        if not options.no_browser and self.terminal.confirm(
            "Open Campfire bot settings in browser?", default=True
        ):
            self.output(f"   Opening {bot_url}...")
            webbrowser.open(bot_url)
            self.output()

        self.output(
            "After creating the bot, Campfire will show you curl commands like:"
        )
        self.output()
        self.output(f"  curl -d 'Hello!' {url}/rooms/1/YOUR-BOT-KEY/messages")
        self.output()

    def _prompt_bot_key(self, room_id: str | None) -> tuple[str, str | None]:
        """Prompt user for bot key (or curl command) and extract info."""
        self._print_section("Step 2: Enter Bot Information")
        self.output()
        self.output(
            "Paste one of the curl commands from Campfire, or just the bot key:"
        )
        self.output()

        user_input = self.terminal.prompt("Curl command or bot key").strip()

        parsed = parse_campfire_curl(user_input)
        if parsed:
            _, parsed_room_id, bot_key = parsed
            self.output(f"   ✓ Extracted bot key: {bot_key}")
            self.output(f"   ✓ Extracted room ID: {parsed_room_id}")
            return bot_key, room_id or parsed_room_id

        if "-" not in user_input:
            self.output(
                "   ⚠️  Bot key should be in format: ID-TOKEN (e.g., 2-rk2SGfi9lZW0)"
            )
        return user_input, room_id

    def _get_room_id(
        self, url: str, bot_key: str, room_id: str | None
    ) -> str:
        """Find or prompt for room ID."""
        if room_id:
            return room_id

        self.output()
        self.output("   Looking for available rooms...")
        found_room = find_valid_room(url, bot_key)
        if found_room:
            self.output(f"   ✅ Found room {found_room}")
            return found_room

        return self.terminal.prompt(
            "Default room ID (from Campfire URL /rooms/N)", default="1"
        )

    def _test_connection(
        self, url: str, room_id: str, bot_key: str, options: WizardOptions
    ) -> None:
        """Test connection and handle failures."""
        self.output()
        self._print_section("Testing Connection")
        self.output()
        self.output(f"Testing bot key with room {room_id}...")

        success, result = check_bot_key(url, room_id, bot_key)
        self.state.connection_tested = True

        if success:
            self.output(
                "   ✅ Connection successful! Check Campfire for the test message."
            )
            self.state.connection_success = True
            return

        self._handle_connection_failure(url, room_id, bot_key, result)

    def _handle_connection_failure(
        self, url: str, room_id: str, bot_key: str, result: str
    ) -> None:
        """Handle connection test failure."""
        if result == "invalid_key":
            self.output("   ❌ Bot key is invalid. Please check the key and try again.")
        elif result == "invalid_room":
            self.output(f"   ❌ Room {room_id} doesn't exist. Bot key is valid though!")
            self.output("   Looking for a valid room...")
            found_room = find_valid_room(url, bot_key)
            if found_room:
                self.state.config.room_id = found_room
                self.output(f"   ✅ Found valid room: {found_room}")
                retry_success, _ = check_bot_key(url, found_room, bot_key)
                if retry_success:
                    self.output("   ✅ Connection successful!")
                    self.state.connection_success = True
                    return
        else:
            self.output(f"   ❌ Connection failed: {result}")

        if not self.terminal.confirm("Continue with setup anyway?", default=False):
            self.output("Setup cancelled.")
            sys.exit(1)

    def _save_config(self) -> None:
        """Save Campfire configuration to config.yaml."""
        self.output()
        self._print_section("Step 4: Saving Configuration")
        self.output()

        config = load_config_yaml()
        if "channels" not in config:
            config["channels"] = {}

        cfg = self.state.config
        config["channels"]["campfire"] = {
            "base_url": cfg.url,
            "bot_key": cfg.bot_key,
            "webhook_port": cfg.webhook_port,
            "webhook_path": cfg.webhook_path,
        }

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
                "chat_id": cfg.room_id,
                "trigger": "@paw",
            }
            self.output(f"   Added group '{group_name}' for room {cfg.room_id}")

        save_config_yaml(config)
        self.output(f"   ✅ Configuration saved to: {get_config_file()}")

    def _print_summary(self) -> None:
        """Print final setup summary."""
        cfg = self.state.config
        self.output(
            _SUMMARY_TEMPLATE.format(
                sep="═" * 40,
                url=cfg.url,
                bot_key=cfg.bot_key,
                room_id=cfg.room_id,
                port=cfg.webhook_port,
            )
        )

    def _print_section(self, title: str) -> None:
        """Print a section header."""
        self.output("─" * 40)
        self.output(title)
        self.output("─" * 40)
