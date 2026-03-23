# iMessage Integration Setup Guide

This guide walks you through setting up iMessage integration for OpenPaws using **BlueBubbles**, a macOS application that bridges iMessage to external services via REST API and webhooks.

## Requirements

- **macOS** computer (Mac Mini, MacBook, or macOS VM) that stays on
- **Messages.app** signed into an Apple ID with iMessage enabled
- **BlueBubbles Server** installed on the Mac
- Network connectivity between OpenPaws and the Mac running BlueBubbles

## Overview

OpenPaws connects to iMessage through BlueBubbles:

```
┌─────────────┐      Webhook       ┌─────────────┐      Messages.app     ┌─────────────┐
│  OpenPaws   │◄──────────────────►│ BlueBubbles │◄────────────────────►│   iMessage  │
│   Server    │      REST API      │   Server    │        (macOS)       │   Network   │
└─────────────┘                    └─────────────┘                      └─────────────┘
```

| Component | Purpose |
|-----------|---------|
| **BlueBubbles Server** | Runs on Mac, provides REST API for sending messages |
| **Webhook** | BlueBubbles POSTs incoming messages to OpenPaws |
| **REST API** | OpenPaws calls to send messages, typing indicators, read receipts |

---

## Step 1: Set Up a Mac for BlueBubbles

### Option A: Dedicated Mac (Recommended)

Use a Mac Mini, old MacBook, or similar that can stay on 24/7:
- Sign into a dedicated Apple ID for your assistant
- Enable iMessage in Messages.app
- Disable sleep in System Settings > Energy Saver

### Option B: macOS VM

You can run macOS in a VM on compatible hardware:
- Use tools like UTM (Apple Silicon) or VMware (Intel)
- See [BlueBubbles VM Guide](https://docs.bluebubbles.app/server/running-a-macos-vm)

### Apple ID Considerations

- **Recommended**: Create a separate Apple ID for your assistant
- This keeps assistant conversations separate from your personal messages
- The assistant's phone number/email becomes the contact address others use

---

## Step 2: Install BlueBubbles Server

1. Download BlueBubbles from [bluebubbles.app](https://bluebubbles.app)
2. Install and launch the application
3. Grant required permissions when prompted:
   - **Full Disk Access** (for reading Messages database)
   - **Accessibility** (for sending messages via automation)
   - **Notifications** (optional, for alerts)

### Initial Configuration

1. In BlueBubbles settings, go to **API & Webhooks**
2. Enable the **REST API**
3. Set a **Password** (you'll need this for OpenPaws config)
4. Note the **Server URL** shown (e.g., `http://192.168.1.100:1234`)

---

## Step 3: Configure Network Access

BlueBubbles needs to be reachable from your OpenPaws server.

### Option A: Same Local Network

If OpenPaws runs on the same network:
```
Server URL: http://192.168.1.100:1234
```
No additional setup needed.

### Option B: Remote Access via Tunnel

For OpenPaws running elsewhere, use a tunnel:

**Cloudflare Tunnel (Recommended)**:
```bash
cloudflared tunnel --url http://localhost:1234
```

**ngrok**:
```bash
ngrok http 1234
```

The tunnel URL becomes your `server_url` in config.

---

## Step 4: Set Up Webhooks

BlueBubbles sends incoming messages to OpenPaws via webhooks.

1. In BlueBubbles, go to **API & Webhooks** > **Webhooks**
2. Click **Add Webhook**
3. Configure:
   - **URL**: `http://your-openpaws-server:8080/webhook?password=YOUR_PASSWORD`
   - **Events**: Select "New Message" (required), optionally add others
4. Click **Save**

### Webhook Security

- Always include the password in the webhook URL
- OpenPaws verifies the password before processing events
- Use HTTPS if exposing webhooks over the internet

---

## Step 5: Configure OpenPaws

Add the iMessage configuration to your `~/.openpaws/config.yaml`:

```yaml
channels:
  imessage:
    server_url: ${BLUEBUBBLES_SERVER_URL}
    password: ${BLUEBUBBLES_PASSWORD}
    webhook_port: 8080
    webhook_path: /webhook
    send_read_receipts: true
    send_typing_indicators: true

groups:
  personal:
    channel: imessage
    chat_id: "iMessage;-;+15551234567"  # Your phone number
    trigger: "hey paws"
```

Set environment variables:

```bash
export BLUEBUBBLES_SERVER_URL="http://192.168.1.100:1234"
export BLUEBUBBLES_PASSWORD="your-bluebubbles-password"
```

Or use a `.env` file:

```bash
# .env
BLUEBUBBLES_SERVER_URL=http://192.168.1.100:1234
BLUEBUBBLES_PASSWORD=your-bluebubbles-password
```

---

## Step 6: Security - Allowed Senders

For a personal assistant, restrict who can message the bot:

```yaml
channels:
  imessage:
    server_url: ${BLUEBUBBLES_SERVER_URL}
    password: ${BLUEBUBBLES_PASSWORD}
    allowed_senders:
      - "+15551234567"      # Your phone number
      - "+15559876543"      # Family member
      - "friend@icloud.com" # Friend's Apple ID
```

Messages from non-listed senders are ignored.

---

## Step 7: Start OpenPaws

```bash
openpaws start
```

Check the logs:

```bash
cat ~/.openpaws/logs/openpaws.log
```

You should see:
```
Connecting to BlueBubbles at http://192.168.1.100:1234
BlueBubbles connection verified
iMessage webhook server started on port 8080
iMessage adapter started
```

---

## Step 8: Test the Integration

1. From your phone, send an iMessage to the assistant's Apple ID
2. You should see the message logged and receive a response

### Finding Chat GUIDs

Chat GUIDs have these formats:
- **Direct messages**: `iMessage;-;+15551234567` or `iMessage;-;email@example.com`
- **Group chats**: `iMessage;+;chat123456789`

To find a specific chat GUID, check BlueBubbles logs or use the REST API:
```bash
curl "http://192.168.1.100:1234/api/v1/chat/query?password=YOUR_PASSWORD"
```

---

## Troubleshooting

### "Cannot connect to BlueBubbles"

1. Verify BlueBubbles is running on the Mac
2. Check the server URL is correct
3. Verify network connectivity: `curl http://192.168.1.100:1234/api/v1/ping?password=YOUR_PASSWORD`
4. Check firewall settings on the Mac

### "Webhook not receiving messages"

1. Verify webhook URL in BlueBubbles settings
2. Check the password matches
3. Ensure OpenPaws webhook server is running on the correct port
4. Test with: `curl -X POST "http://localhost:8080/webhook?password=YOUR_PASSWORD" -H "Content-Type: application/json" -d '{"type":"test"}'`

### "Messages not sending"

1. Verify Messages.app is signed in and working manually
2. Check BlueBubbles has Full Disk Access permission
3. Try sending via BlueBubbles directly to test
4. Check BlueBubbles server logs for errors

### "Typing/read receipts not working"

1. These require the BlueBubbles Private API
2. Enable Private API in BlueBubbles settings
3. Some features require macOS 13+ (Ventura)

### "Mac goes to sleep"

1. Disable sleep: System Settings > Energy Saver > Prevent sleep
2. Use a power source (not battery)
3. Consider using `caffeinate` command
4. Set up a LaunchAgent to keep Messages.app active (see below)

---

## Keeping Messages.app Active

For VMs or headless Macs, Messages.app may go idle. Use this LaunchAgent:

### 1. Create the AppleScript

Save to `~/Scripts/poke-messages.scpt`:

```applescript
try
  tell application "Messages"
    if not running then
      launch
    end if
    set _chatCount to (count of chats)
  end tell
on error
end try
```

### 2. Create the LaunchAgent

Save to `~/Library/LaunchAgents/com.openpaws.poke-messages.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.openpaws.poke-messages</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>-lc</string>
      <string>/usr/bin/osascript "$HOME/Scripts/poke-messages.scpt"</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>300</integer>
  </dict>
</plist>
```

### 3. Load the LaunchAgent

```bash
launchctl load ~/Library/LaunchAgents/com.openpaws.poke-messages.plist
```

---

## Configuration Reference

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `server_url` | string | required | BlueBubbles server URL |
| `password` | string | required | BlueBubbles API password |
| `webhook_port` | int | 8080 | Local port for webhook server |
| `webhook_path` | string | /webhook | Path for webhook endpoint |
| `allowed_senders` | list | null | Allowlist of phone numbers/emails |
| `send_read_receipts` | bool | true | Send read receipts |
| `send_typing_indicators` | bool | true | Send typing indicators |

---

## Security Best Practices

1. **Use a dedicated Apple ID** for the assistant
2. **Set allowed_senders** to restrict who can interact
3. **Use strong passwords** for BlueBubbles API
4. **Use HTTPS tunnels** for remote access
5. **Keep macOS and BlueBubbles updated**
6. **Don't expose BlueBubbles directly** to the internet without a tunnel

---

## Limitations

| Limitation | Notes |
|------------|-------|
| Requires Mac | No way to run iMessage on Linux/Windows |
| Always-on Mac | Mac must stay on and logged in |
| Apple ID needed | Separate Apple ID recommended for assistant |
| Some features macOS 13+ | Edit/unsend require Ventura or newer |

---

## Related Documentation

- [BlueBubbles Documentation](https://docs.bluebubbles.app)
- [BlueBubbles REST API](https://documenter.getpostman.com/view/765844/UV5RnfwM)
- [OpenPaws AGENTS.md](../AGENTS.md)
- [OpenPaws Slack Setup](SLACK_SETUP.md)
