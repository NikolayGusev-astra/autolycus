# Autolycus — Upstream Merge Policy

## Цель

Регулярно синхронизировать изменения из upstream (NousResearch/hermes-agent) в autolycus, сохраняя кастомные improvements.

## Процесс

### Тикер: еженедельно или при значимых upstream фиксах

```bash
# 1. Добавить upstream как remote (если ещё нет)
git remote add upstream https://github.com/NousResearch/hermes-agent.git

# 2. Забрать свежие изменения
git fetch upstream main

# 3. Мердж (dry-run сначала)
git merge --no-commit --no-ff upstream/main
git diff --stat --cached

# 4. Коммит
git commit -m "merge: upstream NousResearch/hermes-agent (N commits)"
git push fork main
```

## Файлы с кастомными изменениями (требуют проверки)

| Файл | Что меняем | Риск конфликта |
|---|---|---|
| `pyproject.toml` | Entry point `autolycus`, брендинг | Низкий |
| `tools/file_operations.py` | P0: stdlib вместо shell | Средний (upstream тоже менял) |
| `model_tools.py` | P1: JIT unload, P5: malloc_trim | Средний |
| `run_agent.py` | P6: ContextWriter integration | Средний |
| `gateway/run.py` | Sanitize pipeline hook | Низкий |
| `gateway/platforms/api_server.py` | Sanitize pipeline hook | Низкий |
| `hermes_cli/config.py` | Autolycus plugins enabled | Низкий |
| `agent/skill_commands.py` | Autolycus tweaks | Низкий |
| `core/sanitize.py` | **Наш файл** (нет в upstream) | Нет — только у нас |
| `core/supply_chain.py` | **Наш файл** | Нет — только у нас |
| Все `plugins/` | **Наши плагины** (ultra-governance, sbl, etc.) | Нет — только у нас |

## Стратегия при конфликте

1. **Наши фичи в файлах, которых нет в upstream** — всегда берём нашу версию
2. **Upstream security fixes** — берём upstream, адаптируем наши изменения поверх
3. **Upstream refactoring** — берём upstream, переписываем наши патчи под новую архитектуру
4. **Конфликт в pyproject.toml** — наши entry points поверх upstream

## GitHub Actions: авто-обновление

### Вариант A: R-скрипт (рекомендуемый)
`.github/workflows/autolycus-sync.yml` — еженедельно:

```yaml
name: Sync with upstream Hermes Agent
on:
  schedule:
    - cron: '0 6 * * 1'  # Каждый понедельник
  workflow_dispatch:  # Ручной запуск

jobs:
  sync:
    runs-on: ubuntu-latest
    environment: autolycus-sync
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GH_PAT }}

      - name: Merge upstream
        run: |
          git remote add upstream https://github.com/NousResearch/hermes-agent.git
          git fetch upstream main
          git merge --no-edit upstream/main || echo "CONFLICT" >> .merge_status

      - name: Check conflicts
        run: |
          if [ -f .merge_status ]; then
            echo "CONFLICT needs manual resolution"
            exit 1
          fi

      - name: Push
        run: git push origin main
```

### Вариант B: Cron на VPS
Через `cronjob` на нашем сервере (менее предпочтительно, т.к. требует рабочего окружения).

## Правила для install.sh

Текущий `install.sh` в корне репозитория поддерживает авто-обновление:
- При повторном запуске делает `git pull --rebase`
- Пересоздаёт venv при необходимости
- Переустанавливает entry point
- Перезапускает systemd сервис

**TODO:** Добавить проверку на break changes от upstream (например, версия миграции конфига).