# Slack Integration Setup Guide

This guide walks you through setting up Slack integration for OpenPaws. The integration uses **Socket Mode**, which allows your bot to receive real-time events without requiring a public HTTP endpoint.

## Requirements

- A Slack workspace where you have permission to install apps
- Slack free tier or higher (all required features are available on free tier)

## Overview

OpenPaws connects to Slack using two tokens:

| Token | Prefix | Purpose |
|-------|--------|---------|
| **App Token** | `xapp-` | Establishes Socket Mode WebSocket connection |
| **Bot Token** | `xoxb-` | Authenticates API calls (posting messages, etc.) |

---

## Step 1: Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **"Create New App"**
3. Choose **"From scratch"**
4. Enter an app name (e.g., `OpenPaws`)
5. Select your workspace
6. Click **"Create App"**

---

## Step 2: Enable Socket Mode

Socket Mode allows your app to receive events via WebSocket instead of HTTP webhooks.

1. In your app settings, go to **"Socket Mode"** (left sidebar)
2. Toggle **"Enable Socket Mode"** to ON
3. You'll be prompted to create an **App-Level Token**:
   - Name it (e.g., `openpaws-socket`)
   - Add the scope: `connections:write`
   - Click **"Generate"**
4. **Copy and save the token** (starts with `xapp-`) — this is your `SLACK_APP_TOKEN`

---

## Step 3: Configure Bot Token Scopes

1. Go to **"OAuth & Permissions"** (left sidebar)
2. Scroll to **"Scopes"** section
3. Under **"Bot Token Scopes"**, add these scopes:

| Scope | Purpose |
|-------|---------|
| `app_mentions:read` | Receive events when users mention @YourBot |
| `chat:write` | Send messages to channels |
| `channels:read` | View basic channel info |
| `im:read` | View direct message info |
| `im:write` | Send direct messages |
| `im:history` | View messages in DMs |

**Note:** You can add more scopes later, but you'll need to reinstall the app.

---

## Step 4: Subscribe to Events

1. Go to **"Event Subscriptions"** (left sidebar)
2. Toggle **"Enable Events"** to ON
3. Under **"Subscribe to bot events"**, add:

| Event | Description |
|-------|-------------|
| `app_mention` | Triggered when someone mentions @YourBot |
| `message.im` | Triggered for direct messages to your bot |

**Optional events:**
- `message.channels` — For monitoring channel messages (requires `channels:history` scope)

---

## Step 5: Enable App Home (for DM Support)

1. Go to **"App Home"** (left sidebar)
2. Under **"Show Tabs"**, ensure **"Messages Tab"** is enabled
3. Check **"Allow users to send Slash commands and messages from the messages tab"**

This enables users to send direct messages to your bot.

---

## Step 6: Install App to Workspace

1. Go to **"Install App"** (left sidebar)
2. Click **"Install to Workspace"**
3. Review and authorize the permissions
4. **Copy the "Bot User OAuth Token"** (starts with `xoxb-`) — this is your `SLACK_BOT_TOKEN`

---

## Step 7: Configure OpenPaws

Add the Slack configuration to your `~/.openpaws/config.yaml`:

```yaml
channels:
  slack:
    app_token: ${SLACK_APP_TOKEN}
    bot_token: ${SLACK_BOT_TOKEN}

groups:
  my-team:
    channel: slack
    chat_id: "C0123456789"  # Your Slack channel ID
    trigger: "@paw"
```

Set the environment variables:

```bash
export SLACK_APP_TOKEN="xapp-1-A0123456789-0123456789012-your-token-here"
export SLACK_BOT_TOKEN="xoxb-0123456789012-0123456789012-your-token-here"
```

Or add them to a `.env` file (not committed to version control):

```bash
# .env
SLACK_APP_TOKEN=xapp-1-A0123456789-0123456789012-your-token-here
SLACK_BOT_TOKEN=xoxb-0123456789012-0123456789012-your-token-here
```

---

## Step 8: Find Your Channel ID

To find the channel ID for your `chat_id` configuration:

1. Open Slack in a web browser
2. Navigate to the channel
3. The URL will be: `https://app.slack.com/client/T.../C0123456789`
4. The `C0123456789` part is your channel ID

Or right-click the channel name → "View channel details" → scroll to the bottom for the Channel ID.

---

## Step 9: Invite the Bot to Channels

Before the bot can respond in a channel, it must be invited:

1. Go to the channel
2. Type `/invite @YourBotName`
3. Or mention the bot: `@YourBotName` (it will be prompted to join)

---

## Step 10: Start OpenPaws

```bash
openpaws start
```

Check the logs:

```bash
cat ~/.openpaws/logs/openpaws.log
```

You should see:
```
Started slack adapter
```

---

## Testing the Integration

1. **Test app mention:** In a channel where the bot is invited, type `@YourBotName hello`
2. **Test DM:** Open a direct message with your bot and send a message

The bot should respond to both.

---

## Troubleshooting

### "Slack adapter not starting"

**Check your tokens:**
```bash
# Verify token format
echo $SLACK_APP_TOKEN | grep -q "^xapp-" && echo "App token OK" || echo "App token INVALID"
echo $SLACK_BOT_TOKEN | grep -q "^xoxb-" && echo "Bot token OK" || echo "Bot token INVALID"
```

### "Not receiving events"

1. Verify Socket Mode is enabled in app settings
2. Check Event Subscriptions are enabled with correct events
3. Ensure the bot is invited to the channel
4. Check that the bot has required scopes

### "Cannot send messages"

1. Verify `chat:write` scope is added
2. Reinstall the app after adding scopes
3. Ensure bot is in the channel

### "Invalid token" errors

- App tokens start with `xapp-`
- Bot tokens start with `xoxb-`
- Check for trailing whitespace when copying tokens

---

## Security Best Practices

1. **Never commit tokens** to version control
2. Use environment variables or a secrets manager
3. Rotate tokens periodically via Slack app settings
4. Use minimal required scopes

---

## App Manifest (Quick Setup Alternative)

For faster setup, you can use an App Manifest. Go to your app's **"App Manifest"** section and paste:

```yaml
display_information:
  name: OpenPaws
  description: AI assistant powered by OpenHands
  background_color: "#2c2d30"

features:
  bot_user:
    display_name: OpenPaws
    always_online: true
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false

oauth_config:
  scopes:
    bot:
      - app_mentions:read
      - chat:write
      - channels:read
      - im:read
      - im:write
      - im:history

settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
  interactivity:
    is_enabled: false
  org_deploy_enabled: false
  socket_mode_enabled: true
  token_rotation_enabled: false
```

After saving the manifest, you still need to:
1. Generate an App-Level Token (Step 2)
2. Install the app (Step 6)

---

## Free Tier Compatibility

All features used by OpenPaws are available on Slack's free tier:

| Feature | Free Tier |
|---------|-----------|
| Socket Mode | ✅ |
| Bot users | ✅ |
| App mentions | ✅ |
| Direct messages | ✅ |
| OAuth scopes | ✅ |
| Event subscriptions | ✅ |

**Limitations on free tier:**
- Max 10 app integrations per workspace
- 90-day message history visibility
- 1-year data retention

These limitations don't affect bot functionality.

---

## Next Steps

- Configure scheduled tasks that post to Slack channels
- Set up multiple groups for different channels
- Customize trigger patterns for your workflow

See [DESIGN.md](DESIGN.md) for more configuration options.
