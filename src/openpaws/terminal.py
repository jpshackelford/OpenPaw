"""Terminal input abstraction for testable CLI interactions.

This module provides an abstraction layer for terminal input operations,
allowing CLI logic to be tested without requiring a real TTY.

Usage:
    # Production code
    terminal = RealTerminalInput()
    if terminal.confirm("Continue?"):
        name = terminal.prompt("Enter name")

    # Test code
    terminal = TestTerminalInput(["y", "John"])
    if terminal.confirm("Continue?"):  # Returns True
        name = terminal.prompt("Enter name")  # Returns "John"
"""

import sys
from typing import Protocol

import click


class TerminalInput(Protocol):
    """Protocol for terminal input operations.

    Implementations must provide methods for reading characters, lines,
    and handling yes/no confirmations and text prompts.
    """

    def read_char(self) -> str:
        """Read a single character from input.

        Returns:
            A single character string.
        """
        ...

    def read_line(self) -> str:
        """Read a line of input (until Enter is pressed).

        Returns:
            The input string (without trailing newline).
        """
        ...

    def confirm(self, prompt: str, default: bool = True) -> bool:
        """Prompt for yes/no confirmation.

        Args:
            prompt: The question to ask.
            default: The default value if user just presses Enter.

        Returns:
            True for yes, False for no.
        """
        ...

    def prompt(self, text: str, default: str = "") -> str:
        """Prompt for text input.

        Args:
            text: The prompt text to display.
            default: Default value if user just presses Enter.

        Returns:
            The user's input, or default if empty.
        """
        ...


def _parse_yes_no(ch: str, default: bool) -> bool:
    """Parse a character as yes/no response."""
    if ch in ("\r", "\n", ""):
        return default
    return {"y": True, "n": False}.get(ch.lower(), default)


def _handle_prompt_char(  # length-ok
    ch: str, result: list[str], echo: bool = True
) -> bool:
    """Handle a single character in prompt input.

    Args:
        ch: The character to handle.
        result: List to append printable characters to.
        echo: Whether to echo characters to terminal.

    Returns:
        True if input is complete (Enter pressed), False otherwise.

    Raises:
        KeyboardInterrupt: If Ctrl+C is pressed.
    """
    if ch in ("\r", "\n"):
        return True
    if ch == "\x7f" and result:  # Backspace
        result.pop()
        if echo:
            click.echo("\b \b", nl=False)
    elif ch == "\x03":  # Ctrl+C
        raise KeyboardInterrupt
    elif ch >= " " and ch != "\x7f":  # Printable character (exclude DEL)
        result.append(ch)
        if echo:
            click.echo(ch, nl=False)
    return False


class RealTerminalInput:
    """Real terminal input using termios/tty.

    This implementation uses raw terminal mode to read single characters
    and provides interactive prompts with proper echo handling.

    Note: This requires a real TTY and won't work with piped input
    or in non-interactive environments.
    """

    def read_char(self) -> str:
        """Read a single character using raw terminal mode."""
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def read_line(self) -> str:
        """Read a line using cbreak mode for character-by-character handling."""
        import termios
        import tty

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        result: list[str] = []
        try:
            tty.setcbreak(fd)
            while not _handle_prompt_char(sys.stdin.read(1), result, echo=True):
                pass
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        return "".join(result)

    def confirm(self, prompt: str, default: bool = True) -> bool:
        """Prompt for yes/no confirmation."""
        suffix = " [Y/n]: " if default else " [y/N]: "
        click.echo(prompt + suffix, nl=False)
        ch = self.read_char()
        click.echo()
        return _parse_yes_no(ch, default)

    def prompt(self, text: str, default: str = "") -> str:
        """Prompt for text input."""
        suffix = f" [{default}]: " if default else ": "
        click.echo(f"{text}{suffix}", nl=False)
        result = self.read_line()
        click.echo()
        return result or default


class MockTerminalInput:
    """Mock terminal input with pre-programmed responses for testing.

    This implementation allows tests to script terminal interactions
    without requiring a real TTY.

    Example:
        terminal = MockTerminalInput([
            "y",           # For first confirm()
            "n",           # For second confirm()
            "hello",       # For prompt()
        ])
    """

    def __init__(self, responses: list[str] | None = None):
        """Initialize with a list of responses.

        Args:
            responses: List of responses to return. Each call to
                read_char(), confirm(), or prompt() consumes one response.
        """
        self.responses = list(responses) if responses else []
        self.index = 0
        self.calls: list[tuple[str, dict]] = []  # Track calls for assertions

    def _next_response(self) -> str:
        """Get the next response, raising if exhausted."""
        if self.index >= len(self.responses):
            raise IndexError(
                f"TestTerminalInput exhausted: {self.index} calls made, "
                f"only {len(self.responses)} responses provided"
            )
        response = self.responses[self.index]
        self.index += 1
        return response

    def read_char(self) -> str:
        """Return the next single character from responses."""
        response = self._next_response()
        self.calls.append(("read_char", {}))
        # Return just the first character if a longer string was provided
        return response[0] if response else ""

    def read_line(self) -> str:
        """Return the next response as a complete line."""
        response = self._next_response()
        self.calls.append(("read_line", {}))
        return response

    def confirm(self, prompt: str, default: bool = True) -> bool:
        """Return yes/no based on next response."""
        response = self._next_response()
        self.calls.append(("confirm", {"prompt": prompt, "default": default}))
        return _parse_yes_no(response, default)

    def prompt(self, text: str, default: str = "") -> str:
        """Return the next response as prompt input."""
        response = self._next_response()
        self.calls.append(("prompt", {"text": text, "default": default}))
        return response or default

    def assert_exhausted(self) -> None:
        """Assert that all responses have been consumed."""
        remaining = len(self.responses) - self.index
        if remaining > 0:
            raise AssertionError(
                f"MockTerminalInput has {remaining} unused responses: "
                f"{self.responses[self.index :]}"
            )

    def reset(self, responses: list[str] | None = None) -> None:
        """Reset the terminal with new responses."""
        self.responses = list(responses) if responses else []
        self.index = 0
        self.calls.clear()


# Alias for backward compatibility (renamed to avoid pytest collection warning)
TestTerminalInput = MockTerminalInput
