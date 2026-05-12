#!/bin/bash
# ============================================================================
# Autolycus VPS Setup — Fresh server in one command
# ============================================================================
# Run as root on a fresh Ubuntu 22.04/24.04 VPS.
#
# Usage:
#   bash <(curl -fsSL https://raw.githubusercontent.com/.../setup-vps.sh)
# ============================================================================

set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║${NC}     ${BOLD}Autolycus VPS Setup${NC}               ${BLUE}║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"

# ── 1. System packages ────────────────────────────────────────────────────
echo -e "${BLUE}[1/6]${NC} System packages..."
apt update -qq && apt upgrade -y -qq
apt install -y -qq \
    curl git docker.io docker-compose-v2 nginx certbot python3-pip \
    ufw htop neofetch

# ── 2. Docker ─────────────────────────────────────────────────────────────
echo -e "${BLUE}[2/6]${NC} Docker..."
systemctl enable --now docker
usermod -aG docker "$SUDO_USER" 2>/dev/null || true

# ── 3. Firewall ───────────────────────────────────────────────────────────
echo -e "${BLUE}[3/6]${NC} Firewall..."
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'
ufw --force enable

# ── 4. Clone Autolycus ────────────────────────────────────────────────────
echo -e "${BLUE}[4/6]${NC} Deploy..."
DEPLOY_DIR="/opt/autolycus"
if [ ! -d "$DEPLOY_DIR" ]; then
    git clone --depth=1 https://github.com/NikolayGusev-astra/autolycus.git "$DEPLOY_DIR"
fi
cd "$DEPLOY_DIR/deploy"

if [ ! -f .env ]; then
    cp .env.example .env
    echo -e "${YELLOW}⚠ Edit ${DEPLOY_DIR}/deploy/.env with your API keys${NC}"
fi

# ── 5. SSL Certificate ────────────────────────────────────────────────────
echo -e "${BLUE}[5/6]${NC} SSL..."
certbot certonly --webroot -w /var/www/certbot \
    -d autolycus-agent.ru -d www.autolycus-agent.ru \
    --agree-tos --email admin@autolycus-agent.ru --non-interactive || {
    echo -e "${YELLOW}⚠ SSL failed — run certbot manually after DNS resolves${NC}"
}

# ── 6. Launch ─────────────────────────────────────────────────────────────
echo -e "${BLUE}[6/6]${NC} Starting services..."
docker compose -f "$DEPLOY_DIR/deploy/docker-compose.yml" up -d

# ── Done ─────────────────────────────────────────────────────────────────
IP=$(curl -s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo -e ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║${NC}  ${BOLD}Autolycus VPS ready!${NC}                 ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                       ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Site:  https://autolycus-agent.ru     ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  IP:    ${IP}                     ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  Deploy: ${DEPLOY_DIR}/deploy        ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}                                       ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  ${YELLOW}Next steps:${NC}                         ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  1. Edit ${DEPLOY_DIR}/deploy/.env   ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  2. Set DNS A record → ${IP}  ${GREEN}║${NC}"
echo -e "${GREEN}║${NC}  3. Run certbot if SSL failed        ${GREEN}║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
