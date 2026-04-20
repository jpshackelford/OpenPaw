# Distributed Agent Pool: Self-Hosted Remote Sandboxes for OpenPaws

**Status:** Draft  
**Related PRs:** #1 (OpenHands Cloud Workspace)

## Problem Statement

OpenPaws currently supports two execution modes:
1. **Local** - Tasks run on the same machine as the OpenPaws daemon
2. **Cloud** - Tasks run on OpenHands Cloud sandboxes (PR #1)

However, many use cases require running tasks on specific machines that the user controls:
- **macOS** for iOS/Swift development and testing
- **Windows** for .NET development
- **GPU machines** for ML training/inference
- **On-premises servers** with access to internal resources
- **Specific hardware** (embedded devices, test equipment)

Cloud sandboxes can't satisfy these requirements. We need a way for users to contribute their own machines to an "agent pool" that OpenPaws can dispatch tasks to.

## Solution Overview

**Distributed Agent Pool** allows self-hosted machines to join an OpenPaws deployment as remote execution environments. The key insight is that remote agents initiate **outbound** connections to the main OpenPaws server, which works through NAT and firewalls.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  OpenPaws Main (Controller)                                                   │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐                     │
│  │   Scheduler   │──│ Agent Registry│──│  Task Queue   │                     │
│  └───────────────┘  └───────┬───────┘  └───────────────┘                     │
│                             │                                                 │
│  ┌───────────────┐          │ WebSocket (agents connect in)                  │
│  │    Gateway    │◀─────────┘                                                │
│  │    Server     │                                                            │
│  └───────┬───────┘                                                            │
└──────────┼────────────────────────────────────────────────────────────────────┘
           │
           │ Outbound connections (through firewalls)
           │
    ┌──────┴──────┬─────────────────┬─────────────────┐
    │             │                 │                 │
┌───┴───┐    ┌────┴────┐      ┌─────┴─────┐    ┌──────┴──────┐
│macOS  │    │ Linux   │      │  Windows  │    │ On-Prem     │
│Mini   │    │ GPU     │      │  Server   │    │ Server      │
│       │    │ Server  │      │           │    │             │
│openpaws│   │openpaws │      │ openpaws  │    │ openpaws    │
│-agent │    │-agent   │      │ -agent    │    │ -agent      │
└───────┘    └─────────┘      └───────────┘    └─────────────┘
```

### Key Design Decisions

1. **Outbound Connection Model** - Remote agents connect TO the main server (like the webhook-bridge pattern), not the other way around. This works through NAT and most firewalls.

2. **Agent-Server Reuse** - Each remote machine runs the `openhands-agent-server` from software-agent-sdk, providing a proven execution environment with bash, file ops, and git operations.

3. **Capability-Based Routing** - Tasks specify requirements (OS, tags, resources); the scheduler matches tasks to capable agents.

4. **Secure by Default** - All connections authenticated and encrypted. No secrets stored on remote agents.

---

## Conversation & Workspace Lifecycle

A key design question: **Can we run multiple tasks with clean contexts, or is it one long conversation?**

### Answer: Each Task = Fresh Conversation

The software-agent-sdk supports multiple independent conversations against the same workspace. Each `Conversation` object:
- Has its own conversation ID
- Has its own message history (clean context)
- Shares the workspace (filesystem, installed packages)
- Is closed independently (`conversation.close()`)

**Task Execution Model:**

```
Remote Agent (persistent)
    │
    └── agent-server (persistent, running)
            │
            ├── Task 1 arrives → Create Conversation A → Run → Close → Cleanup
            │
            ├── Task 2 arrives → Create Conversation B → Run → Close → Cleanup
            │
            └── Task 3 arrives → Create Conversation C → Run → Close → Cleanup
```

Each scheduled task gets:
1. **Fresh conversation** - No message history pollution from previous tasks
2. **Clean workspace directory** - Task-specific working directory
3. **Isolated secrets** - Only that task's secrets are available

### Workspace Directory Strategy

Each task should get its own workspace directory to prevent file conflicts:

```
/var/openpaws/workspaces/
├── task_abc123_20240315_080000/    # Task 1's workspace
│   └── (cloned repos, generated files)
├── task_def456_20240315_090000/    # Task 2's workspace  
│   └── (cloned repos, generated files)
└── task_ghi789_20240315_100000/    # Task 3's workspace
    └── (cloned repos, generated files)
```

**Cleanup Policy Options:**
```yaml
agent:
  workspace:
    cleanup_policy: 
      on_success: immediate      # Delete workspace when task succeeds
      on_failure: retain_24h     # Keep failed task workspace for debugging
      max_age: 7d                # Delete all workspaces older than 7 days
      max_total_size: 50GB       # Delete oldest when total exceeds limit
```

### Implementation in Remote Agent

```python
class RemoteAgentExecutor:
    """Runs on the remote machine, manages task execution."""
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self.agent_server_port = config.sandbox.port
        
        # Workspace manager handles directory allocation/cleanup
        # This is OUR responsibility, not the SDK's
        self.workspace_manager = LocalSandboxManager(
            base_dir=Path(config.workspace.base_dir),
            cleanup_policy=config.workspace.cleanup_policy,
        )
        
        # Start background cleanup task
        self._start_cleanup_scheduler()
    
    async def execute_task(self, task: TaskAssignment) -> TaskResult:
        # 1. Create task-specific workspace directory
        #    For local sandbox, WE manage this (not SDK)
        workspace_dir = self.workspace_manager.create_task_workspace(task.task_id)
        
        # 2. Create RemoteWorkspace pointing to our local agent-server
        #    The working_dir tells agent-server where to execute commands
        workspace = RemoteWorkspace(
            host=f"http://localhost:{self.agent_server_port}",
            working_dir=str(workspace_dir),
        )
        
        # 3. Create fresh Conversation (clean context!)
        #    Each task gets independent message history
        conversation = Conversation(
            agent=self.agent,
            workspace=workspace,
            secrets=task.secrets,  # Task-specific secrets only
            delete_on_close=True,  # Clean up tmux sessions etc.
        )
        
        success = False
        try:
            # 4. Run the task
            conversation.send_message(task.prompt)
            conversation.run()
            
            # 5. Extract result
            success = True
            return TaskResult(success=True, ...)
            
        except Exception as e:
            return TaskResult(success=False, error=str(e))
            
        finally:
            # 6. Clean up conversation (SDK handles tmux, etc.)
            conversation.close()
            
            # 7. Clean up workspace directory (WE handle this)
            self.workspace_manager.cleanup_workspace(workspace_dir, success)
    
    def _start_cleanup_scheduler(self):
        """Run periodic cleanup of old workspaces."""
        async def cleanup_loop():
            while True:
                await asyncio.sleep(3600)  # Every hour
                self.workspace_manager.run_cleanup_sweep()
        asyncio.create_task(cleanup_loop())
```

**Key point:** In local sandbox mode, workspace directory management is the `openpaws-agent`'s responsibility:

| Responsibility | Local Sandbox | Docker Sandbox |
|---------------|---------------|----------------|
| Create workspace dir | `openpaws-agent` | Container provides |
| Set `working_dir` | `openpaws-agent` | Container provides |
| File isolation | `openpaws-agent` (soft) | Container (hard) |
| Cleanup workspace | `openpaws-agent` | Container removed |
| Process isolation | None | Container provides |

### Key SDK Behaviors We Rely On

From investigating the SDK:

1. **`Conversation` is independent** - Creating a new `Conversation` gives a fresh message history
2. **`delete_on_close=True`** - Server-side resources (tmux sessions, etc.) are cleaned up
3. **`RemoteWorkspace.working_dir`** - Can be set per-conversation to isolate file operations
4. **Agent-server persists** - The HTTP server stays running, conversations come and go

This model gives us:
- ✅ Clean context per task (no conversation pollution)
- ✅ Isolated workspaces per task (no file conflicts)
- ✅ Persistent agent-server (no startup overhead)
- ✅ Configurable cleanup (debugging vs. disk space)

---

## Architecture Components

### 1. Agent Gateway (Main Server)

New component in OpenPaws main that accepts incoming WebSocket connections from remote agents.

**Responsibilities:**
- Accept and authenticate agent connections
- Maintain persistent connections with heartbeat
- Route task assignments to connected agents
- Receive task results and events
- Handle reconnection gracefully

**API:**
```
WebSocket: wss://openpaws.example.com:9000/agent/connect
  - Agent authenticates with token
  - Server assigns agent ID
  - Bidirectional event stream established
```

### 2. Agent Registry

Tracks registered agents and their status.

**Data Model:**
```python
@dataclass
class RegisteredAgent:
    id: str                    # Unique agent identifier
    name: str                  # Human-friendly name
    capabilities: AgentCapabilities
    status: AgentStatus        # online, offline, busy
    connection_id: str | None  # WebSocket connection ID
    last_heartbeat: datetime
    current_task: str | None   # Task ID if executing
    registered_at: datetime
    metadata: dict[str, Any]   # User-defined metadata

@dataclass 
class AgentCapabilities:
    os: str                    # linux, macos, windows
    arch: str                  # amd64, arm64
    tags: list[str]            # User-defined: [ios, gpu, docker]
    resources: ResourceLimits
    sandbox_type: str          # local, docker, container
```

### 3. Remote Agent Daemon (`openpaws-agent`)

New package/binary that runs on remote machines.

**Responsibilities:**
- Manage openhands-agent-server lifecycle
- Connect to main OpenPaws via WebSocket
- Authenticate and register capabilities
- Receive and execute assigned tasks
- Stream events back to main
- Handle graceful shutdown

**Process Model:**
```
openpaws-agent (daemon)
    │
    ├── WebSocket client → Main OpenPaws Gateway
    │
    └── openhands-agent-server (subprocess)
            │
            └── Task execution workspace
```

### 4. Task Router

Enhanced scheduler that considers agent availability.

**Routing Logic:**
```python
def route_task(task: TaskConfig) -> str | None:
    """Find best agent for task, return agent_id or None."""
    
    requirements = task.runtime.requirements
    candidates = []
    
    for agent in registry.get_online_agents():
        if not agent.matches_requirements(requirements):
            continue
        if agent.is_busy() and not agent.can_queue():
            continue
        candidates.append(agent)
    
    if not candidates:
        return None
    
    # Simple: pick least loaded
    # Future: affinity, priority, resource scoring
    return min(candidates, key=lambda a: a.queue_depth).id
```

---

## Security Design

### Authentication Flow

```
┌─────────────┐                          ┌─────────────┐
│   Remote    │                          │   OpenPaws  │
│   Agent     │                          │    Main     │
└──────┬──────┘                          └──────┬──────┘
       │                                        │
       │  1. Connect (TLS)                      │
       │───────────────────────────────────────▶│
       │                                        │
       │  2. AUTH { token: "..." }              │
       │───────────────────────────────────────▶│
       │                                        │
       │  3. AUTH_OK { agent_id: "...", ... }   │
       │◀───────────────────────────────────────│
       │                                        │
       │  4. REGISTER { capabilities: {...} }   │
       │───────────────────────────────────────▶│
       │                                        │
       │  5. REGISTERED { ... }                 │
       │◀───────────────────────────────────────│
       │                                        │
       │  6. Bidirectional event stream         │
       │◀──────────────────────────────────────▶│
```

### Authentication Methods

**Phase 1: Pre-Shared Token**
- Admin generates token in main OpenPaws
- Token given to agent during setup
- Simple, works everywhere

```bash
# Generate token on main
openpaws agent create-token --name "mac-mini-01" --ttl 24h

# On remote machine
openpaws-agent join --token="eyJhbGciOi..."
```

**Phase 2: Certificate-Based (mTLS)**
- Main OpenPaws runs internal CA
- Agent CSR submitted during registration
- Certificates used for all subsequent connections
- Enables certificate revocation

**Phase 3: OIDC Integration (Enterprise)**
- Integrate with corporate identity provider
- Agent authenticates via device flow
- Useful for enterprise deployments

### Authorization Rules

1. **Agent can only:**
   - Execute tasks assigned to it
   - Report status and events
   - Access secrets passed with task

2. **Agent cannot:**
   - Query other agents
   - Access task queue directly
   - Retrieve secrets for other tasks

3. **Task isolation:**
   - Each task runs in clean workspace
   - Workspace cleaned after completion
   - Secrets cleared from memory

### Secrets Handling

```
┌─────────────┐                          ┌─────────────┐
│   OpenPaws  │                          │   Remote    │
│    Main     │                          │   Agent     │
└──────┬──────┘                          └──────┬──────┘
       │                                        │
       │  TASK_ASSIGN {                         │
       │    task_id: "...",                     │
       │    prompt: "...",                      │
       │    secrets: {                          │
       │      GITHUB_TOKEN: "...",   ◀── Encrypted in transit
       │      NPM_TOKEN: "..."                  │
       │    }                                   │
       │  }                                     │
       │───────────────────────────────────────▶│
       │                                        │
       │                           Secrets injected as env vars
       │                           for task duration only
       │                                        │
       │  TASK_COMPLETE { ... }                 │
       │◀───────────────────────────────────────│
       │                                        │
       │                           Secrets cleared from memory
```

**Security properties:**
- Secrets never written to disk on agent
- Secrets only in memory during task execution
- Secrets not logged
- Task workspace deleted after completion

---

## Setup Modes

### Mode 1: Manual Setup with Join Token

Simplest approach, works through any network topology.

**On Main OpenPaws:**
```bash
# Create a registration token (valid 24h)
openpaws agent token create --name "mac-mini-build" --expires 24h
# Output: Token: eyJhbGciOiJIUzI1NiIs...

# Or create a permanent agent credential
openpaws agent create --name "mac-mini-build"
# Output: Agent ID: agent_abc123
#         Token: sk_agent_xyz789...
```

**On Remote Machine:**
```bash
# Install openpaws-agent
pip install openpaws-agent
# or: brew install openpaws-agent (macOS)
# or: Download binary from releases

# Join the pool (interactive - will prompt for missing values)
openpaws-agent join \
  --server wss://openpaws.example.com:9000 \
  --token "eyJhbGciOiJIUzI1NiIs..." \
  --name "mac-mini-build" \
  --tags ios,swift,xcode \
  --os macos \
  --arch arm64

# Run as service (daemon mode)
openpaws-agent service install
openpaws-agent service start

# Or run in foreground (for testing/debugging)
openpaws-agent run --foreground
```

**Complete `openpaws-agent` CLI:**
```bash
# Main commands
openpaws-agent join [OPTIONS]     # Register with main server
openpaws-agent run [OPTIONS]      # Start the agent daemon
openpaws-agent status             # Show agent status and current task
openpaws-agent stop               # Stop the agent daemon

# Service management (launchd on macOS, systemd on Linux)
openpaws-agent service install    # Install as system service
openpaws-agent service uninstall  # Remove system service
openpaws-agent service start      # Start the service
openpaws-agent service stop       # Stop the service
openpaws-agent service status     # Check service status
openpaws-agent service logs       # View service logs

# Configuration
openpaws-agent config show        # Display current configuration
openpaws-agent config edit        # Edit configuration file

# Join options:
#   --server URL       Main OpenPaws server URL (required)
#   --token TOKEN      Registration token (required)
#   --name NAME        Agent display name (default: hostname)
#   --os OS            Operating system (auto-detected)
#   --arch ARCH        CPU architecture (auto-detected)
#   --tags TAG,...     Comma-separated capability tags
#   --config FILE      Path to write config (default: /etc/openpaws-agent/config.yaml)

# Run options:
#   --foreground       Run in foreground, don't daemonize
#   --config FILE      Config file path
#   --log-level LEVEL  Logging level (debug, info, warning, error)
```

### Mode 2: SSH Auto-Setup (Network Accessible)

When the main server can reach the remote machine via SSH.

```bash
# From main OpenPaws machine
openpaws agent setup \
  --ssh user@192.168.1.100 \
  --name "mac-mini-build" \
  --tags ios,swift,xcode

# This will:
# 1. SSH into the remote machine
# 2. Install openpaws-agent package
# 3. Generate and inject credentials
# 4. Configure and start the service
# 5. Wait for agent to register back
```

**SSH Setup Flow:**
```
┌─────────────┐                          ┌─────────────┐
│   OpenPaws  │         SSH              │   Remote    │
│    Main     │─────────────────────────▶│   Machine   │
└──────┬──────┘                          └──────┬──────┘
       │                                        │
       │  1. SSH connect                        │
       │───────────────────────────────────────▶│
       │                                        │
       │  2. Install openpaws-agent             │
       │───────────────────────────────────────▶│
       │                                        │
       │  3. Write config with credentials      │
       │───────────────────────────────────────▶│
       │                                        │
       │  4. Start service                      │
       │───────────────────────────────────────▶│
       │                                        │
       │◀───────────────────────────────────────│
       │  5. Agent connects back via WebSocket  │
       │                                        │
```

### Mode 3: Tunnel Mode (Advanced)

For cases where direct API access to the agent-server is needed.

```bash
# Agent establishes reverse tunnel
openpaws-agent join \
  --server wss://openpaws.example.com:9000 \
  --token "..." \
  --tunnel-enabled \
  --tunnel-port 8080  # Local agent-server port

# Main can now proxy requests through the tunnel
# Useful for: streaming logs, large file transfers, debugging
```

---

## Configuration

### Main OpenPaws Config

```yaml
# ~/.openpaws/config.yaml

agent_pool:
  enabled: true
  
  gateway:
    host: 0.0.0.0
    port: 9000
    # External URL agents use to connect
    external_url: wss://openpaws.example.com:9000
  
  security:
    # TLS configuration
    tls:
      enabled: true
      cert_file: /etc/openpaws/tls/server.crt
      key_file: /etc/openpaws/tls/server.key
      # For mTLS (Phase 2)
      client_ca_file: /etc/openpaws/tls/ca.crt
    
    # Token configuration
    token:
      # Secret for signing tokens
      signing_key: ${OPENPAWS_TOKEN_SECRET}
      # Default token expiry
      default_ttl: 24h
  
  # Connection settings
  connection:
    heartbeat_interval: 30s
    heartbeat_timeout: 90s
    reconnect_delay: 5s
    max_reconnect_attempts: 10
  
  # Resource defaults
  defaults:
    max_concurrent_tasks: 2
    task_timeout: 1h
    workspace_cleanup: true

# Task routing to agents
tasks:
  build-ios:
    schedule: "0 */2 * * *"
    group: main
    prompt: "Build and test iOS app"
    runtime:
      type: agent
      requirements:
        os: macos
        tags: [ios, xcode]
  
  train-model:
    schedule: "0 2 * * *"
    group: main
    prompt: "Train ML model"
    runtime:
      type: agent
      requirements:
        tags: [gpu, cuda]
      
  # Fallback behavior
  regular-task:
    schedule: "0 9 * * *"
    group: main
    prompt: "Daily report"
    runtime:
      type: auto  # Use agent if available, else local
      preferences:
        tags: [fast]
```

### Remote Agent Config

```yaml
# /etc/openpaws-agent/config.yaml

agent:
  # Display name in registry
  name: mac-mini-build-01
  
  # Main server connection
  server:
    url: wss://openpaws.example.com:9000
    # Auth token (prefer env var)
    token: ${OPENPAWS_AGENT_TOKEN}
  
  # Capabilities advertised to scheduler
  capabilities:
    os: macos
    arch: arm64
    tags:
      - ios
      - swift
      - xcode-15
    resources:
      cpus: 8
      memory_gb: 16
      disk_gb: 256
  
  # Execution settings
  execution:
    max_concurrent_tasks: 2
    task_timeout: 2h
    
  # Workspace configuration
  workspace:
    type: local  # or "docker" - see Sandbox Types below
    base_dir: /var/openpaws/workspace
    cleanup_on_complete: true
    
  # Agent-server settings
  sandbox:
    port: 8080
    working_dir: /workspace
    # Docker-specific (if workspace.type == docker)
    docker:
      image: ghcr.io/openhands/agent-server:latest-python
      mount_docker_socket: false
```

### Sandbox Types: Local vs Docker

A critical configuration choice for remote agents:

**Local Sandbox (`workspace.type: local`)**

The agent-server runs directly on the host OS with full access to system resources.

```yaml
workspace:
  type: local
  base_dir: /var/openpaws/workspace
```

**Use when:**
- ✅ macOS agents for iOS development (needs Xcode, Simulator, Keychain)
- ✅ Windows agents for .NET (needs Visual Studio, Windows SDK)
- ✅ GPU access required (CUDA drivers on host)
- ✅ Hardware access needed (USB devices, network equipment)
- ✅ Native toolchains that don't work in containers
- ✅ Access to system keychains/credential stores

**Trade-offs:**
- ⚠️ Less isolation between tasks
- ⚠️ Agent can modify host system
- ⚠️ Must trust the tasks being executed

**Important: Workspace Management Required**

Unlike Docker sandbox (where the container provides isolation), local sandbox requires `openpaws-agent` to handle workspace allocation and cleanup:

```python
class LocalSandboxManager:
    """Manages workspace directories for local sandbox mode."""
    
    def __init__(self, base_dir: Path, cleanup_policy: CleanupPolicy):
        self.base_dir = base_dir
        self.cleanup_policy = cleanup_policy
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def create_task_workspace(self, task_id: str) -> Path:
        """Create isolated workspace directory for a task."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        workspace_dir = self.base_dir / f"task_{task_id}_{timestamp}"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir
    
    def cleanup_workspace(self, workspace_dir: Path, task_succeeded: bool):
        """Clean up workspace based on policy."""
        if task_succeeded and self.cleanup_policy.on_success == "immediate":
            shutil.rmtree(workspace_dir, ignore_errors=True)
        elif not task_succeeded and self.cleanup_policy.on_failure == "retain":
            # Keep for debugging, will be cleaned by age policy
            pass
        # Age-based cleanup handled by background task
    
    def run_cleanup_sweep(self):
        """Background task: clean old workspaces."""
        cutoff = datetime.now() - self.cleanup_policy.max_age
        for workspace in self.base_dir.iterdir():
            if workspace.is_dir() and workspace.stat().st_mtime < cutoff.timestamp():
                shutil.rmtree(workspace, ignore_errors=True)
```

The `openpaws-agent` daemon must:
1. Create unique workspace directory per task
2. Pass `working_dir` to the `RemoteWorkspace` when creating conversation
3. Track workspace → task mapping for cleanup
4. Run periodic cleanup sweeps for age-based retention
5. Respect disk space limits (delete oldest first when full)

**Docker Sandbox (`workspace.type: docker`)**

The agent-server runs inside a Docker container, isolated from the host.

```yaml
workspace:
  type: docker
  docker:
    image: ghcr.io/openhands/agent-server:latest-python
    # Mount specific directories if needed
    mounts:
      - /data/models:/models:ro    # Read-only model access
    # Resource limits
    memory_limit: 8g
    cpu_limit: 4
```

**Use when:**
- ✅ Running untrusted or experimental code
- ✅ Need reproducible environments
- ✅ Linux-based toolchains (Python, Node, Go, etc.)
- ✅ Want strong isolation between tasks
- ✅ Multi-tenant agent pools

**Trade-offs:**
- ❌ No access to host GUI (macOS Simulator, etc.)
- ❌ No native Keychain/credential store access
- ❌ GPU passthrough requires extra configuration
- ❌ Some performance overhead

### macOS-Specific Considerations

For macOS agents doing iOS/Swift development:

```yaml
agent:
  name: mac-mini-ios-builder
  
  capabilities:
    os: macos
    arch: arm64
    tags:
      - ios
      - swift
      - xcode-15
      - simulator
      - codesign
  
  workspace:
    type: local  # MUST be local for iOS development
    base_dir: ~/Library/OpenPaws/workspaces
    
  # Security: restrict what the agent can do
  security:
    # Only allow access to specific directories
    allowed_paths:
      - ~/Developer
      - ~/Library/Developer
      - /Applications/Xcode.app
    # Prevent dangerous operations
    deny_commands:
      - rm -rf /
      - sudo
```

**Why local is required for iOS:**

| Resource | Local | Docker |
|----------|-------|--------|
| Xcode CLI tools | ✅ | ✅ (limited) |
| iOS Simulator | ✅ | ❌ |
| Code signing | ✅ | ❌ |
| Keychain Access | ✅ | ❌ |
| Device deployment | ✅ | ❌ |
| SwiftUI Previews | ✅ | ❌ |

The agent advertises capabilities like `simulator` and `codesign` only if running in local mode with proper access.

---

## Protocol Specification

### WebSocket Messages

All messages are JSON with a `type` field.

#### Agent → Main

```typescript
// Authentication
{ type: "AUTH", token: string }

// Registration
{ type: "REGISTER", capabilities: AgentCapabilities }

// Heartbeat
{ type: "HEARTBEAT", status: AgentStatus, current_task?: string }

// Task events
{ type: "TASK_STARTED", task_id: string }
{ type: "TASK_EVENT", task_id: string, event: Event }
{ type: "TASK_COMPLETED", task_id: string, result: TaskResult }
{ type: "TASK_FAILED", task_id: string, error: string }
```

#### Main → Agent

```typescript
// Authentication response
{ type: "AUTH_OK", agent_id: string }
{ type: "AUTH_FAILED", error: string }

// Registration response  
{ type: "REGISTERED", agent_id: string }

// Task assignment
{ 
  type: "TASK_ASSIGN", 
  task_id: string,
  task_name: string,
  prompt: string,
  group: string,
  secrets: Record<string, string>,
  config: TaskExecutionConfig
}

// Task control
{ type: "TASK_CANCEL", task_id: string }

// Connection control
{ type: "PING" }
{ type: "DISCONNECT", reason: string }
```

---

## Implementation Phases

### Phase 1: Foundation (MVP)

**Goal:** Basic distributed execution working end-to-end.

**Stories:**
1. **Gateway Server** - Accept WebSocket connections, authenticate with tokens
2. **Agent Registry** - In-memory registry of connected agents
3. **Remote Agent CLI** - `openpaws-agent join` command
4. **Task Assignment** - Route tasks to specific agents by ID
5. **Event Streaming** - Forward agent events to main
6. **Basic Config** - YAML configuration for both sides

**Deliverables:**
- `openpaws-agent` package
- New `agent_pool` config section
- CLI: `openpaws agent list`, `openpaws agent token create`
- Task config: `runtime: { type: agent, agent_id: "..." }`

### Phase 2: Intelligent Routing

**Goal:** Automatic task-to-agent matching.

**Stories:**
1. **Capability Matching** - Match task requirements to agent capabilities
2. **Health Monitoring** - Track agent health, mark unhealthy
3. **Load Balancing** - Distribute tasks across available agents
4. **Queue Management** - Queue tasks when agents busy
5. **Auto-Recovery** - Handle agent disconnection, task reassignment

**Deliverables:**
- Enhanced task routing logic
- Health dashboard in CLI
- Task queue persistence

### Phase 3: Setup Automation

**Goal:** Easy agent deployment.

**Stories:**
1. **SSH Auto-Setup** - Deploy agent via SSH
2. **Installer Packages** - macOS pkg, Windows msi, Linux deb/rpm
3. **Service Management** - Install/uninstall system service
4. **Update Mechanism** - Auto-update agent when new version available

**Deliverables:**
- `openpaws agent setup --ssh` command
- Platform-specific installers
- Auto-update capability

### Phase 4: Advanced Features

**Goal:** Enterprise-ready features.

**Stories:**
1. **mTLS Authentication** - Certificate-based auth
2. **Agent Groups** - Logical grouping for routing
3. **Priority Queues** - Prioritize certain tasks/groups
4. **Resource Quotas** - Limit resource usage per task
5. **Tunnel Support** - Direct API access through tunnel
6. **Audit Logging** - Comprehensive audit trail
7. **Web Dashboard** - Visual agent management

---

## Relationship to Existing Work

### PR #1: OpenHands Cloud Workspace

The `runtime` field introduced in PR #1 already supports `local`, `cloud`, and `auto`. This design extends it with `agent` type:

```yaml
runtime:
  type: agent         # New type
  requirements:       # New: capability requirements
    os: macos
    tags: [ios]
  agent_id: "..."     # Optional: specific agent
```

The `_should_use_cloud()` method in runner.py becomes `_resolve_runtime()` that handles all four types.

### Webhook Bridge Design

The webhook-bridge design uses the same outbound connection pattern. We could potentially share infrastructure:

- Same WebSocket gateway server
- Same authentication mechanism
- Different message types (webhook events vs task assignments)

### software-agent-sdk

Remote agents reuse `openhands-agent-server` from the SDK. The `RemoteWorkspace` class already knows how to talk to it. The new code is primarily:

1. The WebSocket bridge between main and agent
2. Task lifecycle management
3. Agent registry and routing

---

## Open Questions

1. **Persistence:** Should agent registry survive main restart? (Probably yes, SQLite)

2. **Multi-tenancy:** Do we need to support multiple users sharing an agent pool?

3. **Billing/Quotas:** Should there be resource tracking for cost allocation?

4. **Agent Updates:** How to handle agent-server version mismatches?

5. **Network Policies:** What if agent needs to access internal resources? (VPN? Firewall rules?)

6. **Workspace Caching:** Should agents cache common dependencies between tasks?

---

## Appendix: Alternative Approaches Considered

### A. Direct SSH from Main

**Approach:** Main SSH's into agent for each task.
**Rejected because:** 
- Requires inbound SSH access (often blocked)
- Connection setup overhead for each task
- No persistent context

### B. VPN Mesh (e.g., Tailscale)

**Approach:** All machines on VPN mesh, direct communication.
**Rejected because:**
- Additional infrastructure requirement
- Complexity for simple deployments
- Still need agent management layer

### C. Message Queue (e.g., Redis, RabbitMQ)

**Approach:** Tasks posted to queue, agents pull.
**Rejected because:**
- Additional infrastructure requirement
- More complex deployment
- Loses real-time event streaming benefits

The WebSocket approach was chosen because:
- Works through NAT/firewalls (outbound only)
- Real-time bidirectional communication
- No additional infrastructure needed
- Similar pattern already proven (webhook-bridge)
