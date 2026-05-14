#!/bin/bash
# Apply all autolycus-specific patches after upstream merge
# Exit 0 = success, Exit 1 = conflict
set -e

PATCH_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PATCH_DIR/.."

echo "=== Applying autolycus patches ==="
echo "Base: $(git rev-parse HEAD)"
echo ""

STAGED_FILES=""

for patch in patches/[0-9]*.patch; do
    [ -f "$patch" ] || continue
    name=$(basename "$patch")
    echo "→ $name..."

    # Extract the files this patch touches (for staging)
    FILES=$(grep '^diff --git' "$patch" | sed 's|diff --git a/||;s| b/.*||' | tr '\n' ' ')

    # Dry-run first
    if ! git apply --check "$patch" 2>/dev/null; then
        echo "  ❌ CONFLICT in $name"
        echo "  Files: $FILES"
        echo ""
        echo "  Manual resolution needed:"
        echo "    1. Apply patch with: git apply --reject patches/$name"
        echo "    2. Resolve .rej files"
        echo "    3. Commit and push"
        echo "  Failed patch: $PWD/$patch"
        exit 1
    fi

    git apply "$patch"
    echo "  ✅ $name applied"
    STAGED_FILES="$STAGED_FILES $FILES"

    # Stage each file
    for f in $FILES; do
        if [ -f "$f" ]; then
            git add "$f" 2>/dev/null || true
        fi
    done
done

# Commit patches as a single commit
if [ -n "$STAGED_FILES" ]; then
    git commit -m "apply: autolycus patches ($(date +%Y-%m-%d))

Applied $(ls patches/[0-9]*.patch 2>/dev/null | wc -l) patches:
$(for p in patches/[0-9]*.patch; do echo "  - $(basename $p .patch)"; done)
" --allow-empty
fi

echo ""
echo "=== All patches applied successfully ==="
echo "HEAD: $(git rev-parse HEAD)"
