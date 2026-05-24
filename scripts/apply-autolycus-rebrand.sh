#!/bin/bash
# apply-autolycus-rebrand.sh — apply Autolycus branding changes after upstream merge
# This replaces the old patch-based approach with direct sed replacements
set -e

cd "$(dirname "$0")"

echo "=== Applying Autolycus rebranding ==="
echo "Base: $(git rev-parse HEAD)"
echo ""

# 002: version bump
echo "→ 002: version patch..."
OLD_VER=$(grep "^__version__" hermes_cli/__init__.py | head -1 | sed 's/.*"\(.*\)".*/\1/')
NEW_VER="${OLD_VER}.1-autolycus"
sed -i "s/^__version__ = \".*\"/__version__ = \"$NEW_VER\"/" hermes_cli/__init__.py
sed -i "s/^__release_date__ = \".*\"/__release_date__ = \"`date +%Y.%m.%d`\"/" hermes_cli/__init__.py
echo "  Version: $OLD_VER → $NEW_VER"

# 003: banner rebranding
echo "→ 003: banner Hermes → Autolycus..."
sed -i 's/Hermes Agent v/VERSION/' hermes_cli/banner.py
sed -i 's/Agent v{VERSION}/Agent v{VERSION}/' hermes_cli/banner.py
# More robust: replace the exact pattern
sed -i 's/"Hermes Agent v{VERSION} ({RELEASE_DATE})"/"Autolycus Agent v{VERSION} ({RELEASE_DATE})"/' hermes_cli/banner.py 2>/dev/null || true
# Fallback: just replace any remaining "Hermes Agent" mentions in banner
sed -i 's/f"Hermes Agent v/f"Autolycus Agent v/' hermes_cli/banner.py
echo "  ✓ Banner updated"

# 004: main.py docstring
echo "→ 004: main.py header..."
sed -i 's/Hermes CLI - Main entry point/Autolycus CLI - Main entry point/' hermes_cli/main.py
echo "  ✓ main.py updated"

# 005: setup.py docstring
echo "→ 005: setup.py header..."
sed -i 's/Interactive setup wizard for Hermes Agent/Interactive setup wizard for Autolycus Agent/' hermes_cli/setup.py
sed -i 's/Hermes specific directory layout/Autolycus specific directory layout/' hermes_cli/setup.py 2>/dev/null || true
echo "  ✓ setup.py updated"

# 006: ACP version string
echo "→ 006: ACP version string..."
sed -i 's/Hermes Agent v{HERMES_VERSION}/Autolycus Agent v{HERMES_VERSION}/' acp_adapter/server.py
echo "  ✓ ACP updated"

# 007: pyproject.toml
echo "→ 007: pyproject.toml..."
sed -i 's/^name = "hermes-agent"/name = "autolycus-agent"/' pyproject.toml
# Also update description if it mentions Hermes
sed -i 's/Hermes AI-powered assistant/Autolycus AI-powered assistant/' pyproject.toml 2>/dev/null || true
echo "  ✓ pyproject.toml updated"

# 008: Cost status bar feature
echo "→ 008: cost status bar..."
# Check if already applied
if grep -q "session_cost" cli.py 2>/dev/null; then
    echo "  ✓ Cost status bar already present"
else
    echo "  ⚠️ Cost status bar needs manual re-implementation (upstream shifted)"
    echo "  Skipping — can be added manually"
fi

# 009: token-log command
echo "→ 009: token-log command..."
if grep -q "_show_token_log\|token-log" cli.py 2>/dev/null; then
    echo "  ✓ Token-log command already present"
else
    echo "  ⚠️ Token-log command needs manual re-implementation (upstream shifted)"
    echo "  Skipping — can be added manually"
fi

# Commit
git add -A
CHANGES=$(git diff --cached --stat)
if [ -n "$CHANGES" ]; then
    git commit -m "apply: autolycus rebranding ($(date +%Y-%m-%d))
    
- Hermes → Autolycus in banner, main, setup, ACP
- Version bumped to ${NEW_VER}
- Package name: autolycus-agent"
    echo ""
    echo "=== Applied and committed ==="
else
    echo ""
    echo "=== No changes (already applied?) ==="
fi

echo "HEAD: $(git rev-parse HEAD)"
