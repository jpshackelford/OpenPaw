# Campfire Integration Setup Guide

This guide walks you through setting up Campfire integration for OpenPaws. The integration uses **webhooks** to receive messages and the Campfire **Bot API** to send responses.

## Requirements

- A self-hosted [Campfire](https://once.com/campfire) instance (Basecamp ONCE product)
- Admin access to create bots in Campfire
- Network access between OpenPaws and your Campfire instance

## Overview

Campfire bots work via webhooks:

1. **You create a bot** in Campfire admin with a webhook URL pointing to OpenPaws
2. **When someone @mentions your bot** (or DMs it), Campfire POSTs to your webhook
3. **OpenPaws processes the message** and can respond directly or via the API

Since Campfire is self-hosted, the webhook setup is typically straightforward — both services are often on the same machine or network.

---

## Step 1: Create a Bot in Campfire

1. Log in to Campfire as an admin
2. Go to **Account** → **Bots**
3. Click **"New bot"**
4. Fill in the bot details:
   - **Name**: `OpenPaws` (or whatever you prefer)
   - **Webhook URL**: `http://localhost:8765/webhook` (adjust for your setup)
5. Click **"Create bot"**
6. **Copy the bot key** — this is your `CAMPFIRE_BOT_KEY` (format: `123-abc123xyz`)

---

## Step 2: Determine Your Webhook URL

The webhook URL depends on how you're running OpenPaws relative to Campfire:

| Setup | Webhook URL |
|-------|-------------|
| Both on same machine | `http://localhost:8765/webhook` |
| Campfire in Docker, OpenPaws on host | `http://host.docker.internal:8765/webhook` |
| Same Docker Compose network | `http://openpaws:8765/webhook` |
| Same LAN (OpenPaws at 192.168.1.50) | `http://192.168.1.50:8765/webhook` |
| OpenPaws in Docker, Campfire on host | `http://host.docker.internal:8765/webhook` |

**Note:** The default webhook port is `8765` and path is `/webhook`, but both are configurable.

---

## Step 3: Configure OpenPaws

Add the Campfire configuration to your `~/.openpaws/config.yaml`:

```yaml
channels:
  campfire:
    base_url: ${CAMPFIRE_URL}
    bot_key: ${CAMPFIRE_BOT_KEY}
    webhook_port: 8765        # Optional, default: 8765
    webhook_path: /webhook    # Optional, default: /webhook

groups:
  team-chat:
    channel: campfire
    chat_id: "1"              # Campfire room ID
    trigger: "@paw"
```

Set the environment variables:

```bash
export CAMPFIRE_URL="http://localhost:3000"  # Your Campfire instance URL
export CAMPFIRE_BOT_KEY="123-abc123xyz456"   # From Step 1
```

Or add them to a `.env` file (not committed to version control):

```bash
# .env
CAMPFIRE_URL=http://localhost:3000
CAMPFIRE_BOT_KEY=123-abc123xyz456
```

---

## Step 4: Find Your Room ID

To find the room ID for your `chat_id` configuration:

1. Open Campfire in your browser
2. Navigate to the room
3. Look at the URL: `https://your-campfire.com/rooms/42`
4. The number (`42`) is your room ID

---

## Step 5: Add the Bot to Rooms

Before the bot can receive messages in a room:

1. The bot must be added as a member of the room
2. Go to the room's settings in Campfire
3. Add your bot to the room's members

For direct messages, users can message the bot directly without any room setup.

---

## Step 6: Start OpenPaws

```bash
openpaws start
```

Check the logs:

```bash
cat ~/.openpaws/logs/openpaws.log
```

You should see:
```
Campfire adapter started - webhook listening on http://0.0.0.0:8765/webhook
```

---

## Step 7: Update Bot Webhook URL (If Needed)

If you started OpenPaws on a different port or path than initially configured:

1. Go back to Campfire **Account** → **Bots**
2. Edit your bot
3. Update the webhook URL to match your OpenPaws configuration

---

## Testing the Integration

1. **Test the health endpoint:**
   ```bash
   curl http://localhost:8765/health
   # Should return: OK
   ```

2. **Test @mention:** In a room where the bot is a member, type `@OpenPaws hello`

3. **Test DM:** Open a direct message with your bot and send a message

The bot should respond to both.

---

## How It Works

### Message Flow

```
┌──────────────┐                    ┌──────────────┐
│   Campfire   │                    │   OpenPaws   │
│              │                    │              │
│  User types  │     POST JSON      │   Webhook    │
│  @bot hello  │ ────────────────── │   handler    │
│              │                    │              │
│              │    Response text   │   Process    │
│  Bot replies │ ◄────────────────  │   message    │
└──────────────┘                    └──────────────┘
```

### Webhook Payload

When someone mentions your bot, Campfire sends:

```json
{
  "user": {"id": 42, "name": "Jane Doe"},
  "room": {
    "id": 1,
    "name": "General",
    "path": "/rooms/1/123-botkey/messages"
  },
  "message": {
    "id": 999,
    "body": {
      "html": "<p>@OpenPaws what's the weather?</p>",
      "plain": "@OpenPaws what's the weather?"
    },
    "path": "/rooms/1@999"
  }
}
```

### Response Options

OpenPaws can respond in two ways:

1. **Direct Response:** Return text from the webhook handler — Campfire automatically posts it as a reply
2. **API Call:** POST to `/rooms/{room_id}/{bot_key}/messages` for more control

The adapter uses direct response by default for simplicity.

---

## Docker Compose Example

If you're running both Campfire and OpenPaws in Docker:

```yaml
version: '3.8'

services:
  campfire:
    image: basecamp/campfire
    ports:
      - "3000:3000"
    volumes:
      - campfire_data:/rails/storage
    environment:
      - SECRET_KEY_BASE=${SECRET_KEY_BASE}

  openpaws:
    image: openpaws/openpaws
    ports:
      - "8765:8765"
    environment:
      - CAMPFIRE_URL=http://campfire:3000
      - CAMPFIRE_BOT_KEY=${CAMPFIRE_BOT_KEY}
    volumes:
      - openpaws_data:/root/.openpaws

volumes:
  campfire_data:
  openpaws_data:
```

In this setup, set the bot's webhook URL to: `http://openpaws:8765/webhook`

---

## Troubleshooting

### "Webhook not receiving messages"

1. **Check the webhook URL** in Campfire bot settings
2. **Verify network connectivity:**
   ```bash
   # From Campfire's perspective, test the webhook
   curl -X POST http://localhost:8765/webhook \
     -H "Content-Type: application/json" \
     -d '{"user":{"id":1,"name":"Test"},"room":{"id":1,"path":"/rooms/1/key/messages"},"message":{"id":1,"body":{"plain":"test"}}}'
   ```
3. **Check OpenPaws logs** for incoming requests
4. **Ensure the bot is in the room** you're messaging from

### "Bot not responding"

1. **Check OpenPaws is running:**
   ```bash
   openpaws status
   ```
2. **Check the health endpoint:**
   ```bash
   curl http://localhost:8765/health
   ```
3. **Review logs for errors:**
   ```bash
   tail -f ~/.openpaws/logs/openpaws.log
   ```

### "Connection refused"

1. **Verify the port is open:**
   ```bash
   netstat -an | grep 8765
   ```
2. **Check firewall rules** if running on different machines
3. **For Docker:** Use `host.docker.internal` instead of `localhost`

### "Invalid bot key"

- Bot key format: `{id}-{token}` (e.g., `123-abc123xyz456`)
- Check for trailing whitespace when copying
- Verify the key in Campfire admin matches your config

### "API error when sending messages"

1. **Verify the room ID** is correct
2. **Check the bot is a member** of the target room
3. **Confirm the base_url** includes the protocol (`http://` or `https://`)

---

## Remote Campfire Setup

If your Campfire instance is hosted publicly (e.g., on a cloud server) but OpenPaws runs locally, Campfire won't be able to reach your webhook directly. Here are two recommended solutions:

### Option A: ngrok Tunnel (Recommended for Development)

[ngrok](https://ngrok.com) creates a secure tunnel from a public URL to your local machine.

**1. Install ngrok:**
```bash
# macOS
brew install ngrok

# Linux
curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | \
  sudo tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && \
  echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | \
  sudo tee /etc/apt/sources.list.d/ngrok.list && \
  sudo apt update && sudo apt install ngrok
```

**2. Sign up and configure:**
```bash
# Get your authtoken from https://dashboard.ngrok.com/get-started/your-authtoken
ngrok config add-authtoken YOUR_AUTH_TOKEN
```

**3. Start the tunnel:**
```bash
# Terminal 1: Start OpenPaws
openpaws start

# Terminal 2: Start ngrok tunnel
ngrok http 8765
```

ngrok will display a public URL like:
```
Forwarding    https://abc123.ngrok-free.app -> http://localhost:8765
```

**4. Configure Campfire bot webhook:**
```
https://abc123.ngrok-free.app/webhook
```

**Notes:**
- Free tier URL changes each restart (paid plans offer stable URLs)
- ngrok adds ~50-100ms latency
- Great for development and testing

---

### Option B: Cloudflare Tunnel (Recommended for Production)

[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) provides a free, stable tunnel without exposing ports.

**1. Install cloudflared:**
```bash
# macOS
brew install cloudflared

# Linux
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o cloudflared
chmod +x cloudflared
sudo mv cloudflared /usr/local/bin/
```

**2. Authenticate with Cloudflare:**
```bash
cloudflared tunnel login
```

**3. Create a tunnel:**
```bash
cloudflared tunnel create openpaws
```

**4. Configure the tunnel** (`~/.cloudflared/config.yml`):
```yaml
tunnel: openpaws
credentials-file: /path/to/.cloudflared/<TUNNEL_ID>.json

ingress:
  - hostname: openpaws.yourdomain.com
    service: http://localhost:8765
  - service: http_status:404
```

**5. Route DNS:**
```bash
cloudflared tunnel route dns openpaws openpaws.yourdomain.com
```

**6. Run the tunnel:**
```bash
# One-time
cloudflared tunnel run openpaws

# Or as a service
sudo cloudflared service install
```

**7. Configure Campfire bot webhook:**
```
https://openpaws.yourdomain.com/webhook
```

**Notes:**
- Requires a domain managed by Cloudflare (free tier works)
- Stable URL that persists across restarts
- Can run as a system service
- No port forwarding or firewall changes needed

---

### Other Approaches

If the above options don't fit your setup, consider:

- **Tailscale/WireGuard VPN:** Put both Campfire and OpenPaws on the same private network using [Tailscale](https://tailscale.com) or WireGuard, then use internal IPs
- **Reverse SSH tunnel:** If you have SSH access to the Campfire server, create a reverse tunnel (`ssh -R 8765:localhost:8765 user@campfire-server`)
- **localtunnel:** Free alternative to ngrok (`npx localtunnel --port 8765`), but less reliable
- **Self-hosted tunnel:** Run your own tunnel server with [frp](https://github.com/fatedier/frp) or [bore](https://github.com/ekzhang/bore)

---

## Security Considerations

1. **Tunnel security:** When using tunnels, your webhook is publicly accessible — consider adding authentication headers or IP allowlists if your tunnel supports it
2. **Webhook authentication:** Campfire doesn't sign webhook payloads — rely on network security or tunnel access controls
3. **HTTPS:** Always use HTTPS for public webhooks (ngrok and Cloudflare provide this automatically)
4. **Bot key protection:** Don't commit `CAMPFIRE_BOT_KEY` to version control

---

## Configuration Reference

| Setting | Environment Variable | Default | Description |
|---------|---------------------|---------|-------------|
| `base_url` | `CAMPFIRE_URL` | *required* | Your Campfire instance URL |
| `bot_key` | `CAMPFIRE_BOT_KEY` | *required* | Bot key from Campfire admin |
| `webhook_port` | — | `8765` | Port for the webhook server |
| `webhook_path` | — | `/webhook` | URL path for the webhook endpoint |

---

## Next Steps

- Configure scheduled tasks that post to Campfire rooms
- Set up multiple bots for different purposes
- Customize trigger patterns for your workflow

See [DESIGN.md](DESIGN.md) for more configuration options.
