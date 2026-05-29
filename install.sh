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

REPO="https://github.com/NikolayGusev-astra/autolycus.git"
INSTALL_DIR="$HOME/autolycus"
DOMAIN=""
VENV_DIR="$INSTALL_DIR/.venv"

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --domain) DOMAIN="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "╔══════════════════════════════════════════╗"
echo "║   Autolycus Agent Installer              ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# Check prerequisites
echo "→ Checking prerequisites..."

# Python 3.8+
if ! command -v python3 >/dev/null 2>&1; then
    echo "⚠ Python 3 not found. Install Python 3.8+ and retry."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo "0.0")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 8 ]); then
    echo "⚠ Python $PYTHON_VERSION detected. Requires Python 3.8+"
    exit 1
fi
echo "✓ Python $PYTHON_VERSION"

# git
if ! command -v git >/dev/null 2>&1; then
    echo "⚠ git not found. Install git and retry."
    exit 1
fi
echo "✓ git"

# curl
if ! command -v curl >/dev/null 2>&1; then
    echo "⚠ curl not found. Install curl and retry."
    exit 1
fi
echo "✓ curl"
echo "✓ User: $USER"
echo "✓ Install dir: $INSTALL_DIR"
echo ""

# Clone or update repository
echo "→ Installing Autolycus Agent..."
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "  Updating existing installation..."
    cd "$INSTALL_DIR"
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
else
    echo "  Cloning repository..."
    git clone "$REPO" "$INSTALL_DIR" 2>&1 || {
        echo "⚠ Failed to clone repository. Check network/proxy settings."
        echo "  Manual: git clone $REPO $INSTALL_DIR"
        exit 1
    }
    REPO_UPDATED=1
fi
echo "✓ Repository ready"

# Create virtual environment
echo ""
echo "→ Setting up virtual environment..."
cd "$INSTALL_DIR"

if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" 2>/dev/null || {
        echo "⚠ python3-venv not available. Trying without..."
        python3 -m pip install --user virtualenv -qq 2>/dev/null
        python3 -m virtualenv "$VENV_DIR" 2>/dev/null || {
            echo "⚠ Cannot create virtual environment."
            echo "  Install python3-venv: sudo apt-get install python3-venv"
            exit 1
        }
    }
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip setuptools wheel -qq 2>&1 | tail -2
echo "✓ Virtual environment ready"

# Install Python dependencies
echo ""
echo "→ Installing Python packages..."
if [ -f requirements.txt ]; then
    pip install -r requirements.txt -qq 2>&1 | tail -3
elif [ -f pyproject.toml ]; then
    pip install -e . -qq 2>&1 | tail -3
else
    echo "  No requirements.txt or pyproject.toml found, skipping"
fi
echo "✓ Python packages installed"

# Configure .env
echo ""
echo "→ Configuration..."

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
    echo "  ⚠ IMPORTANT: Edit .env with your credentials:"
    echo "    nano $INSTALL_DIR/.env"
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

# Create systemd user service
echo ""
echo "→ Creating systemd user service..."

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

systemctl --user daemon-reload 2>/dev/null || true
echo "✓ Systemd service created"

# Restart service if updated, start if fresh
echo ""
echo "→ Starting Autolycus Agent..."

# Check if systemd user session is available (requires lingering or systemd user instance)
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1; then
    systemctl --user enable autolycus-agent 2>/dev/null || true

    if [ -n "$REPO_UPDATED" ] && systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        echo "  Restarting with new version..."
        systemctl --user restart autolycus-agent 2>/dev/null || true
    elif ! systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        systemctl --user start autolycus-agent 2>/dev/null || true
    fi

    sleep 3

    if systemctl --user is-active autolycus-agent >/dev/null 2>&1; then
        echo "✓ Autolycus Agent is running"
    else
        echo "⚠ Service may still be starting. Check with:"
        echo "  systemctl --user status autolycus-agent"
    fi
else
    echo "  systemd user session not available — starting in background..."
    nohup "$VENV_DIR/bin/autolycus-agent" start > /tmp/autolycus.log 2>&1 &
    sleep 3
    if pgrep -f autolycus-agent >/dev/null 2>&1; then
        echo "✓ Autolycus Agent is running (PID: $(pgrep -f autolycus-agent | head -1))"
    else
        echo "⚠ Start may have failed. Check: cat /tmp/autolycus.log"
    fi
fi

echo ""
echo "→ Setting up Autolycus home directory..."

AUTOLYCUS_HOME_DIR="$HOME/.autolycus"
mkdir -p "$AUTOLYCUS_HOME_DIR/profiles/default" 2>/dev/null

# Export now so child processes (autolycus setup) pick it up
export AUTOLYCUS_HOME="$AUTOLYCUS_HOME_DIR"

# Set AUTOLYCUS_HOME in shell rc for future sessions
SHELL_RC="$HOME/.bashrc"
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
fi

if ! grep -q 'AUTOLYCUS_HOME' "$SHELL_RC" 2>/dev/null; then
    {
        echo ""
        echo "# Autolycus Agent"
        echo "export AUTOLYCUS_HOME=\"$AUTOLYCUS_HOME_DIR\""
        echo "export PATH=\\\"$INSTALL_DIR/.venv/bin:\\$PATH\\"""
    } >> "$SHELL_RC"
    echo "✓ AUTOLYCUS_HOME configured in $SHELL_RC"
fi

# Run quick setup with isolated home
echo ""
echo "→ Creating default configuration..."
mkdir -p "$AUTOLYCUS_HOME_DIR" 2>/dev/null

# Create default config.yaml with basic settings
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
    echo "✓ Default config created in $AUTOLYCUS_HOME_DIR"
fi

# Create .env if missing
if [ ! -f "$AUTOLYCUS_HOME_DIR/.env" ]; then
    cat > "$AUTOLYCUS_HOME_DIR/.env" << 'ENVEOF'
OPENROUTER_API_KEY=***
# TELEGRAM_BOT_TOKEN=***
# TELEGRAM_ALLOWED_USERS=
ENVEOF
    echo "✓ .env template created"
    echo ""
    echo "  ⚠ Edit with your keys:"
    echo "    nano $AUTOLYCUS_HOME_DIR/.env"
fi

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Installation complete!                 ║"
echo "╚══════════════════════════════════════════╝"
echo ""
echo "  Run:          autolycus setup"
echo "  Install dir:  $INSTALL_DIR"
echo "  Config:       $INSTALL_DIR/.env"
echo "  Logs:         journalctl --user -u autolycus-agent -f"
echo "  Restart:      systemctl --user restart autolycus-agent"
echo "  Stop:         systemctl --user stop autolycus-agent"
echo ""
echo "  Update:       curl -fsSL https://autolycus-agent.ru/install.sh | bash"

# Create symlink for immediate use (in case .bashrc hasn't been sourced yet)
if [ -x "$VENV_DIR/bin/autolycus" ]; then
    ln -sf "$VENV_DIR/bin/autolycus" /usr/local/bin/autolycus
    echo "  Symlinked:    /usr/local/bin/autolycus"
fi
