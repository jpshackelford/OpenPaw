"""Tests for Campfire setup wizard."""

from unittest.mock import MagicMock, patch

from openpaws.channels.campfire_setup import (
    CampfireConfig,
    CampfireSetupWizard,
    WizardState,
    check_bot_key,
    check_campfire_reachable,
    check_campfire_setup_complete,
    find_valid_room,
    normalize_url,
    parse_campfire_curl,
)
from openpaws.terminal import MockTerminalInput


class TestCampfireConfig:
    """Tests for CampfireConfig dataclass."""

    def test_default_values(self):
        config = CampfireConfig()
        assert config.url == ""
        assert config.bot_key == ""
        assert config.room_id == ""
        assert config.webhook_port == 8765
        assert config.webhook_path == "/webhook"

    def test_custom_values(self):
        config = CampfireConfig(
            url="http://test.com",
            bot_key="abc-123",
            room_id="5",
            webhook_port=9000,
        )
        assert config.url == "http://test.com"
        assert config.bot_key == "abc-123"
        assert config.room_id == "5"
        assert config.webhook_port == 9000


class TestWizardState:
    """Tests for WizardState dataclass."""

    def test_default_values(self):
        state = WizardState()
        assert isinstance(state.config, CampfireConfig)
        assert state.connection_tested is False
        assert state.connection_success is False


class TestNormalizeUrl:
    """Tests for normalize_url function."""

    def test_adds_http_if_missing(self):
        assert normalize_url("example.com") == "http://example.com"

    def test_preserves_http(self):
        assert normalize_url("http://example.com") == "http://example.com"

    def test_preserves_https(self):
        assert normalize_url("https://example.com") == "https://example.com"

    def test_removes_trailing_slash(self):
        assert normalize_url("http://example.com/") == "http://example.com"

    def test_removes_multiple_trailing_slashes(self):
        assert normalize_url("http://example.com///") == "http://example.com"


class TestParseCampfireCurl:
    """Tests for parse_campfire_curl function."""

    def test_valid_curl_command(self):
        curl = "curl -d 'Hello' http://campfire.localhost/rooms/1/abc-123/messages"
        result = parse_campfire_curl(curl)
        assert result == ("http://campfire.localhost", "1", "abc-123")

    def test_https_url(self):
        curl = "curl -d 'test' https://chat.example.com/rooms/42/xyz-789/messages"
        result = parse_campfire_curl(curl)
        assert result == ("https://chat.example.com", "42", "xyz-789")

    def test_invalid_string(self):
        assert parse_campfire_curl("not a curl command") is None
        assert parse_campfire_curl("") is None
        assert parse_campfire_curl("curl http://example.com") is None


class TestCheckCampfireReachable:
    """Tests for check_campfire_reachable function."""

    def test_reachable_returns_true(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert check_campfire_reachable("http://test.com") is True

    def test_unreachable_returns_false(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection failed")
            assert check_campfire_reachable("http://test.com") is False


class TestCheckCampfireSetupComplete:
    """Tests for check_campfire_setup_complete function."""

    def test_setup_complete(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"<title>Sign in</title>"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert check_campfire_setup_complete("http://test.com") is True

    def test_setup_not_complete(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b"<title>Setup</title>"
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            assert check_campfire_setup_complete("http://test.com") is False


class TestTestBotKey:
    """Tests for check_bot_key function."""

    def test_success(self):
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 201
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response

            success, msg = check_bot_key("http://test.com", "1", "key")
            assert success is True
            assert msg == "success"

    def test_invalid_key(self):
        import urllib.error

        with patch("urllib.request.urlopen") as mock_urlopen:
            error = urllib.error.HTTPError("http://test.com", 302, "Redirect", {}, None)
            mock_urlopen.side_effect = error

            success, msg = check_bot_key("http://test.com", "1", "key")
            assert success is False
            assert msg == "invalid_key"

    def test_invalid_room(self):
        import urllib.error

        with patch("urllib.request.urlopen") as mock_urlopen:
            error = urllib.error.HTTPError(
                "http://test.com", 500, "Server Error", {}, None
            )
            mock_urlopen.side_effect = error

            success, msg = check_bot_key("http://test.com", "1", "key")
            assert success is False
            assert msg == "invalid_room"


class TestFindValidRoom:
    """Tests for find_valid_room function."""

    def test_finds_room(self):
        def mock_test(base_url, room_id, bot_key):
            return room_id == "3", "success" if room_id == "3" else "invalid_room"

        with patch(
            "openpaws.channels.campfire_setup.check_bot_key", side_effect=mock_test
        ):
            result = find_valid_room("http://test.com", "key")
            assert result == "3"

    def test_no_room_found(self):
        with patch(
            "openpaws.channels.campfire_setup.check_bot_key",
            return_value=(False, "invalid_room"),
        ):
            result = find_valid_room("http://test.com", "key", max_rooms=3)
            assert result is None

    def test_invalid_key_stops_search(self):
        call_count = [0]

        def mock_test(base_url, room_id, bot_key):
            call_count[0] += 1
            return False, "invalid_key"

        with patch(
            "openpaws.channels.campfire_setup.check_bot_key", side_effect=mock_test
        ):
            result = find_valid_room("http://test.com", "key", max_rooms=10)
            assert result is None
            assert call_count[0] == 1  # Stopped after first invalid_key


class TestCampfireSetupWizard:
    """Tests for CampfireSetupWizard class."""

    def test_wizard_with_all_args(self, monkeypatch, tmp_path):
        """Test wizard when all arguments are provided."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        # Empty terminal - no prompts needed when all args provided
        terminal = MockTerminalInput([])
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
        ):
            config = wizard.run(
                url="http://campfire.localhost",
                bot_key="abc-123",
                room_id="1",
                webhook_port=8765,
                no_browser=True,
            )

        assert config.url == "http://campfire.localhost"
        assert config.bot_key == "abc-123"
        assert config.room_id == "1"
        assert config.webhook_port == 8765

        # Config file should be saved
        config_file = tmp_path / "config.yaml"
        assert config_file.exists()

    def test_wizard_prompts_for_url(self, monkeypatch, tmp_path):
        """Test wizard prompts for URL when not provided."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        # With no_browser=True and setup complete, wizard needs:
        # 1. URL prompt (no url arg)
        # 2. Bot key prompt (no existing config)
        # 3. Room ID prompt (no room found)
        terminal = MockTerminalInput(
            [
                "http://mycamp.local",  # URL prompt
                "key-456",  # Bot key prompt
                "2",  # Room ID prompt
            ]
        )
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
            patch(
                "openpaws.channels.campfire_setup.find_valid_room", return_value=None
            ),
        ):
            config = wizard.run(no_browser=True)

        assert config.url == "http://mycamp.local"
        assert config.bot_key == "key-456"
        assert config.room_id == "2"

    def test_wizard_uses_existing_bot_key(self, monkeypatch, tmp_path):
        """Test wizard reuses existing valid bot key from config."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        # Pre-create config with bot key
        config_file = tmp_path / "config.yaml"
        config_file.write_text("channels:\n  campfire:\n    bot_key: existing-key\n")

        terminal = MockTerminalInput(
            [
                "y",  # Use existing bot key
            ]
        )
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
            patch("openpaws.channels.campfire_setup.find_valid_room", return_value="1"),
        ):
            config = wizard.run(
                url="http://test.com",
                no_browser=True,
            )

        assert config.bot_key == "existing-key"
        assert config.room_id == "1"

    def test_wizard_campfire_unreachable_continue(self, monkeypatch, tmp_path):
        """Test wizard when Campfire is unreachable but user continues."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        # Flow when unreachable:
        # 1. "Continue anyway?" - yes
        # 2. Bot key prompt (no existing config)
        # 3. Room ID prompt (no room found)
        # 4. "Continue after failed connection?" - yes
        terminal = MockTerminalInput(
            [
                "y",  # Continue anyway (unreachable)
                "bot-key",  # Bot key prompt
                "1",  # Room ID prompt
                "y",  # Continue after failed connection
            ]
        )
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=False,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(False, "connection_error"),
            ),
            patch(
                "openpaws.channels.campfire_setup.find_valid_room", return_value=None
            ),
        ):
            config = wizard.run(
                url="http://test.com",
                no_browser=True,
            )

        assert config.url == "http://test.com"
        assert config.bot_key == "bot-key"

    def test_wizard_connection_test_finds_room(self, monkeypatch, tmp_path):
        """Test wizard finds valid room when initial room fails."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        terminal = MockTerminalInput([])  # No prompts needed
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        # First call fails with invalid_room, retry succeeds
        call_count = [0]

        def mock_test(url, room, key):
            call_count[0] += 1
            if call_count[0] == 1:
                return False, "invalid_room"
            return True, "success"

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key", side_effect=mock_test
            ),
            patch("openpaws.channels.campfire_setup.find_valid_room", return_value="3"),
        ):
            config = wizard.run(
                url="http://test.com",
                bot_key="key",
                room_id="1",
                no_browser=True,
            )

        # Room should be updated to the valid one found
        assert config.room_id == "3"


class TestWizardWithCurlParsing:
    """Tests for wizard parsing curl commands."""

    def test_wizard_parses_curl_input(self, monkeypatch, tmp_path):
        """Test wizard extracts info from curl command input."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        curl_cmd = "curl -d 'Hi' http://camp.local/rooms/5/key-xyz/messages"
        # With no_browser=True and setup complete, wizard needs:
        # 1. Bot key prompt (curl command works here)
        terminal = MockTerminalInput(
            [
                curl_cmd,  # Paste curl command as bot key
            ]
        )
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
        ):
            config = wizard.run(
                url="http://camp.local",
                no_browser=True,
            )

        assert config.bot_key == "key-xyz"
        assert config.room_id == "5"


class TestWizardOutput:
    """Tests for wizard output formatting."""

    def test_wizard_prints_header(self, monkeypatch, tmp_path):
        """Test wizard prints header at start."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        terminal = MockTerminalInput([])
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
        ):
            wizard.run(
                url="http://test.com",
                bot_key="key",
                room_id="1",
                no_browser=True,
            )

        output = "\n".join(output_lines)
        assert "Campfire Setup Wizard" in output
        assert "Setup Complete" in output

    def test_wizard_prints_summary(self, monkeypatch, tmp_path):
        """Test wizard prints summary at end."""
        monkeypatch.setenv("OPENPAWS_DIR", str(tmp_path))

        terminal = MockTerminalInput([])
        output_lines = []

        wizard = CampfireSetupWizard(
            terminal=terminal, output=lambda x="": output_lines.append(x)
        )

        with (
            patch(
                "openpaws.channels.campfire_setup.check_campfire_reachable",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_campfire_setup_complete",
                return_value=True,
            ),
            patch(
                "openpaws.channels.campfire_setup.check_bot_key",
                return_value=(True, "success"),
            ),
        ):
            wizard.run(
                url="http://test.com",
                bot_key="my-key",
                room_id="42",
                webhook_port=9999,
                no_browser=True,
            )

        output = "\n".join(output_lines)
        assert "http://test.com" in output
        assert "my-key" in output
        assert "42" in output
        assert "9999" in output
