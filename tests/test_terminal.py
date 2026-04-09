"""Tests for terminal input abstraction layer."""

import pytest

from openpaws.terminal import (
    MockTerminalInput,
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
            "confirm", {"prompt": "Question?", "default": True}
        )
        assert terminal.calls[1] == (
            "prompt", {"text": "Input:", "default": "default"}
        )

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
        terminal = MockTerminalInput([
            "y",                    # First confirm
            "http://example.com",   # URL prompt
            "n",                    # Second confirm (don't open browser)
            "bot-key-123",          # Bot key prompt
            "1",                    # Room ID prompt
        ])

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
