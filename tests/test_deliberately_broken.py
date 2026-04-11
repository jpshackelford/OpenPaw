"""Deliberately broken tests to demonstrate quality report failure display.

This file will be removed after demonstrating the feature.
"""

import pytest


def test_this_will_fail_assertion():
    """This test fails with an assertion error."""
    expected = {"status": "success", "count": 42}
    actual = {"status": "failure", "count": 0}
    assert actual == expected, "API response did not match expected"


def test_this_will_fail_with_exception():
    """This test fails with an exception."""
    data = {"users": []}
    # This will raise KeyError
    first_user = data["users"][0]["name"]
    assert first_user == "Alice"


def test_another_failure():
    """Another failing test."""
    result = 10 / 2
    assert result == 6, f"Expected 6 but got {result}"


class TestBrokenClass:
    """A test class with failures."""

    def test_broken_in_class(self):
        """Test that fails inside a class."""
        items = [1, 2, 3]
        assert 5 in items, "Expected 5 to be in items"

    def test_timeout_simulation(self):
        """Simulates a timeout-like failure."""
        connected = False
        assert connected, "Connection timed out after 30 seconds"
