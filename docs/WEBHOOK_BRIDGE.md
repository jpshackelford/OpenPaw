# Webhook Bridge: Event Gateway for Agents Behind Firewalls

## Problem Statement

Agents like OpenPaws often run behind firewalls on desktops or private networks. External services (GitHub, Slack, Stripe, etc.) deliver events via webhooks to public URLs, but these webhooks cannot reach agents that aren't publicly accessible.

**Current state**: Agent misses GitHub PR events, Slack messages, payment notifications, etc.

**Desired state**: Agent receives and responds to external events in real-time, regardless of network topology.

---

## Solution Overview

**Webhook Bridge** is a central public service that acts as an event gateway:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  External Services                                                           │
│  ┌────────┐  ┌────────┐  ┌────────┐  ┌────────┐                             │
│  │ GitHub │  │ Slack  │  │ Stripe │  │ Custom │                             │
│  └───┬────┘  └───┬────┘  └───┬────┘  └───┬────┘                             │
│      │           │           │           │                                   │
│      └───────────┴─────┬─────┴───────────┘                                   │
│                        │ HTTPS webhooks                                      │
│                        ▼                                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Webhook Bridge (Public Service)                                     │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │   Webhook    │  │   Adapter    │  │    Event     │               │    │
│  │  │   Receiver   │─▶│   Pipeline   │─▶│    Store     │               │    │
│  │  └──────────────┘  └──────────────┘  └──────┬───────┘               │    │
│  │                                             │                        │    │
│  │  ┌──────────────┐  ┌──────────────┐         │                        │    │
│  │  │  Socket.IO   │◀─│   Dispatch   │◀────────┘                        │    │
│  │  │   Server     │  │    Engine    │                                  │    │
│  │  └──────┬───────┘  └──────────────┘                                  │    │
│  └─────────┼────────────────────────────────────────────────────────────┘    │
│            │                                                                  │
│            │ WSS (outbound from agent's perspective)                         │
│            ▼                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │  Agent (Behind Firewall)                                             │    │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐               │    │
│  │  │  Socket.IO   │─▶│    Event     │─▶│    Skill     │               │    │
│  │  │   Client     │  │   Router     │  │   Executor   │               │    │
│  │  └──────────────┘  └──────────────┘  └──────────────┘               │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key insight**: The agent initiates an outbound WebSocket connection to the public service. This works through NAT and most firewalls because the connection is outbound. Events flow back over this established connection.

---

## Core Components

### 1. Webhook Receiver

Accepts incoming webhooks at user-specific URLs:

```
https://events.openpaws.dev/hook/u/{user_id}/{adapter_id}
                                   │          │
                                   │          └── github, slack, stripe, etc.
                                   └── User's unique identifier
```

**Responsibilities:**
- Accept POST requests with webhook payloads
- Route to appropriate adapter based on URL path
- Return appropriate HTTP response to webhook sender

### 2. Adapter Pipeline

Processes incoming webhooks through a pluggable adapter system:

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   Receive   │───▶│   Verify    │───▶│   Parse     │───▶│  Normalize  │
│   Webhook   │    │  Signature  │    │   Payload   │    │   Event     │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
```

### 3. Event Store

Persists events for reliability and debugging:

- Stores normalized events with metadata
- Tracks delivery status per subscriber
- Enables event replay for debugging
- Configurable retention policies

### 4. Dispatch Engine

Routes events to connected agents:

- Maintains registry of connected agents
- Pushes events via Socket.IO
- Handles acknowledgments and retries
- Queues events for offline agents

### 5. Socket.IO Server

Manages real-time connections to agents:

- Authenticates agent connections
- Organizes connections into per-user rooms
- Provides bidirectional communication
- Handles reconnection gracefully

---

## Data-Driven Webhook Adapters

### The Extensibility Challenge

Different webhook sources have different:
- Signature verification schemes
- Payload formats
- Header conventions
- Event type indicators

We need to handle new webhook sources without deploying new code.

### Adapter Definition Format

Adapters are defined in YAML, describing how to process webhooks from a specific source:

```yaml
# adapters/github.yaml
adapter:
  id: github
  name: GitHub
  description: GitHub repository and organization webhooks
  documentation: https://docs.github.com/webhooks

# Signature verification configuration
signature:
  algorithm: hmac-sha256
  header: X-Hub-Signature-256
  format: "sha256={signature}"  # How signature appears in header
  payload: body                  # What to sign (body, body+timestamp, etc.)
  secret_source: user_config     # Where to get the secret

# Header extraction
headers:
  event_type: X-GitHub-Event     # Header containing event type
  delivery_id: X-GitHub-Delivery # Unique delivery identifier
  timestamp: null                # GitHub doesn't include timestamp header

# Payload configuration  
payload:
  format: json
  encoding: utf-8

# Event normalization rules
events:
  - source_type: push
    normalized_type: code.push
    description: "Push to a repository"
    extract:
      repository: "$.repository.full_name"
      ref: "$.ref"
      branch: "$.ref | split('/') | last"
      before: "$.before"
      after: "$.after"
      commits: "$.commits"
      pusher: "$.pusher.name"
      compare_url: "$.compare"

  - source_type: pull_request
    normalized_type: code.pull_request
    description: "Pull request activity"
    extract:
      action: "$.action"
      repository: "$.repository.full_name"
      number: "$.pull_request.number"
      title: "$.pull_request.title"
      body: "$.pull_request.body"
      author: "$.pull_request.user.login"
      head_ref: "$.pull_request.head.ref"
      base_ref: "$.pull_request.base.ref"
      url: "$.pull_request.html_url"
      draft: "$.pull_request.draft"

  - source_type: issues
    normalized_type: issue.activity
    extract:
      action: "$.action"
      repository: "$.repository.full_name"
      number: "$.issue.number"
      title: "$.issue.title"
      author: "$.issue.user.login"
      labels: "$.issue.labels[*].name"

  # Catch-all for unmapped events
  - source_type: "*"
    normalized_type: "github.{source_type}"
    extract:
      action: "$.action"
      repository: "$.repository.full_name"
```

### Expression Language for Extraction

The `extract` field uses JSONPath with extensions:

| Expression | Description |
|------------|-------------|
| `$.field` | Simple field access |
| `$.nested.field` | Nested field access |
| `$.array[0]` | Array index |
| `$.array[*].field` | Map over array |
| `$.field \| default('value')` | Default if null |
| `$.field \| split('/') \| last` | Pipe through transforms |
| `{source_type}` | Template variable substitution |

### Plugin System for Complex Adapters

Some webhooks require custom logic that can't be expressed declaratively. These use Python plugins:

```python
# plugins/slack.py
from webhook_bridge.adapter import WebhookAdapter, AdapterResponse

class SlackAdapter(WebhookAdapter):
    """Slack webhook adapter with URL verification challenge support."""
    
    id = "slack"
    
    def pre_process(self, request) -> AdapterResponse | None:
        """Handle Slack's URL verification challenge."""
        payload = request.json
        if payload.get("type") == "url_verification":
            return AdapterResponse(
                status=200,
                body={"challenge": payload["challenge"]},
                skip_processing=True
            )
        return None
    
    def verify_signature(self, request, secret: str) -> bool:
        """Slack's timestamp-based signature verification."""
        timestamp = request.headers.get("X-Slack-Request-Timestamp")
        signature = request.headers.get("X-Slack-Signature")
        
        # Reject requests older than 5 minutes
        if abs(time.time() - int(timestamp)) > 300:
            return False
        
        # Compute expected signature
        sig_basestring = f"v0:{timestamp}:{request.body.decode()}"
        expected = "v0=" + hmac.new(
            secret.encode(), sig_basestring.encode(), hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(expected, signature)
    
    def get_event_type(self, payload: dict) -> str:
        """Extract event type from Slack's nested structure."""
        if "event" in payload:
            return payload["event"].get("type", "unknown")
        return payload.get("type", "unknown")
```

### Adapter Registry

```yaml
# registry.yaml
adapters:
  # Built-in adapters (YAML definitions)
  builtin:
    - github
    - gitlab
    - bitbucket
    - stripe
    - discord
    - linear
    - jira
    - pagerduty
    
  # Built-in adapters (Python plugins for complex cases)  
  plugins:
    - slack      # Needs URL verification challenge
    - twilio     # Needs request validation
    - shopify    # Needs HMAC with specific encoding
    
  # User-defined adapters
  custom:
    enabled: true
    directory: /data/custom_adapters/
```

### Adding a Custom Adapter

Users can add adapters for internal services:

```yaml
# custom_adapters/internal-ci.yaml
adapter:
  id: internal-ci
  name: Internal CI System
  description: Webhooks from our internal CI/CD pipeline

signature:
  algorithm: hmac-sha256
  header: X-CI-Signature
  format: "{signature}"
  secret_source: user_config

headers:
  event_type: X-CI-Event
  delivery_id: X-CI-Delivery-ID

events:
  - source_type: build.completed
    normalized_type: ci.build.completed
    extract:
      project: "$.project.name"
      branch: "$.build.branch"
      status: "$.build.status"
      duration: "$.build.duration_seconds"
      commit: "$.build.commit_sha"
```

---

## Common Event Schema

All events are normalized to a common schema for consistent handling:

```json
{
  "id": "evt_01HQXYZ789",
  "version": "1.0",
  "timestamp": "2024-03-15T10:30:45.123Z",
  
  "source": {
    "adapter": "github",
    "event_type": "pull_request",
    "delivery_id": "abc-123-def-456"
  },
  
  "user": {
    "id": "usr_abc123",
    "webhook_url": "https://events.openpaws.dev/hook/u/usr_abc123/github"
  },
  
  "normalized": {
    "type": "code.pull_request",
    "action": "opened",
    "data": {
      "repository": "jpshackelford/open-paw",
      "number": 42,
      "title": "Add webhook bridge support",
      "author": "jpshackelford",
      "head_ref": "feature/webhooks",
      "base_ref": "main",
      "url": "https://github.com/jpshackelford/open-paw/pull/42"
    }
  },
  
  "raw": {
    "included": true,
    "payload": { /* Original webhook payload */ }
  },
  
  "delivery": {
    "status": "pending",
    "attempts": 0,
    "last_attempt": null
  }
}
```

### Normalized Type Taxonomy

```
code.
├── push              # Code pushed to repository
├── pull_request      # PR opened, updated, merged, closed
├── commit_comment    # Comment on a commit
├── branch            # Branch created, deleted
└── tag               # Tag created, deleted

issue.
├── created           # Issue opened
├── updated           # Issue edited
├── closed            # Issue closed
├── commented         # Comment added
└── labeled           # Labels changed

ci.
├── build.started     # Build/pipeline started
├── build.completed   # Build finished (success or failure)
├── deploy.started    # Deployment initiated
└── deploy.completed  # Deployment finished

chat.
├── message           # Direct message received
├── mention           # Bot/user mentioned
├── reaction          # Reaction added/removed
└── thread_reply      # Reply in thread

payment.
├── succeeded         # Payment completed
├── failed            # Payment failed
├── refunded          # Payment refunded
└── subscription      # Subscription event

alert.
├── triggered         # Alert fired
├── resolved          # Alert resolved
└── acknowledged      # Alert acknowledged
```

---

## Persistence Layer

### Database Schema

```sql
-- Users
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT NOW(),
    settings JSONB DEFAULT '{}'
);

-- API keys for agent authentication
CREATE TABLE api_keys (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    key_hash TEXT NOT NULL,  -- bcrypt hash
    name TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    last_used_at TIMESTAMP,
    expires_at TIMESTAMP,
    revoked BOOLEAN DEFAULT FALSE
);

-- Webhook configurations per user per adapter
CREATE TABLE webhook_configs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    adapter_id TEXT NOT NULL,
    secret_encrypted BYTEA,          -- Encrypted with per-user key
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP,
    UNIQUE (user_id, adapter_id)
);

-- Stored events
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    adapter_id TEXT NOT NULL,
    source_event_type TEXT NOT NULL,
    normalized_type TEXT,
    delivery_id TEXT,                -- From source system
    
    -- Payloads
    raw_payload JSONB,               -- Original webhook body
    normalized_payload JSONB,        -- Extracted/normalized data
    
    -- Timestamps
    received_at TIMESTAMP NOT NULL,
    processed_at TIMESTAMP,
    
    -- Delivery tracking
    delivery_status TEXT DEFAULT 'pending',  -- pending, delivered, failed, expired
    delivery_attempts INTEGER DEFAULT 0,
    last_delivery_attempt TIMESTAMP,
    delivered_at TIMESTAMP,
    
    -- Indexing
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_events_user_received ON events(user_id, received_at DESC);
CREATE INDEX idx_events_delivery_status ON events(delivery_status, last_delivery_attempt);

-- Connected agents (could also use Redis for this)
CREATE TABLE agent_connections (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    socket_id TEXT NOT NULL,
    connected_at TIMESTAMP DEFAULT NOW(),
    last_heartbeat TIMESTAMP,
    subscriptions JSONB DEFAULT '[]',
    metadata JSONB DEFAULT '{}'
);
```

### Event Lifecycle

```
                    ┌──────────────────────────────────────────────────┐
                    │                                                  │
    ┌─────────┐     │   ┌─────────┐    ┌───────────┐    ┌─────────┐   │
    │ Webhook │────▶│   │ pending │───▶│ delivered │    │ expired │   │
    │ Arrives │     │   └─────────┘    └───────────┘    └─────────┘   │
    └─────────┘     │        │              ▲                ▲        │
                    │        │              │                │        │
                    │        ▼              │                │        │
                    │   ┌─────────┐         │                │        │
                    │   │ retrying│─────────┘                │        │
                    │   └─────────┘                          │        │
                    │        │                               │        │
                    │        │ max retries exceeded          │        │
                    │        ▼                               │        │
                    │   ┌─────────┐                          │        │
                    │   │ failed  │──────────────────────────┘        │
                    │   └─────────┘   retention expired               │
                    │                                                  │
                    └──────────────────────────────────────────────────┘
```

---

## Security Architecture

### Authentication Layers

```
┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: Webhook Source Authentication                          │
│ • Signature verification (HMAC, RSA)                            │
│ • Per-adapter verification schemes                              │
│ • User-provided secrets stored encrypted                        │
├─────────────────────────────────────────────────────────────────┤
│ Layer 2: User Authentication                                    │
│ • OAuth (GitHub, Google) for dashboard                          │
│ • API keys for programmatic access                              │
│ • JWT tokens for session management                             │
├─────────────────────────────────────────────────────────────────┤
│ Layer 3: Agent Authentication                                   │
│ • API key in Socket.IO handshake                                │
│ • TLS client certificates (optional)                            │
│ • Token refresh for long-lived connections                      │
├─────────────────────────────────────────────────────────────────┤
│ Layer 4: Transport Security                                     │
│ • TLS 1.3 for all connections                                   │
│ • Certificate pinning (optional)                                │
│ • HSTS headers                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Signature Verification Flow

```python
async def verify_webhook(request, adapter, user_config):
    """Verify incoming webhook signature."""
    
    # Get signature from header
    sig_header = adapter.signature.header
    signature = request.headers.get(sig_header)
    if not signature:
        raise WebhookError(401, "Missing signature header")
    
    # Get user's secret for this adapter
    secret = await get_decrypted_secret(user_config)
    if not secret:
        raise WebhookError(401, "Webhook secret not configured")
    
    # Compute expected signature
    if adapter.signature.algorithm == "hmac-sha256":
        payload = request.body
        expected = hmac.new(
            secret.encode(),
            payload,
            hashlib.sha256
        ).hexdigest()
        
        # Handle format like "sha256={signature}"
        if adapter.signature.format:
            expected = adapter.signature.format.replace("{signature}", expected)
    
    # Constant-time comparison
    if not hmac.compare_digest(signature, expected):
        raise WebhookError(401, "Invalid signature")
```

### Data Isolation

```
┌─────────────────────────────────────────────────────────────────┐
│ User A's Data Boundary                                          │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│ │   Events    │  │   Secrets   │  │ Connections │              │
│ │ user_id=A   │  │ user_id=A   │  │ user_id=A   │              │
│ └─────────────┘  └─────────────┘  └─────────────┘              │
├─────────────────────────────────────────────────────────────────┤
│ User B's Data Boundary                                          │
│ ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│ │   Events    │  │   Secrets   │  │ Connections │              │
│ │ user_id=B   │  │ user_id=B   │  │ user_id=B   │              │
│ └─────────────┘  └─────────────┘  └─────────────┘              │
└─────────────────────────────────────────────────────────────────┘

• All queries include user_id filter
• Row-level security policies in database
• Socket.IO rooms scoped to user_id
• Audit logging for cross-boundary access attempts
```

### Secret Management

```
┌─────────────────────────────────────────────────────────────────┐
│ Secret Storage Flow                                              │
│                                                                  │
│  User provides        Encrypt with         Store in             │
│  webhook secret  ───▶ user's key     ───▶ database              │
│                       (derived from        (encrypted            │
│                        master key +        blob)                 │
│                        user_id)                                  │
│                                                                  │
│  Master key stored in environment variable or KMS               │
└─────────────────────────────────────────────────────────────────┘
```

### Rate Limiting

| Resource | Limit | Scope |
|----------|-------|-------|
| Incoming webhooks | 100/minute | Per user |
| Socket.IO connections | 10 | Per user |
| API requests | 1000/hour | Per API key |
| Event storage | 10,000/day | Per user (free tier) |

### Threat Mitigations

| Threat | Mitigation |
|--------|------------|
| Webhook forgery | Signature verification required |
| Replay attacks | Timestamp validation, delivery_id deduplication |
| Data exfiltration | User isolation, encryption at rest |
| DoS via webhooks | Rate limiting, payload size limits |
| Stolen API key | Key rotation, IP allowlisting (optional) |
| Man-in-the-middle | TLS 1.3, certificate validation |

---

## Agent Integration

### OpenPaws Configuration

```yaml
# ~/.openpaws/config.yaml

channels:
  telegram:
    bot_token: ${TELEGRAM_BOT_TOKEN}
  
  # Webhook bridge channel
  webhook_bridge:
    endpoint: wss://events.openpaws.dev/socket
    api_key: ${OPENPAWS_WEBHOOK_API_KEY}
    
    # Optional: filter which events to receive
    subscriptions:
      - adapter: github
        events: ["pull_request", "push", "issues"]
      - adapter: slack
        events: ["message", "app_mention"]
      - adapter: "*"  # All events from all adapters

# Map event types to skills
webhook_skills:
  # GitHub PR events trigger code review skill
  - match:
      type: code.pull_request
      action: [opened, synchronize]
    skill: github-pr-review
    config:
      auto_approve: false
      focus_areas: [security, performance]
  
  # GitHub push to main triggers deployment check
  - match:
      type: code.push
      data.branch: [main, master]
    skill: deployment-validator
  
  # Slack mentions trigger conversational response
  - match:
      type: chat.mention
      source.adapter: slack
    skill: slack-responder
    respond_to_source: true
  
  # PagerDuty alerts trigger incident handler
  - match:
      type: alert.triggered
    skill: incident-handler
    priority: high

# Response routing - how to send responses back to sources
response_channels:
  github:
    token: ${GITHUB_TOKEN}
  slack:
    bot_token: ${SLACK_BOT_TOKEN}
```

### Webhook Channel Adapter

New channel type for OpenPaws:

```python
# src/openpaws/channels/webhook_bridge.py

class WebhookBridgeChannel:
    """Channel adapter for receiving events from Webhook Bridge."""
    
    def __init__(self, config: WebhookBridgeConfig):
        self.config = config
        self.socket: socketio.AsyncClient = None
        self.skill_router = SkillRouter(config.webhook_skills)
    
    async def connect(self):
        """Connect to Webhook Bridge via Socket.IO."""
        self.socket = socketio.AsyncClient()
        
        @self.socket.on("event")
        async def on_event(data):
            await self.handle_event(WebhookEvent.from_dict(data))
        
        @self.socket.on("connect")
        async def on_connect():
            # Subscribe to configured event types
            await self.socket.emit("subscribe", {
                "subscriptions": self.config.subscriptions
            })
        
        await self.socket.connect(
            self.config.endpoint,
            auth={"api_key": self.config.api_key},
            transports=["websocket"]
        )
    
    async def handle_event(self, event: WebhookEvent):
        """Process incoming webhook event."""
        # Find matching skill
        skill_config = self.skill_router.match(event)
        if not skill_config:
            logger.info(f"No skill matched for event {event.normalized.type}")
            return
        
        # Build context for the skill
        context = self.build_skill_context(event, skill_config)
        
        # Run conversation with skill
        result = await self.runner.run_prompt(
            group_name="webhooks",
            prompt=context.prompt,
            skills=[skill_config.skill],
        )
        
        # Send response back to source if configured
        if skill_config.respond_to_source:
            await self.send_response(event, result)
        
        # Acknowledge delivery
        await self.socket.emit("ack", {"event_id": event.id})
    
    def build_skill_context(self, event: WebhookEvent, skill_config) -> SkillContext:
        """Build context object for skill execution."""
        return SkillContext(
            prompt=f"""
A webhook event has been received that requires your attention.

**Event Type:** {event.normalized.type}
**Source:** {event.source.adapter}
**Action:** {event.normalized.get('action', 'N/A')}

**Event Data:**
```json
{json.dumps(event.normalized.data, indent=2)}
```

Please process this event according to your skill instructions.
            """,
            event=event,
            skill_config=skill_config,
        )
```

### Skill Loading for Events

Skills can be loaded dynamically based on event type:

```python
# src/openpaws/skills/router.py

class SkillRouter:
    """Routes webhook events to appropriate skills."""
    
    def __init__(self, skill_configs: list[WebhookSkillConfig]):
        self.configs = skill_configs
    
    def match(self, event: WebhookEvent) -> WebhookSkillConfig | None:
        """Find the first matching skill config for an event."""
        for config in self.configs:
            if self._matches(event, config.match):
                return config
        return None
    
    def _matches(self, event: WebhookEvent, criteria: dict) -> bool:
        """Check if event matches the criteria."""
        for key, expected in criteria.items():
            actual = self._get_nested(event, key)
            
            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        
        return True
    
    def _get_nested(self, obj, path: str):
        """Get nested attribute using dot notation."""
        parts = path.split(".")
        current = obj
        for part in parts:
            if hasattr(current, part):
                current = getattr(current, part)
            elif isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current
```

### Event-Specific Skills

Skills can be designed specifically for webhook events:

```markdown
# skills/github-pr-review/SKILL.md

## GitHub Pull Request Review Skill

This skill is activated when a `code.pull_request` event is received with action 
`opened` or `synchronize`.

### Context Provided

When activated, you will receive:
- `event.normalized.data.repository` - Full repository name (owner/repo)
- `event.normalized.data.number` - PR number
- `event.normalized.data.title` - PR title
- `event.normalized.data.author` - PR author username
- `event.normalized.data.head_ref` - Source branch
- `event.normalized.data.base_ref` - Target branch
- `event.normalized.data.url` - Link to PR

### Your Task

1. Fetch the PR diff using the GitHub API
2. Review the changes for:
   - Code quality issues
   - Security vulnerabilities
   - Performance concerns
   - Missing tests
3. Post a review comment with your findings
4. If issues are found, request changes; otherwise approve

### Response Format

Your response will be posted as a PR review comment. Use GitHub-flavored markdown.
Include inline suggestions using the suggestion syntax:

```suggestion
// Your suggested code here
```
```

---

## User Experience

### Registration Flow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Sign In                                                       │
│    ┌─────────────────────────────────────┐                      │
│    │  🔐 Sign in to Webhook Bridge       │                      │
│    │                                      │                      │
│    │  [Continue with GitHub]              │                      │
│    │  [Continue with Google]              │                      │
│    │                                      │                      │
│    │  Or sign in with email               │                      │
│    └─────────────────────────────────────┘                      │
├─────────────────────────────────────────────────────────────────┤
│ 2. Get Your Webhook URL                                          │
│    ┌─────────────────────────────────────┐                      │
│    │  Your webhook base URL:              │                      │
│    │                                      │                      │
│    │  https://events.openpaws.dev/       │                      │
│    │    hook/u/usr_abc123/               │                      │
│    │                                      │                      │
│    │  [Copy to Clipboard]                 │                      │
│    └─────────────────────────────────────┘                      │
├─────────────────────────────────────────────────────────────────┤
│ 3. Configure First Webhook Source                                │
│    ┌─────────────────────────────────────┐                      │
│    │  Add a webhook source               │                      │
│    │                                      │                      │
│    │  [GitHub]  [Slack]  [Stripe]        │                      │
│    │  [GitLab]  [Linear] [Custom...]     │                      │
│    └─────────────────────────────────────┘                      │
├─────────────────────────────────────────────────────────────────┤
│ 4. GitHub Setup Instructions                                     │
│    ┌─────────────────────────────────────┐                      │
│    │  Configure GitHub Webhook            │                      │
│    │                                      │                      │
│    │  1. Go to your repo → Settings →    │                      │
│    │     Webhooks → Add webhook          │                      │
│    │                                      │                      │
│    │  2. Payload URL:                     │                      │
│    │     https://events.openpaws.dev/    │                      │
│    │       hook/u/usr_abc123/github      │                      │
│    │                                      │                      │
│    │  3. Content type: application/json   │                      │
│    │                                      │                      │
│    │  4. Secret: [Generate Secret]        │                      │
│    │                                      │                      │
│    │  5. Events: Select events to send    │                      │
│    │                                      │                      │
│    │  [Test Connection]  [Complete Setup] │                      │
│    └─────────────────────────────────────┘                      │
├─────────────────────────────────────────────────────────────────┤
│ 5. Get API Key for Agent                                         │
│    ┌─────────────────────────────────────┐                      │
│    │  Connect your agent                  │                      │
│    │                                      │                      │
│    │  API Key: op_live_abc123...         │                      │
│    │  [Copy]  [Regenerate]                │                      │
│    │                                      │                      │
│    │  Add to your OpenPaws config:        │                      │
│    │  ┌────────────────────────────────┐ │                      │
│    │  │ channels:                       │ │                      │
│    │  │   webhook_bridge:               │ │                      │
│    │  │     api_key: ${WEBHOOK_API_KEY} │ │                      │
│    │  └────────────────────────────────┘ │                      │
│    └─────────────────────────────────────┘                      │
└─────────────────────────────────────────────────────────────────┘
```

### Dashboard

```
┌─────────────────────────────────────────────────────────────────┐
│ Webhook Bridge Dashboard                          [user@email]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  📊 Overview                     🔌 Agent Status                │
│  ┌─────────────────────────┐    ┌─────────────────────────┐    │
│  │ Events today:    156    │    │ ● Connected (1 agent)   │    │
│  │ Delivered:       154    │    │ Last event: 2 min ago   │    │
│  │ Failed:            2    │    └─────────────────────────┘    │
│  └─────────────────────────┘                                    │
│                                                                  │
│  📡 Configured Sources                                          │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Source    │ Status  │ Last Event │ Events (24h) │       │   │
│  ├───────────┼─────────┼────────────┼──────────────┼───────│   │
│  │ GitHub    │ ● Active│ 2 min ago  │     124      │ [···] │   │
│  │ Slack     │ ● Active│ 15 min ago │      28      │ [···] │   │
│  │ Stripe    │ ○ Idle  │ 2 days ago │       4      │ [···] │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                 [+ Add Source]  │
│                                                                  │
│  📜 Recent Events                                               │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ Time     │ Source │ Type              │ Status │        │   │
│  ├──────────┼────────┼───────────────────┼────────┼────────│   │
│  │ 10:32:15 │ GitHub │ pull_request      │ ✓ Sent │ [View] │   │
│  │ 10:31:02 │ GitHub │ push              │ ✓ Sent │ [View] │   │
│  │ 10:28:45 │ Slack  │ app_mention       │ ✓ Sent │ [View] │   │
│  │ 10:15:33 │ GitHub │ issues            │ ✗ Fail │ [View] │   │
│  │ 10:12:01 │ GitHub │ pull_request      │ ✓ Sent │ [View] │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                   [View All →]  │
└─────────────────────────────────────────────────────────────────┘
```

### CLI Commands

```bash
# Configure webhook bridge in OpenPaws
openpaws webhooks setup
# Interactive setup wizard

# List configured sources  
openpaws webhooks sources
# github    ● active   124 events/24h
# slack     ● active    28 events/24h
# stripe    ○ idle       4 events/24h

# View recent events
openpaws webhooks events
# 10:32:15  github  pull_request   ✓ delivered
# 10:31:02  github  push           ✓ delivered
# 10:28:45  slack   app_mention    ✓ delivered

# View specific event details
openpaws webhooks event evt_01HQXYZ789
# Event: evt_01HQXYZ789
# Source: github / pull_request
# Received: 2024-03-15 10:32:15
# Status: delivered
# 
# Normalized Data:
#   type: code.pull_request
#   action: opened
#   repository: jpshackelford/open-paw
#   ...

# Test webhook configuration
openpaws webhooks test github
# Sending test event to github webhook...
# ✓ Received response in 45ms
# ✓ Agent received event

# Replay an event (for debugging)
openpaws webhooks replay evt_01HQXYZ789
# Replaying event evt_01HQXYZ789...
# ✓ Event delivered to agent
```

---

## Deployment Architecture

### Production Setup

```
                        ┌─────────────────────────────────────┐
                        │           Load Balancer              │
                        │      (AWS ALB / Cloudflare)          │
                        └────────────────┬────────────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            │                            │                            │
            ▼                            ▼                            ▼
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│   Web Server 1      │    │   Web Server 2      │    │   Web Server N      │
│   (Webhook RX)      │    │   (Webhook RX)      │    │   (Webhook RX)      │
└──────────┬──────────┘    └──────────┬──────────┘    └──────────┬──────────┘
           │                          │                          │
           └──────────────────────────┼──────────────────────────┘
                                      │
                                      ▼
                        ┌─────────────────────────────────────┐
                        │           Message Queue              │
                        │        (Redis Streams / SQS)         │
                        └────────────────┬────────────────────┘
                                         │
            ┌────────────────────────────┼────────────────────────────┐
            │                            │                            │
            ▼                            ▼                            ▼
┌─────────────────────┐    ┌─────────────────────┐    ┌─────────────────────┐
│  Socket.IO Server 1 │    │  Socket.IO Server 2 │    │  Socket.IO Server N │
│  (Agent Connections)│    │  (Agent Connections)│    │  (Agent Connections)│
└──────────┬──────────┘    └──────────┬──────────┘    └──────────┬──────────┘
           │                          │                          │
           └──────────────────────────┼──────────────────────────┘
                                      │
                        ┌─────────────┴─────────────┐
                        │                           │
                        ▼                           ▼
              ┌─────────────────┐        ┌─────────────────┐
              │    PostgreSQL   │        │      Redis      │
              │  (Events, Users)│        │  (Sessions,     │
              │                 │        │   Pub/Sub)      │
              └─────────────────┘        └─────────────────┘
```

### Scaling Considerations

| Component | Scaling Strategy |
|-----------|------------------|
| Webhook receivers | Horizontal, stateless |
| Socket.IO servers | Horizontal with Redis adapter for pub/sub |
| PostgreSQL | Read replicas, partitioning by user_id |
| Redis | Cluster mode for high availability |

---

## Implementation Phases

### Phase 1: MVP (4-6 weeks)
- [ ] Core webhook receiver with GitHub adapter
- [ ] Basic user registration (API key only)
- [ ] SQLite storage (single instance)
- [ ] Socket.IO server with authentication
- [ ] OpenPaws channel adapter
- [ ] Basic dashboard (event log)

### Phase 2: Production Ready (4-6 weeks)
- [ ] Additional adapters (Slack, GitLab, Stripe)
- [ ] PostgreSQL migration
- [ ] User OAuth (GitHub, Google)
- [ ] Secret encryption
- [ ] Rate limiting
- [ ] Full dashboard with management UI

### Phase 3: Scale & Extend (ongoing)
- [ ] Custom adapter upload
- [ ] Multi-region deployment
- [ ] Advanced filtering and routing
- [ ] Webhook transformation rules
- [ ] Event replay and debugging tools
- [ ] Billing integration (for hosted service)

---

## Open Questions

1. **Hosting model**: Self-hosted only, or also offer managed service?
   - Managed service reduces friction but adds operational burden
   - Self-hosted gives users full control but requires more setup

2. **Adapter contribution model**: How do community members contribute adapters?
   - Pull requests to main repo?
   - Separate adapter registry?
   - User-uploadable adapters?

3. **Event transformation**: Should we support user-defined transformations?
   - Could use simple templates
   - Could use JavaScript/Python snippets (security implications)
   - Could use a DSL

4. **Offline delivery**: How long to queue events for offline agents?
   - Hours? Days? Configurable?
   - What about agents that never reconnect?

5. **Response routing**: How to handle sending responses back to webhook sources?
   - Agent needs credentials for each source
   - Central service could proxy responses
   - Each source has different API patterns

---

## References

- [NanoClaw](https://github.com/qwibitai/nanoclaw) - Inspiration for event-driven agent architecture
- [Webhook.site](https://webhook.site/) - Similar webhook inspection tool
- [Svix](https://www.svix.com/) - Webhook delivery platform (different use case)
- [Socket.IO](https://socket.io/) - Real-time communication library
- [GitHub Webhooks](https://docs.github.com/webhooks) - Example webhook source
