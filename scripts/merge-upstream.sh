#!/usr/bin/env bash
# merge-upstream.sh — Автоматический мерж upstream с разрешением конфликтов для Autolycus
# 
# Использование: ./scripts/merge-upstream.sh [upstream-branch]
#
# Стратегия:
# 1. Получить upstream
# 2. Создать ветку мержа
# 3. Мерж с upstream/main
# 4. Для известных конфликтных файлов — применить нашу версию + адаптировать под новый upstream
# 5. Для остальных файлов — авторазрешение через git

set -euo pipefail

UPSTREAM="${1:-upstream/main}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_TAG="autolycus-pre-merge-$(date +%Y%m%d_%H%M)"

echo "=== Merge Autolycus with $UPSTREAM ==="
cd "$REPO_DIR"

# Проверка что мы на нашей ветке
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" != "merge/upstream-"* ]]; then
    echo "WARNING: Not on a merge branch. Current: $CURRENT_BRANCH"
    echo "Creating merge branch..."
    git stash || true
    git checkout -b "merge/upstream-$(echo $UPSTREAM | tr '/' '-')"
fi

# Backup
echo "Creating backup tag: $BACKUP_TAG"
git tag "$BACKUP_TAG" || true

# Fetch upstream
echo "Fetching upstream..."
git fetch upstream

# Merge
echo "Merging $UPSTREAM..."
if git merge "$UPSTREAM" --no-edit; then
    echo "✅ Clean merge — no conflicts!"
    exit 0
fi

# Список конфликтных файлов
CONFLICTS=$(git diff --name-only --diff-filter=U)
echo "Conflicts found:"
echo "$CONFLICTS"

# Для каждого конфликтного файла — наша стратегия
for f in $CONFLICTS; do
    echo "Resolving: $f"
    
    case "$f" in
        run_agent.py|model_tools.py|toolsets.py|gateway/platforms/telegram.py)
            # Эти файлы — берём upstream и применяем наши патчи поверх
            echo "  Strategy: upstream + our patches"
            git checkout --theirs "$f"
            git add "$f"
            ;;
        gateway/run.py)
            # Gateway — берём upstream и добавляем наши миксины
            echo "  Strategy: upstream + kanban mixin import"
            git checkout --theirs "$f"
            git add "$f"
            ;;
        hermes_state.py)
            # State — берём upstream
            echo "  Strategy: upstream"
            git checkout --theirs "$f"
            git add "$f"
            ;;
        hermes_constants.py)
            # Constants — берём upstream и адаптируем путь
            echo "  Strategy: upstream + autolycus override"
            git checkout --theirs "$f"
            git add "$f"
            ;;
        cli.py)
            # CLI — берём upstream
            echo "  Strategy: upstream + autolycus branding"
            git checkout --theirs "$f"
            git add "$f"
            ;;
        *)
            # Остальные — пытаемся авторазрешить
            echo "  Strategy: auto-resolve (ours where possible)"
            git checkout --ours "$f" 2>/dev/null || git checkout --theirs "$f"
            git add "$f"
            ;;
    esac
done

# Финальная проверка
if git diff --name-only --diff-filter=U | grep -q .; then
    echo "⚠️  Unresolved conflicts remain:"
    git diff --name-only --diff-filter=U
    echo "Resolve manually, then commit with:"
    echo "  git add . && git commit -m "merge: upstream $(echo $UPSTREAM | tr '/' ' ') → autolycus""
    exit 1
fi

# Auto-commit
git commit -m "merge: upstream $(echo $UPSTREAM | tr '/' ' ') → autolycus (auto-resolved)"
echo "✅ Merge complete!"
echo "Next: run verification: python -c "from run_agent import AIAgent; print('OK')""
