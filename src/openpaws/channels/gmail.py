"""Gmail channel adapter supporting both tool and full channel modes.

This adapter connects to Gmail using OAuth2 credentials and can operate in two modes:
- Channel mode: Polls inbox for new messages that trigger the agent
- Tool mode: Provides read/send/search capabilities for the agent

Configuration:
    channels:
      gmail:
        credentials_file: ${GMAIL_CREDENTIALS}
        mode: channel  # or "tool"
        poll_interval: 60  # seconds
        filter_label: "openpaws"  # optional label filter

See docs/GMAIL_SETUP.md for detailed setup instructions.
"""

import asyncio
import base64
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import TYPE_CHECKING

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from openpaws.channels.base import (
    ChannelAdapter,
    IncomingMessage,
    MessageHandler,
    OutgoingMessage,
)

if TYPE_CHECKING:
    from googleapiclient.discovery import Resource

logger = logging.getLogger(__name__)

# Gmail API scopes
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]


@dataclass
class GmailConfig:
    """Configuration for Gmail adapter.

    Attributes:
        credentials_file: Path to OAuth2 credentials JSON file
        token_file: Path to store/retrieve OAuth tokens (optional)
        mode: Operating mode - "channel" for polling or "tool" for agent tools
        poll_interval: Seconds between inbox polls (channel mode only)
        filter_label: Only process emails with this label (optional)
    """

    credentials_file: str
    token_file: str | None = None
    mode: str = "channel"
    poll_interval: int = 60
    filter_label: str | None = None

    def _validate_mode(self) -> None:
        """Validate mode setting."""
        if self.mode not in ("channel", "tool"):
            raise ValueError(f"Gmail mode must be 'channel' or 'tool': {self.mode}")

    def _validate_poll_interval(self) -> None:
        """Validate poll_interval setting."""
        if self.poll_interval < 10:
            raise ValueError(f"poll_interval must be >= 10s: {self.poll_interval}")

    def _validate_credentials_file(self) -> None:
        """Validate credentials file exists."""
        if not Path(self.credentials_file).exists():
            raise ValueError(f"Credentials file not found: {self.credentials_file}")

    def __post_init__(self):
        self._validate_mode()
        self._validate_poll_interval()
        self._validate_credentials_file()


class GmailAdapter(ChannelAdapter):
    """Gmail channel adapter supporting polling and sending emails."""

    def __init__(self, config: GmailConfig):
        self._config = config
        self._service: Resource | None = None
        self._credentials: Credentials | None = None
        self._message_handler: MessageHandler | None = None
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._processed_ids: set[str] = set()
        self._last_poll_time: datetime | None = None

    @property
    def channel_type(self) -> str:
        return "gmail"

    def _get_token_path(self) -> Path:
        """Get path to token file."""
        if self._config.token_file:
            return Path(self._config.token_file)
        creds_path = Path(self._config.credentials_file)
        return creds_path.parent / "gmail_token.json"

    def _load_credentials(self) -> Credentials | None:
        """Load existing credentials from token file."""
        token_path = self._get_token_path()
        if token_path.exists():
            return Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        return None

    def _save_credentials(self, creds: Credentials) -> None:
        """Save credentials to token file."""
        token_path = self._get_token_path()
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        logger.debug(f"Saved Gmail credentials to {token_path}")

    def _refresh_credentials(self, creds: Credentials) -> Credentials:
        """Refresh expired credentials."""
        creds.refresh(Request())
        self._save_credentials(creds)
        return creds

    def _run_oauth_flow(self) -> Credentials:
        """Run OAuth2 flow for user authorization."""
        flow = InstalledAppFlow.from_client_secrets_file(
            self._config.credentials_file, GMAIL_SCOPES
        )
        creds = flow.run_local_server(port=0)
        self._save_credentials(creds)
        return creds

    def _authenticate(self) -> Credentials:  # length-ok
        """Authenticate with Gmail API, running OAuth flow if needed."""
        creds = self._load_credentials()

        if creds and creds.valid:
            return creds

        if creds and creds.expired and creds.refresh_token:
            logger.info("Refreshing expired Gmail credentials")
            return self._refresh_credentials(creds)

        logger.info("Running OAuth flow for Gmail authentication")
        return self._run_oauth_flow()

    def _build_service(self) -> "Resource":
        """Build Gmail API service."""
        self._credentials = self._authenticate()
        return build("gmail", "v1", credentials=self._credentials)

    def _build_raw_event(self, msg_data: dict, headers: dict) -> dict:
        """Build raw_event dict for IncomingMessage."""
        return {
            "id": msg_data["id"],
            "threadId": msg_data.get("threadId"),
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
        }

    def _extract_headers(self, msg_data: dict) -> dict:
        """Extract headers dict from message payload."""
        return {h["name"]: h["value"] for h in msg_data["payload"]["headers"]}

    def _create_incoming_message(self, msg_data: dict) -> IncomingMessage:  # length-ok
        """Convert Gmail message to IncomingMessage."""
        headers = self._extract_headers(msg_data)
        from_addr = headers.get("From", "")
        return IncomingMessage(
            channel_type=self.channel_type,
            channel_id=from_addr,
            user_id=from_addr,
            user_name=self._extract_sender_name(from_addr),
            text=self._extract_body(msg_data),
            thread_id=msg_data.get("threadId"),
            is_mention=True,
            is_dm=True,
            raw_event=self._build_raw_event(msg_data, headers),
        )

    def _extract_sender_name(self, from_header: str) -> str:
        """Extract display name from email From header."""
        if "<" in from_header:
            return from_header.split("<")[0].strip().strip('"')
        return from_header

    def _extract_body(self, msg_data: dict) -> str:
        """Extract plain text body from Gmail message."""
        payload = msg_data.get("payload", {})
        return self._extract_text_from_payload(payload)

    def _decode_body_data(self, data: str) -> str:
        """Decode base64-encoded body data."""
        return base64.urlsafe_b64decode(data).decode("utf-8") if data else ""

    def _extract_plain_text_from_parts(self, parts: list) -> str:
        """Extract plain text from message parts."""
        for part in parts:
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return self._decode_body_data(data)
        return ""

    def _extract_text_from_payload(self, payload: dict) -> str:
        """Recursively extract plain text from message payload."""
        if payload.get("mimeType") == "text/plain" and "body" in payload:
            return self._decode_body_data(payload["body"].get("data", ""))

        parts = payload.get("parts", [])
        text = self._extract_plain_text_from_parts(parts)
        if text:
            return text

        for part in parts:
            text = self._extract_text_from_payload(part)
            if text:
                return text
        return ""

    def _build_label_query(self) -> str:
        """Build Gmail search query based on config."""
        query_parts = ["is:unread"]
        if self._config.filter_label:
            query_parts.append(f"label:{self._config.filter_label}")
        return " ".join(query_parts)

    def _list_messages_sync(self, query: str) -> list[dict]:
        """Synchronous list messages call."""
        results = (
            self._service.users()
            .messages()
            .list(userId="me", q=query, maxResults=10)
            .execute()
        )
        return results.get("messages", [])

    async def _fetch_unread_messages(self) -> list[dict]:
        """Fetch unread messages from Gmail inbox."""
        if not self._service:
            return []
        query = self._build_label_query()
        return await asyncio.get_event_loop().run_in_executor(
            None, self._list_messages_sync, query
        )

    def _get_message_sync(self, msg_id: str) -> dict:
        """Synchronous get message call."""
        return (
            self._service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )

    async def _get_message_details(self, msg_id: str) -> dict | None:
        """Fetch full message details."""
        if not self._service:
            return None
        return await asyncio.get_event_loop().run_in_executor(
            None, self._get_message_sync, msg_id
        )

    async def _mark_as_read(self, msg_id: str) -> None:
        """Mark message as read by removing UNREAD label."""
        if not self._service:
            return

        def modify():
            self._service.users().messages().modify(
                userId="me", id=msg_id, body={"removeLabelIds": ["UNREAD"]}
            ).execute()

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, modify)

    async def _process_message(self, msg_id: str) -> None:
        """Process a single unread message."""
        if msg_id in self._processed_ids:
            return

        msg_data = await self._get_message_details(msg_id)
        if not msg_data:
            return

        self._processed_ids.add(msg_id)
        incoming = self._create_incoming_message(msg_data)
        logger.info(f"Processing email from {incoming.user_name}")

        if self._message_handler:
            response = await self._message_handler(incoming)
            if response:
                await self._send_reply(incoming, response)

        await self._mark_as_read(msg_id)

    async def _poll_once(self) -> None:
        """Run a single poll cycle."""
        messages = await self._fetch_unread_messages()
        for msg_ref in messages:
            await self._process_message(msg_ref["id"])
        self._last_poll_time = datetime.now(UTC)

    async def _poll_inbox(self) -> None:
        """Poll inbox for new messages."""
        while self._running:
            try:
                await self._poll_once()
            except Exception as e:
                logger.error(f"Error polling Gmail inbox: {e}")
            await asyncio.sleep(self._config.poll_interval)

    async def _send_reply(self, original: IncomingMessage, response_text: str) -> None:
        """Send a reply to an incoming message."""
        raw_event = original.raw_event
        subject = raw_event.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        message = OutgoingMessage(
            channel_id=original.user_id,  # Reply to sender
            text=response_text,
            thread_id=raw_event.get("threadId"),
        )
        await self.send_message(message, subject=subject)

    async def start(self, message_handler: MessageHandler) -> None:
        """Start the Gmail adapter."""
        if self._running:
            logger.warning("Gmail adapter already running")
            return

        self._message_handler = message_handler

        logger.info("Authenticating with Gmail API")
        loop = asyncio.get_event_loop()
        self._service = await loop.run_in_executor(None, self._build_service)

        self._running = True

        if self._config.mode == "channel":
            logger.info(
                f"Starting Gmail polling (interval={self._config.poll_interval}s)"
            )
            self._poll_task = asyncio.create_task(self._poll_inbox())
        else:
            logger.info("Gmail adapter started in tool mode (no polling)")

    async def stop(self) -> None:
        """Stop the Gmail adapter."""
        if not self._running:
            return

        logger.info("Stopping Gmail adapter")
        self._running = False

        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

        self._service = None
        self._message_handler = None

    def _build_mime_message(
        self, message: OutgoingMessage, subject: str
    ) -> MIMEText:
        """Build MIME message from OutgoingMessage."""
        mime_msg = MIMEText(message.text)
        mime_msg["to"] = message.channel_id
        mime_msg["subject"] = subject
        if message.thread_id:
            mime_msg["References"] = message.thread_id
            mime_msg["In-Reply-To"] = message.thread_id
        return mime_msg

    async def send_message(
        self, message: OutgoingMessage, subject: str = "OpenPaws Message"
    ) -> None:
        """Send an email message."""
        if not self._service:
            raise RuntimeError("Gmail adapter not started")

        mime_msg = self._build_mime_message(message, subject)
        raw = base64.urlsafe_b64encode(mime_msg.as_bytes()).decode()
        body = {"raw": raw}
        if message.thread_id:
            body["threadId"] = message.thread_id

        def send():
            self._service.users().messages().send(userId="me", body=body).execute()

        await asyncio.get_event_loop().run_in_executor(None, send)
        logger.info(f"Sent email to {message.channel_id}")

    def is_running(self) -> bool:
        return self._running

    # Tool mode methods for agent use
    async def _search_message_ids(self, query: str, max_results: int) -> list[dict]:
        """Search for message IDs matching query."""
        def search():
            results = (
                self._service.users()
                .messages()
                .list(userId="me", q=query, maxResults=max_results)
                .execute()
            )
            return results.get("messages", [])

        return await asyncio.get_event_loop().run_in_executor(None, search)

    async def search_emails(
        self, query: str, max_results: int = 10
    ) -> list[IncomingMessage]:
        """Search emails with Gmail query syntax. Available in tool mode."""
        if not self._service:
            raise RuntimeError("Gmail adapter not started")

        messages = await self._search_message_ids(query, max_results)
        results = []
        for msg_ref in messages:
            msg_data = await self._get_message_details(msg_ref["id"])
            if msg_data:
                results.append(self._create_incoming_message(msg_data))
        return results

    async def get_email(self, message_id: str) -> IncomingMessage | None:
        """Get a specific email by ID. Available in tool mode."""
        msg_data = await self._get_message_details(message_id)
        if msg_data:
            return self._create_incoming_message(msg_data)
        return None


def create_gmail_adapter(
    credentials_file: str,
    mode: str = "channel",
    poll_interval: int = 60,
    filter_label: str | None = None,
    token_file: str | None = None,
) -> GmailAdapter:
    """Create a Gmail adapter with the given configuration.

    Args:
        credentials_file: Path to OAuth2 credentials JSON file
        mode: Operating mode - "channel" or "tool"
        poll_interval: Seconds between inbox polls (channel mode)
        filter_label: Only process emails with this label
        token_file: Path to store OAuth tokens (optional)

    Returns:
        Configured GmailAdapter instance
    """
    config = GmailConfig(
        credentials_file=credentials_file,
        token_file=token_file,
        mode=mode,
        poll_interval=poll_interval,
        filter_label=filter_label,
    )
    return GmailAdapter(config)


async def _echo_handler(msg: IncomingMessage) -> str | None:
    """Echo handler for standalone testing."""
    logger.info(f"Email from {msg.user_id}: {msg.text[:100]}")
    return "Echo: received your email"


async def run_gmail_adapter_standalone(  # length-ok
    credentials_file: str,
    poll_interval: int = 60,
    filter_label: str | None = None,
) -> None:
    """Run Gmail adapter standalone for testing."""
    adapter = create_gmail_adapter(
        credentials_file=credentials_file,
        mode="channel",
        poll_interval=poll_interval,
        filter_label=filter_label,
    )
    try:
        await adapter.start(_echo_handler)
        while adapter.is_running():
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down")
    finally:
        await adapter.stop()
