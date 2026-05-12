#!/bin/bash
# ============================================================================
# Autolycus Agent Installer
# ============================================================================
# One-command installation for Linux and macOS.
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/NikolayGusev-astra/autolycus/main/deploy/scripts/install.sh)
#
# Or with options:
#   bash <(curl -fsSL ...) -s -- --no-venv --skip-setup
#
# ============================================================================

set -e

# ── Colors ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

# ── Banner ────────────────────────────────────────────────────────────────
echo -e "${BLUE}"
echo '    _         _          _                    '
echo '   / \  _   _| |_ _   _ | | ___   ___  _   _ '
echo '  / _ \| | | | __| | | || |/ _ \ / __|| | | |'
echo ' / ___ \ |_| | |_| |_| || | (_) | (__ | |_| |'
echo '/_/   \_\__,_|\__|\__, |_|\___/ \___| \__, |'
echo '                  |___/               |___/ '
echo -e "${NC}"
echo -e "${BOLD}Autolycus Agent${NC} — Enterprise AI Assistant"
echo -e "Fork of Hermes Agent — ${BOLD}autolycus-agent.ru${NC}"
echo ""

# ── Detect OS ─────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
echo -e "${BLUE}├─${NC} OS: ${OS} ${ARCH}"

# ── Prerequisites ─────────────────────────────────────────────────────────
REQUIRED_CMDS="curl git python3"
MISSING=""
for cmd in $REQUIRED_CMDS; do
    if ! command -v "$cmd" &>/dev/null; then
        MISSING="$MISSING $cmd"
    fi
done

if [ -n "$MISSING" ]; then
    echo -e "${YELLOW}⚠ Missing:${MISSING}${NC}"
    echo "Installing dependencies..."
    if [ "$OS" = "Linux" ]; then
        if command -v apt &>/dev/null; then
            sudo apt update -qq && sudo apt install -y -qq curl git python3 python3-venv
        elif command -v yum &>/dev/null; then
            sudo yum install -y curl git python3 python3-venv
        elif command -v apk &>/dev/null; then
            sudo apk add curl git python3 py3-pip
        else
            echo -e "${RED}✗ Unsupported package manager. Install manually: curl, git, python3${NC}"
            exit 1
        fi
    elif [ "$OS" = "Darwin" ]; then
        if command -v brew &>/dev/null; then
            brew install python3 git curl
        else
            echo -e "${RED}✗ Install Homebrew first: https://brew.sh${NC}"
            exit 1
        fi
    fi
fi

echo -e "${GREEN}├─✓${NC} All prerequisites satisfied"

# ── Install Directory ─────────────────────────────────────────────────────
INSTALL_DIR="${AUTOLYCUS_HOME:-$HOME/autolycus}"

if [ -d "$INSTALL_DIR" ]; then
    echo -e "${YELLOW}├─⚠ Autolycus already installed at ${INSTALL_DIR}${NC}"
    echo -e "${YELLOW}├─ Updating...${NC}"
    cd "$INSTALL_DIR"
    git pull origin main
else
    echo -e "${BLUE}├─ Cloning Autolycus...${NC}"
    git clone --depth=1 https://github.com/NikolayGusev-astra/autolycus.git "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# ── Venv ──────────────────────────────────────────────────────────────────
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo -e "${BLUE}├─ Creating virtual environment...${NC}"
    python3 -m venv "$INSTALL_DIR/venv"
fi

echo -e "${BLUE}├─ Installing dependencies...${NC}"
source "$INSTALL_DIR/venv/bin/activate"
cd "$INSTALL_DIR/repo"
pip install -q -e . 2>/dev/null || pip install -q -e ".[all]" 2>/dev/null || {
    echo -e "${YELLOW}├─ Main install deferred (Hermes deps), installing minimal...${NC}"
    pip install -q -e ".[cli]"
}

# ── Aliases ───────────────────────────────────────────────────────────────
SHELL_RC="$HOME/.bashrc"
[ -f "$HOME/.zshrc" ] && SHELL_RC="$HOME/.zshrc"

if ! grep -q 'alias autolycus' "$SHELL_RC" 2>/dev/null; then
    echo -e "\n# Autolycus" >> "$SHELL_RC"
    echo "alias autolycus='cd $INSTALL_DIR/repo && source $INSTALL_DIR/venv/bin/activate && python3 -m hermes_cli.main'" >> "$SHELL_RC"
    echo "alias ac='autolycus'" >> "$SHELL_RC"
    echo -e "${BLUE}├─ Aliases added to ${SHELL_RC}${NC}"
fi

# ── Config ────────────────────────────────────────────────────────────────
CONFIG_DIR="$HOME/.autolycus"
mkdir -p "$CONFIG_DIR"

if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cat > "$CONFIG_DIR/config.yaml" << 'CFGEOF'
# Autolycus Agent — config
plugins:
  enabled:
    - ultra-governance
    - sbl
    - findings-to-wiki
CFGEOF
    echo -e "${BLUE}├─ Config created at ${CONFIG_DIR}/config.yaml${NC}"
fi

# ── Done ──────────────────────────────────────────────────────────────────
echo -e ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}  ${BOLD}Autolycus Agent installed!${NC}                    ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                                  ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Run:  ${BOLD}ac${NC} or ${BOLD}autolycus${NC}                          ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Config: ${CONFIG_DIR}/config.yaml                ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Repo:  ${INSTALL_DIR}                       ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                                  ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  ${YELLOW}📖 autolycus-agent.ru${NC}                          ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}ℹ  Restart your shell or run: source ${SHELL_RC}${NC}"
