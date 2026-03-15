# OpenPaws

A lightweight, always-on AI assistant with scheduled tasks and chat connectors. Built on [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk).

## Vision

OpenPaws is a Python reimplementation of [NanoClaw](https://github.com/qwibitai/nanoclaw) concepts using software-agent-sdk as the foundation. It turns the SDK from "run an agent when I call it" into "have a persistent assistant that runs scheduled tasks and responds via chat apps."

### Name Origin

- **Open** → OpenHands ecosystem
- **Paws** → A paw has claws (NanoClaw heritage) and is a hand (OpenHands heritage)

---

## NanoClaw vs software-agent-sdk: What's Missing?

### Sandboxing

#### Container Runtime Isolation

| Feature | NanoClaw | software-agent-sdk |
|---------|----------|-------------------|
| Docker containers | ✅ | ✅ |
| Docker Sandbox (micro VM) | ✅ | ❌ |
| Apple Container (macOS) | ✅ | ❌ |
| Apptainer/Singularity | ❌ | ✅ |
| Container pause/resume | ❌ | ✅ |

#### Filesystem Sandboxing

| Feature | NanoClaw | software-agent-sdk |
|---------|----------|-------------------|
| Mount allowlist (approved directories) | ✅ | ❌ |
| Blocked patterns (`.ssh`, `.aws`, `.env`, etc.) | ✅ | ❌ |
| Symlink traversal protection | ✅ | ❌ |
| Read-only enforcement by policy | ✅ | ⚠️ Manual per-volume |
| Project root protection (shadow `.env`) | ✅ | ❌ |

#### Credential Sandboxing

| Feature | NanoClaw | software-agent-sdk |
|---------|----------|-------------------|
| LLM API key isolation | ✅ Built-in proxy | ✅ Via LiteLLM proxy |
| Bash command secret injection | ✅ | ✅ SecretRegistry |
| Secret masking in output | ✅ | ✅ |
| Encrypted state persistence | ⚠️ Basic | ✅ Cipher support |

### Security Policies

| Feature | NanoClaw | software-agent-sdk |
|---------|----------|-------------------|
| Security analyzers | ❌ | ✅ Pluggable |
| Confirmation policies | ❌ Bypassed | ✅ Full |
| Risk-based action blocking | ❌ | ✅ |

### Biggest Differentiators (What OpenPaws Adds)

| Feature | NanoClaw | software-agent-sdk | OpenPaws |
|---------|----------|-------------------|----------|
| **Task Scheduling** | ✅ Cron/interval/once | ❌ | ✅ |
| **Chat Connectors** | ✅ WhatsApp, Telegram, Slack, Discord | ❌ | ✅ |
| **Trigger Routing** | ✅ `@Andy` pattern matching | ❌ | ✅ |
| **Message Queue** | ✅ Per-group with concurrency | ❌ | ✅ |

### What SDK Already Has

| Feature | software-agent-sdk | NanoClaw |
|---------|-------------------|----------|
| REST API | ✅ Full | ❌ |
| WebSocket streaming | ✅ | ❌ |
| Multi-provider LLM | ✅ 100+ via LiteLLM | ❌ Anthropic only |
| Cloud workspaces | ✅ | ❌ |

---

## UX Design

**Philosophy**: Config files first, small CLI. Start with minimal UX, add more as needed.

### Config File (`openpaws.yaml`)

```yaml
# Channels
channels:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
    
  slack:
    app_token: ${SLACK_APP_TOKEN}
    bot_token: ${SLACK_BOT_TOKEN}

  gmail:
    credentials_file: ${GMAIL_CREDENTIALS}
    mode: channel  # or "tool"
    poll_interval: 60  # seconds
    filter_label: "openpaws"  # optional

# Groups/Conversations
groups:
  main:
    channel: telegram
    chat_id: "123456789"
    trigger: "@paw"
    admin: true
    
  family:
    channel: telegram  
    chat_id: "-100987654321"
    trigger: "@paw"
    mounts:
      - ~/Documents/family:ro

# Scheduled Tasks
tasks:
  morning-news:
    schedule: "0 8 * * *"  # 8am daily
    group: main
    prompt: "Summarize top AI news from Hacker News"
    
  weekly-review:
    schedule: "0 9 * * 1"  # Monday 9am
    group: main
    prompt: "Review my calendar for the week"

# Agent
agent:
  model: anthropic/claude-sonnet-4-20250514
  llm_proxy: http://localhost:4000  # Optional LiteLLM proxy
```

### CLI

```bash
# Start the service
openpaws start
openpaws start --config ./my-config.yaml

# Stop
openpaws stop

# Status
openpaws status

# Tasks
openpaws tasks list
openpaws tasks run morning-news      # Run now
openpaws tasks pause weekly-review
openpaws tasks resume weekly-review

# Groups  
openpaws groups list
openpaws groups add family --channel telegram --chat-id "-100..."

# Send a test message
openpaws send main "Hello from CLI"

# Logs
openpaws logs
openpaws logs --group family
openpaws logs --task morning-news
```

### Directory Structure

```
~/.openpaws/
├── config.yaml           # Main config
├── state.db              # SQLite for tasks, sessions
├── groups/
│   ├── main/
│   │   └── CLAUDE.md     # Per-group memory
│   └── family/
│       └── CLAUDE.md
├── logs/
└── mount-policy.yaml     # Optional: filesystem restrictions
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Chat Apps (Telegram, Slack, Discord, WhatsApp)             │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│  OpenPaws Daemon                                             │
│  ├── Channel Adapters (poll/webhook for messages)           │
│  ├── Message Router (trigger detection, queue management)   │
│  ├── Scheduler (cron parser, task execution)                │
│  └── Group Manager (isolation, mounts, sessions)            │
├─────────────────────────────────────────────────────────────┤
│  software-agent-sdk                                          │
│  ├── Conversations                                           │
│  ├── Agents + Tools                                          │
│  ├── SecretRegistry                                          │
│  ├── DockerWorkspace (optional)                              │
│  └── LLM (via LiteLLM)                                       │
└─────────────────────────────────────────────────────────────┘
```

### Core Components

| Component | Responsibility |
|-----------|---------------|
| **Daemon** | Main process, lifecycle management |
| **Channel Adapters** | Slack, Gmail, Telegram clients |
| **Message Router** | Trigger detection (`@paw`), group routing |
| **Scheduler** | Cron parsing, task persistence, execution loop |
| **Group Manager** | Per-group isolation, mounts, session state |

---

## State Persistence

OpenPaws uses SQLite to persist scheduler state and conversation sessions across daemon restarts.

### Database Location

```
~/.openpaws/state.db        # Default location
$OPENPAWS_DIR/state.db      # If OPENPAWS_DIR env var is set
```

### Schema

```sql
-- Task state: preserves execution history across restarts
CREATE TABLE tasks (
    name TEXT PRIMARY KEY,
    schedule TEXT,
    group_name TEXT,
    prompt TEXT,
    status TEXT,           -- active, paused, running
    next_run TEXT,         -- ISO datetime
    last_run TEXT,         -- ISO datetime
    last_result TEXT       -- Result/error message
);

-- Session state: enables conversation resume capability
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    group_name TEXT,
    created_at TEXT,       -- ISO datetime
    updated_at TEXT,       -- ISO datetime
    state BLOB             -- Serialized conversation state
);
```

### Behavior

- **Task state restoration**: When the daemon starts, it loads previous task states (last_run, last_result, status) from the database
- **Automatic persistence**: Task state is saved after each execution and on pause/resume operations
- **Session storage**: Conversation sessions can be saved and resumed (for future multi-turn chat support)

---

## v0.1 Scope

### In Scope

- [x] Config file parsing (`openpaws.yaml`)
- [x] CLI: `start`, `stop`, `status`
- [ ] CLI: `tasks list`, `tasks run <name>`
- [x] Scheduler: cron-based tasks
- [ ] One channel adapter (Telegram)
- [ ] Single group support
- [x] SQLite state persistence
- [ ] Integration with software-agent-sdk Conversation

### Out of Scope (Future)

- Web UI
- Interactive setup wizard
- Multiple config file formats (TOML, JSON)
- Plugin system for channels
- WhatsApp adapter (requires more complex auth)
- Multi-group isolation
- Filesystem sandboxing policy
- Docker workspace integration

---

## Dependencies

```
software-agent-sdk (openhands-sdk)
croniter                   # Cron expression parsing
python-telegram-bot        # Telegram adapter
slack-bolt                 # Slack adapter
google-auth                # Gmail OAuth
google-auth-oauthlib       # Gmail OAuth flow
google-api-python-client   # Gmail API
click                      # CLI framework
pyyaml                     # Config parsing
sqlite3                    # State persistence (stdlib)
```

---

## Open Questions

1. **Daemon management**: Use systemd/launchd directly, or build supervisor into CLI?
2. **Channel auth**: Store tokens in config (with env var substitution) or separate secrets file?
3. **LLM proxy**: Require LiteLLM proxy for credential isolation, or make optional?
4. **Workspace**: Start with LocalWorkspace or DockerWorkspace?

---

## References

- [NanoClaw](https://github.com/qwibitai/nanoclaw) - TypeScript implementation, inspiration
- [software-agent-sdk](https://github.com/OpenHands/software-agent-sdk) - Foundation
- [LiteLLM Proxy](https://docs.litellm.ai/docs/proxy/quick_start) - Credential isolation pattern
