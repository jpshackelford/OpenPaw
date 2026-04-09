"""Tests for terminal input abstraction layer."""

import sys

import pytest

from openpaws.terminal import (
    MockTerminalInput,
    RealTerminalInput,
    _handle_prompt_char,
    _parse_yes_no,
)


class TestParseYesNo:
    """Tests for _parse_yes_no function."""

    def test_yes_lowercase(self):
        assert _parse_yes_no("y", default=False) is True

    def test_yes_uppercase(self):
        assert _parse_yes_no("Y", default=False) is True

    def test_no_lowercase(self):
        assert _parse_yes_no("n", default=True) is False

    def test_no_uppercase(self):
        assert _parse_yes_no("N", default=True) is False

    def test_carriage_return_uses_default(self):
        assert _parse_yes_no("\r", default=True) is True
        assert _parse_yes_no("\r", default=False) is False

    def test_newline_uses_default(self):
        assert _parse_yes_no("\n", default=True) is True
        assert _parse_yes_no("\n", default=False) is False

    def test_empty_uses_default(self):
        assert _parse_yes_no("", default=True) is True
        assert _parse_yes_no("", default=False) is False

    def test_invalid_char_uses_default(self):
        assert _parse_yes_no("x", default=True) is True
        assert _parse_yes_no("x", default=False) is False
        assert _parse_yes_no("?", default=True) is True


class TestHandlePromptChar:
    """Tests for _handle_prompt_char function."""

    def test_enter_returns_true(self):
        result = []
        assert _handle_prompt_char("\r", result, echo=False) is True
        assert _handle_prompt_char("\n", result, echo=False) is True

    def test_ctrl_c_raises_keyboard_interrupt(self):
        result = []
        with pytest.raises(KeyboardInterrupt):
            _handle_prompt_char("\x03", result, echo=False)

    def test_printable_appends_to_result(self):
        result = []
        assert _handle_prompt_char("a", result, echo=False) is False
        assert result == ["a"]
        assert _handle_prompt_char("B", result, echo=False) is False
        assert result == ["a", "B"]
        assert _handle_prompt_char("1", result, echo=False) is False
        assert result == ["a", "B", "1"]

    def test_backspace_removes_last_char(self):
        result = ["a", "b", "c"]
        assert _handle_prompt_char("\x7f", result, echo=False) is False
        assert result == ["a", "b"]

    def test_backspace_on_empty_does_nothing(self):
        result = []
        assert _handle_prompt_char("\x7f", result, echo=False) is False
        assert result == []

    def test_del_char_excluded(self):
        """Test that DEL character (0x7f) doesn't get added as printable."""
        result = []
        _handle_prompt_char("\x7f", result, echo=False)
        # DEL on empty should not add anything
        assert result == []

    def test_space_is_printable(self):
        result = []
        assert _handle_prompt_char(" ", result, echo=False) is False
        assert result == [" "]


class TestMockTerminalInput:
    """Tests for MockTerminalInput class."""

    def test_read_char_returns_first_char(self):
        terminal = MockTerminalInput(["hello", "world"])
        assert terminal.read_char() == "h"
        assert terminal.read_char() == "w"

    def test_read_line_returns_full_response(self):
        terminal = MockTerminalInput(["hello", "world"])
        assert terminal.read_line() == "hello"
        assert terminal.read_line() == "world"

    def test_confirm_yes(self):
        terminal = MockTerminalInput(["y"])
        assert terminal.confirm("Continue?") is True

    def test_confirm_no(self):
        terminal = MockTerminalInput(["n"])
        assert terminal.confirm("Continue?") is False

    def test_confirm_default_true(self):
        terminal = MockTerminalInput([""])
        assert terminal.confirm("Continue?", default=True) is True

    def test_confirm_default_false(self):
        terminal = MockTerminalInput([""])
        assert terminal.confirm("Continue?", default=False) is False

    def test_prompt_returns_response(self):
        terminal = MockTerminalInput(["John"])
        assert terminal.prompt("Enter name") == "John"

    def test_prompt_empty_uses_default(self):
        terminal = MockTerminalInput([""])
        assert terminal.prompt("Enter name", default="Anonymous") == "Anonymous"

    def test_calls_are_tracked(self):
        terminal = MockTerminalInput(["y", "test input"])
        terminal.confirm("Question?", default=True)
        terminal.prompt("Input:", default="default")

        assert len(terminal.calls) == 2
        assert terminal.calls[0] == (
            "confirm",
            {"prompt": "Question?", "default": True},
        )
        assert terminal.calls[1] == ("prompt", {"text": "Input:", "default": "default"})

    def test_exhausted_raises_error(self):
        terminal = MockTerminalInput(["only one"])
        terminal.read_line()

        with pytest.raises(IndexError) as exc_info:
            terminal.read_line()

        assert "exhausted" in str(exc_info.value).lower()

    def test_assert_exhausted_passes_when_all_used(self):
        terminal = MockTerminalInput(["a", "b"])
        terminal.read_line()
        terminal.read_line()
        terminal.assert_exhausted()  # Should not raise

    def test_assert_exhausted_fails_with_unused(self):
        terminal = MockTerminalInput(["a", "b", "c"])
        terminal.read_line()

        with pytest.raises(AssertionError) as exc_info:
            terminal.assert_exhausted()

        assert "unused" in str(exc_info.value).lower()

    def test_reset_clears_state(self):
        terminal = MockTerminalInput(["a"])
        terminal.read_line()

        terminal.reset(["x", "y"])

        assert terminal.read_line() == "x"
        assert terminal.read_line() == "y"
        assert terminal.index == 2
        assert len(terminal.calls) == 2

    def test_reset_clears_calls(self):
        terminal = MockTerminalInput(["a"])
        terminal.confirm("test")
        assert len(terminal.calls) == 1

        terminal.reset(["b"])
        assert len(terminal.calls) == 0

    def test_empty_responses_list(self):
        terminal = MockTerminalInput([])

        with pytest.raises(IndexError):
            terminal.read_char()

    def test_none_responses_treated_as_empty(self):
        terminal = MockTerminalInput(None)
        assert terminal.responses == []


class TestMockTerminalInputIntegration:
    """Integration tests showing typical usage patterns."""

    def test_wizard_flow_simulation(self):
        """Simulate a typical wizard flow."""
        terminal = MockTerminalInput(
            [
                "y",  # First confirm
                "http://example.com",  # URL prompt
                "n",  # Second confirm (don't open browser)
                "bot-key-123",  # Bot key prompt
                "1",  # Room ID prompt
            ]
        )

        # Simulate wizard steps
        assert terminal.confirm("Check status?") is True
        url = terminal.prompt("Enter URL")
        assert url == "http://example.com"
        assert terminal.confirm("Open browser?") is False
        bot_key = terminal.prompt("Enter bot key")
        assert bot_key == "bot-key-123"
        room = terminal.prompt("Enter room")
        assert room == "1"

        terminal.assert_exhausted()

    def test_mixed_operations(self):
        """Test mixing different terminal operations."""
        terminal = MockTerminalInput(["abc", "y", "test"])

        # read_char takes first char of first response
        assert terminal.read_char() == "a"
        # confirm uses next response
        assert terminal.confirm("Continue?") is True
        # prompt uses next response
        assert terminal.prompt("Input") == "test"


class TestRealTerminalInput:
    """Tests for RealTerminalInput by mocking low-level terminal operations.

    These tests verify that RealTerminalInput correctly:
    - Saves and restores terminal settings
    - Uses appropriate terminal modes (raw/cbreak)
    - Reads from stdin correctly
    - Integrates with helper functions (_parse_yes_no, _handle_prompt_char)

    Note: We mock termios/tty modules in sys.modules before importing RealTerminalInput
    methods, which allows us to test the actual code paths.
    """

    def test_read_char_basic(self, monkeypatch):
        """Test read_char returns the character read from stdin."""
        from unittest.mock import MagicMock

        # Create mock modules
        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "x"

        # Patch
        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.read_char()

        assert result == "x"
        mock_stdin.read.assert_called_with(1)
        mock_tty.setraw.assert_called_once()
        mock_termios.tcgetattr.assert_called_once()
        mock_termios.tcsetattr.assert_called_once()

    def test_read_line_basic(self, monkeypatch):
        """Test read_line returns accumulated characters until Enter."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.side_effect = ["h", "i", "\r"]

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.read_line()

        assert result == "hi"
        mock_tty.setcbreak.assert_called_once()

    def test_read_line_with_backspace(self, monkeypatch):
        """Test read_line handles backspace correctly."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        # Type "ab", backspace, "c", Enter -> "ac"
        mock_stdin.read.side_effect = ["a", "b", "\x7f", "c", "\r"]

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.read_line()

        assert result == "ac"

    def test_confirm_yes(self, monkeypatch):
        """Test confirm returns True for 'y'."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "y"

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        assert terminal.confirm("Continue?") is True

    def test_confirm_no(self, monkeypatch):
        """Test confirm returns False for 'n'."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "n"

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        assert terminal.confirm("Continue?") is False

    def test_confirm_default_on_enter(self, monkeypatch):
        """Test confirm uses default on Enter."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "\r"

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        assert terminal.confirm("Continue?", default=True) is True
        assert terminal.confirm("Continue?", default=False) is False

    def test_prompt_returns_input(self, monkeypatch):
        """Test prompt returns user input."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.side_effect = ["t", "e", "s", "t", "\r"]

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.prompt("Enter value")

        assert result == "test"

    def test_prompt_empty_uses_default(self, monkeypatch):
        """Test prompt returns default on empty input."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.return_value = "\r"

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.prompt("Enter value", default="fallback")

        assert result == "fallback"

    def test_read_char_restores_settings_on_error(self, monkeypatch):
        """Test terminal settings are restored even on read error."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.side_effect = OSError("read error")

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        with pytest.raises(IOError):
            terminal.read_char()

        # Settings should still be restored via finally block
        mock_termios.tcsetattr.assert_called_once()


class TestRealTerminalInputTTYFallback:
    """Tests for RealTerminalInput TTY fallback behavior."""

    def test_is_tty_returns_false_for_non_tty(self, monkeypatch):
        """Test _is_tty returns False when stdin is not a TTY."""
        from unittest.mock import MagicMock

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        assert terminal._is_tty() is False

    def test_is_tty_returns_true_for_tty(self, monkeypatch):
        """Test _is_tty returns True when stdin is a TTY."""
        from unittest.mock import MagicMock

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        assert terminal._is_tty() is True

    def test_read_char_fallback_returns_first_char(self, monkeypatch):
        """Test _read_char_fallback returns first char of input."""
        monkeypatch.setattr("builtins.input", lambda: "hello")

        terminal = RealTerminalInput()
        assert terminal._read_char_fallback() == "h"

    def test_read_char_fallback_empty_returns_newline(self, monkeypatch):
        """Test _read_char_fallback returns newline for empty input."""
        monkeypatch.setattr("builtins.input", lambda: "")

        terminal = RealTerminalInput()
        assert terminal._read_char_fallback() == "\n"

    def test_read_char_fallback_eof_returns_newline(self, monkeypatch):
        """Test _read_char_fallback returns newline on EOF."""

        def raise_eof():
            raise EOFError()

        monkeypatch.setattr("builtins.input", raise_eof)

        terminal = RealTerminalInput()
        assert terminal._read_char_fallback() == "\n"

    def test_read_line_fallback_returns_input(self, monkeypatch):
        """Test _read_line_fallback returns full line."""
        monkeypatch.setattr("builtins.input", lambda: "test line")

        terminal = RealTerminalInput()
        assert terminal._read_line_fallback() == "test line"

    def test_read_line_fallback_eof_returns_empty(self, monkeypatch):
        """Test _read_line_fallback returns empty string on EOF."""

        def raise_eof():
            raise EOFError()

        monkeypatch.setattr("builtins.input", raise_eof)

        terminal = RealTerminalInput()
        assert terminal._read_line_fallback() == ""

    def test_read_char_uses_fallback_when_not_tty(self, monkeypatch):
        """Test read_char uses fallback when stdin is not a TTY."""
        from unittest.mock import MagicMock

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        monkeypatch.setattr(sys, "stdin", mock_stdin)
        monkeypatch.setattr("builtins.input", lambda: "abc")

        terminal = RealTerminalInput()
        result = terminal.read_char()

        assert result == "a"
        # Should not try to use termios (no fileno call for terminal ops)

    def test_read_line_uses_fallback_when_not_tty(self, monkeypatch):
        """Test read_line uses fallback when stdin is not a TTY."""
        from unittest.mock import MagicMock

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = False
        monkeypatch.setattr(sys, "stdin", mock_stdin)
        monkeypatch.setattr("builtins.input", lambda: "test input")

        terminal = RealTerminalInput()
        result = terminal.read_line()

        assert result == "test input"

    def test_read_line_cbreak_is_called_for_tty(self, monkeypatch):
        """Test _read_line_cbreak is used when stdin is a TTY."""
        from unittest.mock import MagicMock

        mock_termios = MagicMock()
        mock_termios.tcgetattr.return_value = ["settings"]
        mock_termios.TCSADRAIN = 1

        mock_tty = MagicMock()

        mock_stdin = MagicMock()
        mock_stdin.isatty.return_value = True
        mock_stdin.fileno.return_value = 0
        mock_stdin.read.side_effect = ["t", "e", "s", "t", "\r"]

        monkeypatch.setitem(sys.modules, "termios", mock_termios)
        monkeypatch.setitem(sys.modules, "tty", mock_tty)
        monkeypatch.setattr(sys, "stdin", mock_stdin)

        terminal = RealTerminalInput()
        result = terminal.read_line()

        assert result == "test"
        mock_tty.setcbreak.assert_called_once()
