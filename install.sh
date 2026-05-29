#!/bin/bash
# ============================================================================
# Autolycus Agent Installer
# ============================================================================
# Installation script for Linux servers. No root required.
# Everything installs into user's home directory via venv.
#
# Usage:
#   curl -fsSL https://autolycus-agent.ru/install.sh | bash
#
# Or with options:
#   curl -fsSL ... | bash -s -- --domain your-domain.com
#
# Prerequisites (usually pre-installed):
#   - python3 (3.8+)
#   - git
#   - curl
# ============================================================================

set -e

# Guard against inherited PYTHONPATH/PYTHONHOME
if [ -n "${PYTHONPATH:-}" ]; then
    unset PYTHONPATH
fi
if [ -n "${PYTHONHOME:-}" ]; then
    unset PYTHONHOME
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

# Configuration
REPO="https://github.com/NikolayGusev-astra/autolycus.git"
INSTALL_DIR="$HOME/autolycus"
DOMAIN=""
PYTHON_MIN="3.8"

# Options
SKIP_SETUP=false

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain) DOMAIN="$2"; shift 2 ;;
        --skip-setup) SKIP_SETUP=true; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo ""
echo -e "${CYAN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${CYAN}${BOLD}║   Autolycus Agent Installer              ║${NC}"
echo -e "${CYAN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""

# ============================================================================
# Helpers
# ============================================================================

log_info()  { echo -e "${CYAN}→${NC} $1"; }
log_ok()    { echo -e "${GREEN}✓${NC} $1"; }
log_warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
log_error() { echo -e "${RED}✗${NC} $1"; }

# ============================================================================
# Prerequisites
# ============================================================================

log_info "Checking prerequisites..."

# Python 3.8+
if ! command -v python3 >/dev/null 2>&1; then
    log_error "Python 3 not found. Install Python 3.8+ and retry."
    exit 1
fi

PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 8 ]; }; then
    log_error "Python $PY_VER detected. Requires Python 3.8+"
    exit 1
fi
log_ok "Python $PY_VER"

# git
if ! command -v git >/dev/null 2>&1; then
    log_error "git not found. Install git and retry."
    exit 1
fi
log_ok "git"

# curl
if ! command -v curl >/dev/null 2>&1; then
    log_error "curl not found. Install curl and retry."
    exit 1
fi
log_ok "curl"
log_ok "User: $USER"
echo ""

# ============================================================================
# Clone or update repository
# ============================================================================

log_info "Installing Autolycus Agent..."

if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing installation..."
    cd "$INSTALL_DIR"
    git stash -q 2>/dev/null || true
    git fetch origin 2>&1
    LOCAL=$(git rev-parse HEAD)
    REMOTE=$(git rev-parse origin/main)
    if [ "$LOCAL" = "$REMOTE" ]; then
        echo "  Already up to date (commit ${LOCAL:0:8})"
    else
        echo "  New version available (${LOCAL:0:8} → ${REMOTE:0:8})"
        git pull --rebase 2>&1
        REPO_UPDATED=1
    fi
    git stash pop -q 2>/dev/null || true
else
    echo "  Cloning repository..."
    git clone "$REPO" "$INSTALL_DIR" 2>&1 || {
        log_error "Failed to clone repository."
        echo "  Manual: git clone $REPO $INSTALL_DIR"
        exit 1
    }
    REPO_UPDATED=1
fi
log_ok "Repository ready"

# ============================================================================
# Virtual environment
# ============================================================================

echo ""
log_info "Setting up virtual environment..."
cd "$INSTALL_DIR"

# Resolve venv path: prefer .venv, fall back to legacy venv
if [ -d "$INSTALL_DIR/.venv" ]; then
    VENV_DIR="$INSTALL_DIR/.venv"
    echo "  Using existing .venv"
elif [ -d "$INSTALL_DIR/venv" ]; then
    VENV_DIR="$INSTALL_DIR/venv"
    echo "  Using existing venv (legacy path)"
else
    VENV_DIR="$INSTALL_DIR/.venv"
    python3 -m venv "$VENV_DIR" 2>/dev/null || {
        log_warn "python3-venv not available. Trying virtualenv..."
        python3 -m pip install --user virtualenv -qq 2>/dev/null
        python3 -m virtualenv "$VENV_DIR" 2>/dev/null || {
            log_error "Cannot create virtual environment."
            echo "  Install python3-venv: sudo apt-get install python3-venv"
            exit 1
        }
    }
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel -qq 2>&1 | tail -1
log_ok "Virtual environment ready ($VENV_DIR)"

# ============================================================================
# Install Python packages
# ============================================================================

echo ""
log_info "Installing Python packages..."

if [ -f requirements.txt ]; then
    pip install -r requirements.txt -qq 2>&1 | tail -3
elif [ -f pyproject.toml ]; then
    pip install -e . -qq 2>&1 | tail -3
else
    log_warn "No requirements.txt or pyproject.toml found"
fi
log_ok "Python packages installed"

# ============================================================================
# Create symlink
# ============================================================================

if [ -x "$VENV_DIR/bin/autolycus" ]; then
    ln -sf "$VENV_DIR/bin/autolycus" /usr/local/bin/autolycus 2>/dev/null || true
fi

# ============================================================================
# Configure .env
# ============================================================================

echo ""
log_info "Configuration..."

if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        cp .env.example .env
        echo "  Created .env from template"
    else
        cat > .env << ENVEOF
# Autolycus Agent Configuration
OPENROUTER_API_KEY=***
# TELEGRAM_BOT_TOKEN=***
# TELEGRAM_ALLOWED_USERS=your-user-id
DOMAIN=${DOMAIN:-localhost}
ENVEOF
        echo "  Created .env template"
    fi
    echo ""
    log_warn "IMPORTANT: Edit .env with your credentials:"
    echo "  nano $INSTALL_DIR/.env"
    echo ""
    echo "  Required:"
    echo "    OPENROUTER_API_KEY — from https://openrouter.ai/keys"
    echo "    TELEGRAM_BOT_TOKEN — from @BotFather"
    echo "    TELEGRAM_ALLOWED_USERS — your Telegram user ID"
    if [ -n "$DOMAIN" ]; then
        sed -i "s/DOMAIN=.*/DOMAIN=$DOMAIN/" .env
        echo "    DOMAIN set to: $DOMAIN"
    fi
else
    echo "  .env already exists, skipping"
fi

# ============================================================================
# Systemd service (optional)
# ============================================================================

echo ""
log_info "Creating systemd user service..."

SERVICE_FILE="$HOME/.config/systemd/user/autolycus-agent.service"
mkdir -p "$(dirname "$SERVICE_FILE")"

cat > "$SERVICE_FILE" << SERVICEEOF
[Unit]
Description=Autolycus AI Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$VENV_DIR/bin/autolycus gateway run --replace
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SERVICEEOF

# Start service — systemd optional, fallback to nohup
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    systemctl --user daemon-reload 2>/dev/null || true
    log_ok "Systemd service created"

    echo ""
    log_info "Starting Autolycus Agent..."
    systemctl --user enable autolycus-agent 2>/dev/null || true

    if [ -n "${REPO_UPDATED:-}" ] && systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        echo "  Restarting with new version..."
        systemctl --user restart autolycus-agent 2>/dev/null || true
    elif ! systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        systemctl --user start autolycus-agent 2>/dev/null || true
    fi

    sleep 3

    if systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        log_ok "Autolycus Agent is running"
    else
        log_warn "Service may still be starting. Check:"
        echo "  systemctl --user status autolycus-agent"
    fi
else
    log_ok "Service file created (systemd user not available)"

    echo ""
    log_info "Starting Autolycus Agent in background..."
    nohup "$VENV_DIR/bin/autolycus" gateway run --replace > /tmp/autolycus.log 2>&1 &
    sleep 3
    if pgrep -f "autolycus" >/dev/null 2>&1; then
        log_ok "Autolycus Agent is running (PID: $(pgrep -f 'autolycus' | head -1))"
    else
        log_warn "Start may have failed. Check: cat /tmp/autolycus.log"
    fi
fi

# ============================================================================
# Autolycus home directory
# ============================================================================

echo ""
log_info "Setting up Autolycus home directory..."

AUTOLYCUS_HOME_DIR="$HOME/.autolycus"
mkdir -p "$AUTOLYCUS_HOME_DIR/profiles/default" 2>/dev/null
export AUTOLYCUS_HOME="$AUTOLYCUS_HOME_DIR"

# Set AUTOLYCUS_HOME in shell rc
SHELL_RC="$HOME/.bashrc"
[ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"

if ! grep -q 'AUTOLYCUS_HOME' "$SHELL_RC" 2>/dev/null; then
    cat >> "$SHELL_RC" << RCEOF

# Autolycus Agent
export AUTOLYCUS_HOME="$AUTOLYCUS_HOME_DIR"
export PATH="$VENV_DIR/bin:\$PATH"
RCEOF
    log_ok "AUTOLYCUS_HOME configured in $SHELL_RC"
fi

# Create default config.yaml
echo ""
log_info "Creating default configuration..."

if [ ! -f "$AUTOLYCUS_HOME_DIR/config.yaml" ]; then
    cat > "$AUTOLYCUS_HOME_DIR/config.yaml" << 'CFGEOF'
model:
  provider: openrouter
  base_url: https://openrouter.ai/api/v1
  default: deepseek/deepseek-v4-flash
  context_length: 1048576
  api_mode: chat_completions
  usage_tier: free
agent:
  max_turns: 60
  gateway_timeout: 1800
  yolo_mode: true
toolsets:
  - all
display:
  show_reasoning: true
  streaming: true
  skin: wizard
memory:
  provider: findings_to_wiki
  memory_enabled: true
  memory_char_limit: 2200
  user_profile_enabled: true
compression:
  enabled: true
  target_ratio: 0.2
  threshold: 0.5
plugins:
  enabled:
    - ultra-governance
    - sbl
    - findings-to-wiki
    - rtk
  ultra_governance:
    rtk:
      enabled: false
CFGEOF
    log_ok "Default config created in $AUTOLYCUS_HOME_DIR"
fi

# Create .env template in home dir
if [ ! -f "$AUTOLYCUS_HOME_DIR/.env" ]; then
    cat > "$AUTOLYCUS_HOME_DIR/.env" << 'ENVEOF'
OPENROUTER_API_KEY=***
# TELEGRAM_BOT_TOKEN=***
# TELEGRAM_ALLOWED_USERS=
ENVEOF
    log_ok ".env template created"
    echo ""
    log_warn "Edit with your keys:"
    echo "  nano $AUTOLYCUS_HOME_DIR/.env"
fi

# ============================================================================
# Done
# ============================================================================

echo ""
echo -e "${GREEN}${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}║   Installation complete!                 ║${NC}"
echo -e "${GREEN}${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${CYAN}Run:${NC}          autolycus setup"
echo -e "  ${CYAN}Install dir:${NC}  $INSTALL_DIR"
echo -e "  ${CYAN}Config:${NC}       $INSTALL_DIR/.env"
echo -e "  ${CYAN}Logs:${NC}         journalctl --user -u autolycus-agent -f"
echo -e "  ${CYAN}Restart:${NC}      systemctl --user restart autolycus-agent"
echo -e "  ${CYAN}Stop:${NC}         systemctl --user stop autolycus-agent"
echo ""
echo "  Update:       curl -fsSL https://autolycus-agent.ru/install.sh | bash"
