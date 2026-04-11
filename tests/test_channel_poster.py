"""Tests for channel_poster module - direct channel posting functionality."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openpaws.tools.channel_poster import (
    _markdown_to_html,
    _post_to_campfire,
    _post_to_slack,
    post_to_channel,
)


class TestMarkdownToHtml:
    """Tests for _markdown_to_html helper."""

    def test_basic_markdown(self):
        """Test basic markdown conversion."""
        result = _markdown_to_html("**bold** and *italic*")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result

    def test_code_block(self):
        """Test fenced code block conversion."""
        markdown = "```python\nprint('hello')\n```"
        result = _markdown_to_html(markdown)
        assert "code" in result
        assert "print" in result

    def test_newline_to_br(self):
        """Test nl2br extension converts newlines to br."""
        result = _markdown_to_html("line1\nline2")
        assert "<br" in result

    def test_html_escaping(self):
        """Test that HTML entities are escaped to prevent XSS."""
        malicious = "<script>alert('xss')</script>"
        result = _markdown_to_html(malicious)
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_table_markdown(self):
        """Test table markdown conversion."""
        table = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = _markdown_to_html(table)
        assert "<table>" in result


class TestPostToChannel:
    """Tests for post_to_channel dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatch_to_campfire(self):
        """Test dispatching to Campfire channel."""
        with patch(
            "openpaws.tools.channel_poster._post_to_campfire", new_callable=AsyncMock
        ) as mock_campfire:
            mock_campfire.return_value = True

            result = await post_to_channel(
                channel_type="campfire",
                channel_id="room123",
                message="Hello!",
                base_url="https://example.37signals.com",
                credential="bot-key-123",
            )

            assert result is True
            mock_campfire.assert_called_once_with(
                "https://example.37signals.com",
                "bot-key-123",
                "room123",
                "Hello!",
                None,  # thread_id
            )

    @pytest.mark.asyncio
    async def test_dispatch_to_slack(self):
        """Test dispatching to Slack channel."""
        with patch(
            "openpaws.tools.channel_poster._post_to_slack", new_callable=AsyncMock
        ) as mock_slack:
            mock_slack.return_value = True

            result = await post_to_channel(
                channel_type="slack",
                channel_id="C12345678",
                message="Hello Slack!",
                thread_id="1234567890.123456",
                credential="xoxb-token",
            )

            assert result is True
            mock_slack.assert_called_once_with(
                "xoxb-token",
                "C12345678",
                "Hello Slack!",
                "1234567890.123456",
            )

    @pytest.mark.asyncio
    async def test_unsupported_channel_type(self):
        """Test that unsupported channel types return False."""
        result = await post_to_channel(
            channel_type="telegram",
            channel_id="chat123",
            message="Hello!",
            credential="bot-token",
        )

        assert result is False


class TestPostToCampfire:
    """Tests for _post_to_campfire."""

    @pytest.mark.asyncio
    async def test_successful_post(self):
        """Test successful Campfire post."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 201
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            mock_client.return_value = mock_context

            result = await _post_to_campfire(
                base_url="https://example.37signals.com",
                bot_key="bot-key-123",
                room_id="room456",
                message="Hello Campfire!",
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_missing_base_url(self):
        """Test Campfire post fails without base_url."""
        result = await _post_to_campfire(
            base_url=None,
            bot_key="bot-key-123",
            room_id="room456",
            message="Hello!",
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_api_error(self):
        """Test Campfire API error handling."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            mock_client.return_value = mock_context

            result = await _post_to_campfire(
                base_url="https://example.37signals.com",
                bot_key="bot-key-123",
                room_id="room456",
                message="Hello!",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_network_exception(self):
        """Test Campfire network exception handling."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("Connection failed")
            )
            mock_client.return_value = mock_context

            result = await _post_to_campfire(
                base_url="https://example.37signals.com",
                bot_key="bot-key-123",
                room_id="room456",
                message="Hello!",
            )

            assert result is False


class TestPostToSlack:
    """Tests for _post_to_slack."""

    @pytest.mark.asyncio
    async def test_successful_post(self):
        """Test successful Slack post."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            mock_client.return_value = mock_context

            result = await _post_to_slack(
                bot_token="xoxb-token-123",
                channel_id="C12345678",
                message="Hello Slack!",
            )

            assert result is True

    @pytest.mark.asyncio
    async def test_post_with_thread(self):
        """Test Slack post with thread_ts."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_context = AsyncMock()
            post_mock = AsyncMock(return_value=mock_response)
            mock_context.__aenter__.return_value.post = post_mock
            mock_client.return_value = mock_context

            result = await _post_to_slack(
                bot_token="xoxb-token-123",
                channel_id="C12345678",
                message="Reply!",
                thread_ts="1234567890.123456",
            )

            assert result is True
            # Verify thread_ts was included in payload
            call_kwargs = post_mock.call_args
            payload = call_kwargs.kwargs["json"]
            assert payload["thread_ts"] == "1234567890.123456"

    @pytest.mark.asyncio
    async def test_api_error(self):
        """Test Slack API error handling."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": False, "error": "channel_not_found"}
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                return_value=mock_response
            )
            mock_client.return_value = mock_context

            result = await _post_to_slack(
                bot_token="xoxb-token-123",
                channel_id="INVALID",
                message="Hello!",
            )

            assert result is False

    @pytest.mark.asyncio
    async def test_network_exception(self):
        """Test Slack network exception handling."""
        with patch("openpaws.tools.channel_poster.httpx.AsyncClient") as mock_client:
            mock_context = AsyncMock()
            mock_context.__aenter__.return_value.post = AsyncMock(
                side_effect=Exception("Network error")
            )
            mock_client.return_value = mock_context

            result = await _post_to_slack(
                bot_token="xoxb-token-123",
                channel_id="C12345678",
                message="Hello!",
            )

            assert result is False
