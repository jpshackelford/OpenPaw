# OpenPaw 🐾

A lightweight, always-on AI assistant with scheduled tasks and chat connectors.

Built on [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk).

## What is this?

OpenPaws turns the OpenHands SDK from "run an agent when I call it" into "have a persistent assistant that runs scheduled tasks and responds via chat apps."

**Features:**
- 📅 **Scheduled Tasks** - Cron-based recurring tasks that post results to chat
- 💬 **Chat Connectors** - Multiple platform adapters (see below)
- 🔒 **Sandboxed Execution** - Runs agents in isolated environments
- ⚡ **Minimal Config** - YAML config + small CLI

## Chat Adapters

| Adapter | Status | Setup Guide | Notes |
|---------|--------|-------------|-------|
| [Campfire](https://once.com/campfire) | ✅ Verified | [Campfire Setup](docs/CAMPFIRE_SETUP.md) | **Recommended** - Free, self-hosted, easiest setup |
| [Slack](https://slack.com) | ⚠️ Untested | [Slack Setup](docs/SLACK_SETUP.md) | Requires Socket Mode app |
| [Gmail](https://gmail.com) | ⚠️ Untested | [Gmail Setup](docs/GMAIL_SETUP.md) | Email-based interaction |

> **New to OpenPaws?** Start with Campfire - it's free, runs locally in Docker, and has the simplest setup path.

## Quick Start with Campfire

The easiest way to get started is with [Campfire](https://once.com/campfire), a free self-hosted chat app that runs entirely on your machine.

**Prerequisites:** Docker, Python 3.10+

```bash
# Clone the repo
git clone https://github.com/jpshackelford/OpenPaw.git
cd OpenPaw

# Run the setup script (installs Campfire + OpenPaws)
python scripts/setup_campfire_openpaw.py --disable-tls
```

The script will:
1. ✅ Check prerequisites (Docker, Python, curl)
2. 📦 Install the [ONCE CLI](https://github.com/basecamp/once) 
3. 🔥 Deploy Campfire locally via Docker
4. 🐾 Install OpenPaws using [uv](https://docs.astral.sh/uv/)
5. ⚙️ Generate config at `~/.openpaws/config.yaml`

**After installation:**

1. Open Campfire at http://campfire.localhost and complete setup
2. Go to **Account → Bots → New bot**
   - Name: `OpenPaws`
   - Webhook URL: `http://localhost:8765/webhook`
3. Copy the bot key and set it:
   ```bash
   export CAMPFIRE_BOT_KEY='your-bot-key-here'
   ```
4. Start OpenPaws:
   ```bash
   openpaws start
   ```
5. In Campfire, @mention your bot: `@OpenPaws hello`

See [Campfire Setup Guide](docs/CAMPFIRE_SETUP.md) for detailed instructions.

## Alternative: Install OpenPaws Only

If you already have Campfire (or want to use Slack/Telegram):

```bash
uv tool install "openpaws @ git+https://github.com/jpshackelford/OpenPaw.git@feature/campfire-adapter"
```

Then create your config manually - see the [documentation](#documentation) for each channel.

## CLI

```bash
openpaws start              # Start the daemon
openpaws stop               # Stop
openpaws status             # Show status

openpaws tasks list         # List scheduled tasks
openpaws tasks run <name>   # Run a task now

openpaws logs               # View logs
```

## Configuration

OpenPaws uses a YAML config file at `~/.openpaws/config.yaml`:

```yaml
channels:
  campfire:
    base_url: http://campfire.localhost
    bot_key: ${CAMPFIRE_BOT_KEY}
    webhook_port: 8765
    context_messages: 10    # Recent messages for conversation context

groups:
  team:
    channel: campfire
    chat_id: "1"           # Room ID from Campfire URL
    trigger: "@paw"

tasks:
  daily-standup:
    schedule: "0 9 * * 1-5"  # 9 AM weekdays
    group: team
    prompt: "What should I focus on today?"
    enabled: true            # Set to false to pause without removing

  dad-jokes:
    schedule: "*/5 * * * *"  # Every 5 minutes
    group: team
    prompt: "Tell me a dad joke."
    enabled: false           # Paused - won't run until enabled

agent:
  model: anthropic/claude-sonnet-4-20250514
```

### Conversation Context (Campfire)

When someone @mentions the bot in Campfire, OpenPaws fetches recent messages from the room so the AI understands the conversation context. Configure this with:

```yaml
channels:
  campfire:
    context_messages: 10    # Number of recent messages to include (default: 10)
                            # Set to 0 to disable context fetching
```

This allows the bot to give contextual responses based on what people were discussing, not just the single message that triggered it.

> **Note:** Conversation context requires the bot read API, which is available in our Campfire fork. See [Campfire Fork](#campfire-fork) below.

### Task Scheduling

Tasks run on a cron schedule and automatically post their results to the configured channel. Each task needs:

- **schedule**: Cron expression (e.g., `"0 9 * * *"` for 9 AM daily, `"*/5 * * * *"` for every 5 minutes)
- **group**: Which chat group to post results to
- **prompt**: What to ask the AI agent
- **enabled** (optional): Set to `false` to pause without removing from config (defaults to `true`)

## Documentation

- [Design Doc](docs/DESIGN.md) - Architecture and comparison with NanoClaw
- See [Chat Adapters](#chat-adapters) table above for setup guides

## Campfire Fork

OpenPaws uses a fork of Campfire that adds a **bot read API**, allowing bots to read recent messages from rooms they're members of. This enables conversation context - the bot can understand what people were discussing before responding.

**PR:** [basecamp/once-campfire#190](https://github.com/basecamp/once-campfire/pull/190)

### Why a Fork?

The official Campfire bot API only allows bots to *send* messages, not *read* them. Our fork adds a `GET /rooms/:room_id/:bot_key/messages` endpoint that:
- Returns recent messages from the room
- Only works for rooms where the bot is a member (same security as posting)
- Includes pagination support

### Using the Fork

The setup script automatically uses our fork:

```bash
python scripts/setup_campfire_openpaw.py --disable-tls
```

If you already have Campfire deployed with the official image, update to the fork:

```bash
once update campfire.localhost --image ghcr.io/jpshackelford/once-campfire:bot-api-v2
```

To switch back to the official image (without conversation context):

```bash
once update campfire.localhost --image ghcr.io/basecamp/once-campfire
# Also set context_messages: 0 in your config to avoid 404 warnings
```

### When the PR is Merged

Once [PR #190](https://github.com/basecamp/once-campfire/pull/190) is merged upstream, you can switch to the official image and conversation context will work automatically.

## Why "OpenPaws"?

- **Open** → OpenHands ecosystem
- **Paws** → A paw has claws (inspired by [NanoClaw](https://github.com/qwibitai/nanoclaw)) and is a hand (OpenHands)

## Status

🚧 **Early Development** - Core functionality working, APIs may change.

## License

MIT
