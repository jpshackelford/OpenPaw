"""Lightweight channel posting for direct message delivery.

This module provides simple HTTP POST capabilities for each supported
channel type. Unlike the full channel adapters, this only handles
outbound messages - no webhooks, no event loops.

Used by SendStatusExecutor when channel context is available, enabling
remote mode conversations to post status updates directly to channels.
"""

import html
import logging

import httpx
import markdown

logger = logging.getLogger(__name__)


async def post_to_channel(
    channel_type: str,
    channel_id: str,
    message: str,
    *,
    thread_id: str | None = None,
    base_url: str | None = None,
    credential: str,
) -> bool:
    """Post a message to a channel.

    Args:
        channel_type: Type of channel ("campfire", "slack", "telegram")
        channel_id: Room/channel ID to post to
        message: Message text to send
        thread_id: Thread ID for replies (optional)
        base_url: API base URL (required for Campfire)
        credential: API credential (bot key/token)

    Returns:
        True if successful, False otherwise.
    """
    if channel_type == "campfire":
        return await _post_to_campfire(
            base_url, credential, channel_id, message, thread_id
        )
    elif channel_type == "slack":
        return await _post_to_slack(credential, channel_id, message, thread_id)
    else:
        logger.warning(f"Unsupported channel type for direct posting: {channel_type}")
        return False


async def _campfire_request(url: str, html_content: str) -> bool:
    """Execute Campfire POST request."""
    headers = {"Content-Type": "text/html; charset=utf-8"}
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url, content=html_content, headers=headers, timeout=30
            )
            return resp.status_code == 201
    except Exception:
        return False


async def _post_to_campfire(  # length-ok
    base_url: str | None,
    bot_key: str,
    room_id: str,
    message: str,
    parent_id: str | None = None,
) -> bool:
    """Post a message to Campfire (converts markdown to HTML)."""
    if not base_url:
        logger.error("Campfire requires base_url")
        return False
    url = f"{base_url.rstrip('/')}/rooms/{room_id}/{bot_key}/messages"
    success = await _campfire_request(url, _markdown_to_html(message))
    if success:
        logger.debug(f"Posted to Campfire room {room_id}")
    else:
        logger.error(f"Campfire API error posting to room {room_id}")
    return success


def _markdown_to_html(text: str) -> str:
    """Convert markdown text to HTML for Campfire's ActionText rendering.

    Escapes HTML entities before conversion to prevent XSS attacks.
    """
    safe_text = html.escape(text)
    return markdown.markdown(
        safe_text,
        extensions=["fenced_code", "tables", "nl2br"],
    )


def _build_slack_payload(channel_id: str, message: str, thread_ts: str | None) -> dict:
    """Build Slack API payload."""
    payload = {"channel": channel_id, "text": message}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return payload


async def _slack_request(bot_token: str, payload: dict) -> dict | None:
    """Execute Slack API request."""
    url = "https://slack.com/api/chat.postMessage"
    headers = {
        "Authorization": f"Bearer {bot_token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers, timeout=30)
            return resp.json()
    except Exception:
        return None


async def _post_to_slack(  # length-ok
    bot_token: str, channel_id: str, message: str, thread_ts: str | None = None
) -> bool:
    """Post a message to Slack using the Web API."""
    payload = _build_slack_payload(channel_id, message, thread_ts)
    data = await _slack_request(bot_token, payload)
    if data and data.get("ok"):
        logger.debug(f"Posted to Slack channel {channel_id}")
        return True
    err = data.get("error", "unknown") if data else "request failed"
    logger.error(f"Slack API error: {err}")
    return False
