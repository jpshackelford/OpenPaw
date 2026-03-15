# Gmail Channel Adapter Setup

This guide explains how to set up the Gmail channel adapter for OpenPaws.

## Overview

The Gmail adapter supports two modes:

- **Channel Mode**: Polls your inbox for new messages and triggers the agent
- **Tool Mode**: Provides read/send/search capabilities for the agent to use

## Prerequisites

- A Google Cloud Platform (GCP) account
- A Gmail account
- Python 3.11 or later

## Step 1: Create a GCP Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select an existing one
3. Note your project ID

## Step 2: Enable Gmail API

1. In the Cloud Console, go to **APIs & Services > Library**
2. Search for "Gmail API"
3. Click **Enable**

## Step 3: Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Select **External** user type (or Internal if using Google Workspace)
3. Fill in required fields:
   - App name: "OpenPaws"
   - User support email: Your email
   - Developer contact: Your email
4. Add scopes:
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.modify`
5. Add your Gmail account as a test user

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Select **Desktop app** as application type
4. Name it "OpenPaws Desktop"
5. Click **Create**
6. Download the JSON file
7. Save it as `~/.openpaws/gmail_credentials.json`

## Step 5: Configure OpenPaws

Add the Gmail channel to your `~/.openpaws/config.yaml`:

```yaml
channels:
  gmail:
    credentials_file: ~/.openpaws/gmail_credentials.json
    mode: channel  # or "tool"
    poll_interval: 60  # seconds between inbox checks
    filter_label: openpaws  # optional: only process emails with this label
```

## Step 6: First Run Authorization

On first run, OpenPaws will:

1. Open a browser window for Google sign-in
2. Ask you to authorize the requested permissions
3. Save the OAuth token to `~/.openpaws/gmail_token.json`

Subsequent runs will use the saved token automatically.

## Configuration Options

| Option | Required | Default | Description |
|--------|----------|---------|-------------|
| `credentials_file` | Yes | - | Path to OAuth credentials JSON |
| `token_file` | No | `<credentials_dir>/gmail_token.json` | Path to save OAuth token |
| `mode` | No | `channel` | `channel` for inbox polling, `tool` for agent tools |
| `poll_interval` | No | `60` | Seconds between inbox polls (minimum 10) |
| `filter_label` | No | - | Only process emails with this Gmail label |

## Channel Mode

In channel mode, the adapter polls your inbox for unread messages:

```yaml
channels:
  gmail:
    credentials_file: ~/.openpaws/gmail_credentials.json
    mode: channel
    poll_interval: 60
    filter_label: openpaws  # recommended to avoid noise
```

**Recommended**: Create a Gmail filter that applies the `openpaws` label to emails you want the agent to process. This prevents the agent from responding to all your emails.

### How it Works

1. Every `poll_interval` seconds, the adapter checks for unread messages
2. Messages matching the `filter_label` (if set) are processed
3. Each message is sent to the agent as an incoming message
4. Agent responses are sent as reply emails in the same thread
5. Processed messages are marked as read

## Tool Mode

In tool mode, the adapter doesn't poll automatically. Instead, it provides methods for the agent to search and read emails on demand:

```yaml
channels:
  gmail:
    credentials_file: ~/.openpaws/gmail_credentials.json
    mode: tool
```

Available methods:
- `search_emails(query, max_results)` - Search emails using Gmail query syntax
- `get_email(message_id)` - Get a specific email by ID
- `send_message(message, subject)` - Send an email

## Troubleshooting

### "Access blocked" error
- Make sure you added your email as a test user in the OAuth consent screen
- Verify the app is not in "Testing" mode with limited users

### "Credentials file not found"
- Check the path to your credentials JSON file
- Use absolute paths or `~` for home directory

### Token expired
- Delete `~/.openpaws/gmail_token.json`
- Restart OpenPaws to re-authorize

### Rate limits
- Gmail API has quota limits (1 billion quota units per day)
- Normal usage won't hit these limits
- Keep `poll_interval` at 60 seconds or higher

## Security Considerations

- Store `credentials_file` securely (contains your OAuth secret)
- Never commit `gmail_credentials.json` or `gmail_token.json` to version control
- Use environment variables for paths in production:
  ```yaml
  credentials_file: ${GMAIL_CREDENTIALS_PATH}
  ```
- Consider using a separate Gmail account for OpenPaws

## References

- [Gmail API Python Quickstart](https://developers.google.com/gmail/api/quickstart/python)
- [Gmail API Reference](https://developers.google.com/gmail/api/reference/rest)
- [OAuth 2.0 for Desktop Apps](https://developers.google.com/identity/protocols/oauth2/native-app)
