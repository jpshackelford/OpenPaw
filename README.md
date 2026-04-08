# OpenPaws 🐾

A lightweight, always-on AI assistant with scheduled tasks and chat connectors.

Built on [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk).

## What is this?

OpenPaws turns the OpenHands SDK from "run an agent when I call it" into "have a persistent assistant that runs scheduled tasks and responds via chat apps."

**Features:**
- 📅 **Scheduled Tasks** - Cron-based recurring tasks that post results to chat
- 💬 **Chat Connectors** - Campfire, Slack, Telegram, Gmail
- 🔒 **Sandboxed Execution** - Runs agents in isolated environments
- ⚡ **Minimal Config** - YAML config + small CLI

## Quick Start with Campfire

The easiest way to get started is with [Campfire](https://once.com/campfire), a free self-hosted chat app.

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

### Task Scheduling

Tasks run on a cron schedule and automatically post their results to the configured channel. Each task needs:

- **schedule**: Cron expression (e.g., `"0 9 * * *"` for 9 AM daily, `"*/5 * * * *"` for every 5 minutes)
- **group**: Which chat group to post results to
- **prompt**: What to ask the AI agent
- **enabled** (optional): Set to `false` to pause without removing from config (defaults to `true`)

## Documentation

- [Campfire Setup](docs/CAMPFIRE_SETUP.md) - Complete Campfire integration guide
- [Slack Setup](docs/SLACK_SETUP.md) - Slack integration guide
- [Gmail Setup](docs/GMAIL_SETUP.md) - Gmail integration guide
- [Design Doc](docs/DESIGN.md) - Architecture and comparison with NanoClaw

## Why "OpenPaws"?

- **Open** → OpenHands ecosystem
- **Paws** → A paw has claws (inspired by [NanoClaw](https://github.com/qwibitai/nanoclaw)) and is a hand (OpenHands)

## Status

🚧 **Early Development** - Core functionality working, APIs may change.

## License

MIT
