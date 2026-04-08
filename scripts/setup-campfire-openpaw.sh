#!/usr/bin/env bash
#
# Setup script for Campfire + OpenPaws integration
#
# This script:
# 1. Installs the ONCE CLI (for managing Campfire)
# 2. Deploys Campfire using ONCE
# 3. Installs OpenPaws from the current branch
# 4. Generates an initial OpenPaws configuration
#
# Usage:
#   ./scripts/setup-campfire-openpaw.sh [OPTIONS]
#
# Options:
#   --hostname <host>    Hostname for Campfire (default: campfire.localhost)
#   --disable-tls        Disable TLS (for local development)
#   --openpaws-branch    Git branch to install OpenPaws from (default: feature/campfire-adapter)
#   --skip-campfire      Skip Campfire installation (if already installed)
#   --skip-openpaws      Skip OpenPaws installation
#   --help               Show this help message

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Defaults
CAMPFIRE_HOSTNAME="campfire.localhost"
DISABLE_TLS="false"
OPENPAWS_BRANCH="feature/campfire-adapter"
OPENPAWS_REPO="https://github.com/jpshackelford/OpenPaw.git"
SKIP_CAMPFIRE="false"
SKIP_OPENPAWS="false"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
}

show_help() {
    cat << 'EOF'
Setup script for Campfire + OpenPaws integration

This script installs and configures:
  - ONCE CLI (for managing Campfire)
  - Campfire (self-hosted group chat)
  - OpenPaws (AI assistant with chat connectors)

Usage:
  ./scripts/setup-campfire-openpaw.sh [OPTIONS]

Options:
  --hostname <host>      Hostname for Campfire (default: campfire.localhost)
  --disable-tls          Disable TLS (recommended for local development)
  --openpaws-branch <b>  Git branch to install OpenPaws from (default: feature/campfire-adapter)
  --skip-campfire        Skip Campfire installation (if already installed)
  --skip-openpaws        Skip OpenPaws installation
  --help                 Show this help message

Examples:
  # Local development setup (no TLS)
  ./scripts/setup-campfire-openpaw.sh --disable-tls

  # Production setup with custom hostname
  ./scripts/setup-campfire-openpaw.sh --hostname chat.example.com

  # Only install OpenPaws (Campfire already running)
  ./scripts/setup-campfire-openpaw.sh --skip-campfire

After installation:
  1. Open Campfire at http://${CAMPFIRE_HOSTNAME:-campfire.localhost}
  2. Create a bot in Campfire Admin → Bots
  3. Copy the bot key to ~/.openpaws/config.yaml
  4. Run: openpaws start
EOF
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case $1 in
            --hostname)
                CAMPFIRE_HOSTNAME="$2"
                shift 2
                ;;
            --disable-tls)
                DISABLE_TLS="true"
                shift
                ;;
            --openpaws-branch)
                OPENPAWS_BRANCH="$2"
                shift 2
                ;;
            --skip-campfire)
                SKIP_CAMPFIRE="true"
                shift
                ;;
            --skip-openpaws)
                SKIP_OPENPAWS="true"
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "Unknown option: $1"
                show_help
                exit 1
                ;;
        esac
    done
}

detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "linux" ;;
        Darwin*) echo "darwin" ;;
        *)
            log_error "Unsupported OS: $(uname -s)"
            exit 1
            ;;
    esac
}

check_docker() {
    log_info "Checking Docker..."
    
    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed."
        if [[ "$(detect_os)" == "darwin" ]]; then
            log_info "Install Docker Desktop from: https://www.docker.com/products/docker-desktop"
        else
            log_info "Install Docker using: curl -fsSL https://get.docker.com | sh"
        fi
        exit 1
    fi
    
    if ! docker info &> /dev/null; then
        if [[ "$(detect_os)" == "darwin" ]]; then
            log_error "Docker Desktop is installed but not running. Please start Docker Desktop."
        else
            log_warn "Docker daemon not accessible. May need sudo or docker group membership."
        fi
        exit 1
    fi
    
    log_success "Docker is available"
}

check_uv() {
    log_info "Checking uv..."
    
    if command -v uv &> /dev/null; then
        log_success "uv is installed: $(uv --version)"
        return 0
    fi
    
    log_info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Source the updated PATH
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    
    if command -v uv &> /dev/null; then
        log_success "uv installed successfully"
    else
        log_error "Failed to install uv. Please install manually: https://docs.astral.sh/uv/"
        exit 1
    fi
}

install_once_cli() {
    log_info "Installing ONCE CLI..."
    
    if command -v once &> /dev/null; then
        log_success "ONCE CLI already installed: $(once version 2>/dev/null || echo 'version unknown')"
        return 0
    fi
    
    # Install ONCE CLI non-interactively
    curl -fsSL https://get.once.com | ONCE_INTERACTIVE=false sh
    
    if command -v once &> /dev/null; then
        log_success "ONCE CLI installed successfully"
    else
        # Check common install locations
        if [[ -x /usr/local/bin/once ]]; then
            export PATH="/usr/local/bin:$PATH"
            log_success "ONCE CLI installed at /usr/local/bin/once"
        else
            log_error "ONCE CLI installation failed"
            exit 1
        fi
    fi
}

deploy_campfire() {
    if [[ "$SKIP_CAMPFIRE" == "true" ]]; then
        log_info "Skipping Campfire installation (--skip-campfire)"
        return 0
    fi
    
    log_info "Deploying Campfire..."
    
    # Check if Campfire is already deployed
    if once list 2>/dev/null | grep -q "campfire"; then
        log_warn "Campfire appears to already be deployed"
        log_info "Use 'once' command to manage existing installation"
        return 0
    fi
    
    # Build deploy command
    local deploy_args=("deploy" "campfire" "--host" "$CAMPFIRE_HOSTNAME")
    
    if [[ "$DISABLE_TLS" == "true" ]]; then
        deploy_args+=("--disable-tls")
    fi
    
    log_info "Running: once ${deploy_args[*]}"
    
    # Deploy Campfire
    # Note: This may require sudo on Linux if user is not in docker group
    if [[ "$(detect_os)" == "linux" ]] && ! docker info &> /dev/null 2>&1; then
        sudo once "${deploy_args[@]}"
    else
        once "${deploy_args[@]}"
    fi
    
    log_success "Campfire deployed at: http${DISABLE_TLS:+s}://${CAMPFIRE_HOSTNAME}"
}

install_openpaws() {
    if [[ "$SKIP_OPENPAWS" == "true" ]]; then
        log_info "Skipping OpenPaws installation (--skip-openpaws)"
        return 0
    fi
    
    log_info "Installing OpenPaws from ${OPENPAWS_REPO}@${OPENPAWS_BRANCH}..."
    
    # Install OpenPaws as a tool using uv
    local git_url="git+${OPENPAWS_REPO}@${OPENPAWS_BRANCH}"
    
    # Check if already installed
    if uv tool list 2>/dev/null | grep -q "openpaws"; then
        log_info "Upgrading existing OpenPaws installation..."
        uv tool upgrade "openpaws" --reinstall || uv tool install "openpaws @ ${git_url}" --force
    else
        uv tool install "openpaws @ ${git_url}"
    fi
    
    # Verify installation
    if command -v openpaws &> /dev/null; then
        log_success "OpenPaws installed successfully"
        openpaws --help | head -5
    else
        # Try adding uv tools to PATH
        export PATH="$HOME/.local/bin:$PATH"
        if command -v openpaws &> /dev/null; then
            log_success "OpenPaws installed (add ~/.local/bin to PATH)"
        else
            log_error "OpenPaws installation verification failed"
            log_info "Try running: uv tool install 'openpaws @ ${git_url}'"
            exit 1
        fi
    fi
}

generate_config() {
    local config_dir="$HOME/.openpaws"
    local config_file="$config_dir/config.yaml"
    
    log_info "Generating OpenPaws configuration..."
    
    mkdir -p "$config_dir"
    
    if [[ -f "$config_file" ]]; then
        log_warn "Config file already exists: $config_file"
        log_info "Backing up to ${config_file}.bak"
        cp "$config_file" "${config_file}.bak"
    fi
    
    # Determine the Campfire URL
    local campfire_url
    if [[ "$DISABLE_TLS" == "true" ]]; then
        campfire_url="http://${CAMPFIRE_HOSTNAME}"
    else
        campfire_url="https://${CAMPFIRE_HOSTNAME}"
    fi
    
    cat > "$config_file" << EOF
# OpenPaws Configuration
# Generated by setup-campfire-openpaw.sh on $(date -Iseconds)
#
# See docs/CAMPFIRE_SETUP.md for complete setup instructions

channels:
  campfire:
    # Your Campfire instance URL
    base_url: ${campfire_url}
    
    # Bot key from Campfire Admin → Bots
    # Format: {id}-{token} (e.g., 123-abc123xyz456)
    bot_key: \${CAMPFIRE_BOT_KEY}
    
    # Webhook settings (OpenPaws receives messages here)
    webhook_port: 8765
    webhook_path: /webhook

groups:
  main:
    channel: campfire
    # Room ID from Campfire URL (e.g., /rooms/1 → chat_id: "1")
    chat_id: "1"
    trigger: "@paw"

tasks:
  # Example scheduled task
  # daily-summary:
  #   schedule: "0 9 * * *"  # 9 AM daily
  #   group: main
  #   prompt: "Summarize yesterday's discussion"

agent:
  # Model to use (requires appropriate API key in environment)
  model: anthropic/claude-sonnet-4-20250514
EOF

    log_success "Configuration generated: $config_file"
}

print_next_steps() {
    local campfire_url
    if [[ "$DISABLE_TLS" == "true" ]]; then
        campfire_url="http://${CAMPFIRE_HOSTNAME}"
    else
        campfire_url="https://${CAMPFIRE_HOSTNAME}"
    fi
    
    echo ""
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}                    Installation Complete!                      ${NC}"
    echo -e "${GREEN}═══════════════════════════════════════════════════════════════${NC}"
    echo ""
    echo -e "${BLUE}Next Steps:${NC}"
    echo ""
    echo -e "1. ${YELLOW}Set up Campfire:${NC}"
    echo "   Open: ${campfire_url}"
    echo "   Complete the initial setup wizard"
    echo ""
    echo -e "2. ${YELLOW}Create a bot in Campfire:${NC}"
    echo "   • Go to Account → Bots"
    echo "   • Click 'New bot'"
    echo "   • Name: OpenPaws (or your preference)"
    echo "   • Webhook URL: http://localhost:8765/webhook"
    echo "   • Copy the bot key (format: 123-abc123xyz)"
    echo ""
    echo -e "3. ${YELLOW}Configure OpenPaws:${NC}"
    echo "   export CAMPFIRE_BOT_KEY='your-bot-key-here'"
    echo "   # Or add to ~/.openpaws/.env"
    echo ""
    echo -e "4. ${YELLOW}Start OpenPaws:${NC}"
    echo "   openpaws start"
    echo ""
    echo -e "5. ${YELLOW}Test the integration:${NC}"
    echo "   In Campfire, @mention your bot: @OpenPaws hello"
    echo ""
    echo -e "${BLUE}Useful Commands:${NC}"
    echo "   openpaws status     # Check daemon status"
    echo "   openpaws logs       # View logs"
    echo "   openpaws stop       # Stop the daemon"
    echo "   once                # Manage Campfire (TUI)"
    echo "   once list           # List deployed apps"
    echo ""
    echo -e "${BLUE}Documentation:${NC}"
    echo "   • Campfire Setup: docs/CAMPFIRE_SETUP.md"
    echo "   • OpenPaws Config: ~/.openpaws/config.yaml"
    echo ""
}

main() {
    echo ""
    echo -e "${BLUE}╔═══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║        Campfire + OpenPaws Setup Script                       ║${NC}"
    echo -e "${BLUE}╚═══════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    
    parse_args "$@"
    
    log_info "Configuration:"
    log_info "  Campfire hostname: $CAMPFIRE_HOSTNAME"
    log_info "  TLS disabled: $DISABLE_TLS"
    log_info "  OpenPaws branch: $OPENPAWS_BRANCH"
    log_info "  Skip Campfire: $SKIP_CAMPFIRE"
    log_info "  Skip OpenPaws: $SKIP_OPENPAWS"
    echo ""
    
    # Prerequisites - only check Docker if installing Campfire
    if [[ "$SKIP_CAMPFIRE" != "true" ]]; then
        check_docker
        install_once_cli
        deploy_campfire
    fi
    
    # OpenPaws installation requires uv
    if [[ "$SKIP_OPENPAWS" != "true" ]]; then
        check_uv
        install_openpaws
    fi
    
    # Generate configuration
    generate_config
    
    # Show next steps
    print_next_steps
}

main "$@"
