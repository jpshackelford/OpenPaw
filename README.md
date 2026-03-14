# OpenPaws 🐾

A lightweight, always-on AI assistant with scheduled tasks and chat connectors.

Built on [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk).

## What is this?

OpenPaws turns the OpenHands SDK from "run an agent when I call it" into "have a persistent assistant that runs scheduled tasks and responds via chat apps."

**Features:**
- 📅 **Scheduled Tasks** - Cron-based recurring tasks
- 💬 **Chat Connectors** - Telegram, Slack, Discord (more coming)
- 🔒 **Sandboxed Execution** - Runs agents in isolated environments
- ⚡ **Minimal Config** - YAML config + small CLI

## Quick Start

```bash
pip install openpaws

# Create config
cat > ~/.openpaws/config.yaml << 'EOF'
channels:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}

groups:
  main:
    channel: telegram
    chat_id: "your-chat-id"
    trigger: "@paw"
    admin: true

tasks:
  morning-news:
    schedule: "0 8 * * *"
    group: main
    prompt: "Summarize top AI news"

agent:
  model: anthropic/claude-sonnet-4-20250514
EOF

# Start
openpaws start
```

## CLI

```bash
openpaws start              # Start the daemon
openpaws stop               # Stop
openpaws status             # Show status

openpaws tasks list         # List scheduled tasks
openpaws tasks run <name>   # Run a task now

openpaws logs               # View logs
```

## Documentation

- [Design Doc](docs/DESIGN.md) - Architecture and comparison with NanoClaw
- [Slack Setup](docs/SLACK_SETUP.md) - Complete Slack integration guide

## Why "OpenPaws"?

- **Open** → OpenHands ecosystem
- **Paws** → A paw has claws (inspired by [NanoClaw](https://github.com/qwibitai/nanoclaw)) and is a hand (OpenHands)

## Status

🚧 **Early Development** - Not yet functional

## License

MIT
