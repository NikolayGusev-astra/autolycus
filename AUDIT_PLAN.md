# Autolycus — План доработок

**Репозиторий:** https://github.com/NikolayGusev-astra/autolycus
**Ветка:** `main` (10 кастомных коммитов поверх upstream)
**Отставание от upstream:** 170 коммитов
**Дата:** 2026-05-14

---

## ✅ Уже влито

| Компонент | Статус | Где |
|---|---|---|
| **Merge upstream** (170 коммитов) | ✅ | `88b7200dd` → `dec9487ca` |
| **Entry point `autolycus`** | ✅ | `pyproject.toml` + `install.sh` |
| P0: file_operations stdlib | ✅ | core/file_operations.py |
| P1: Unload after use (JIT import) | ✅ | model_tools.py |
| P5: malloc_trim(0) | ✅ | model_tools.py |
| P6: ContextWriter | ✅ | plugins/memory/context_writer.py |
| ultra-governance plugin | ✅ | plugins/ultra-governance/ |
| SBL (System Boundary Layer) | ✅ | plugins/sbl/ |
| Sanitize pipeline | ✅ | core/sanitize.py |
| Sanitize MCP | ✅ | plugins/sanitize_mcp/ |
| ClickHouse memory provider | ✅ | plugins/memory/clickhouse/ |
| findings_to_wiki provider (+ LLM extraction) | ✅ | plugins/memory/findings_to_wiki/ |
| VPS deploy (Docker, Nginx, landing, Xray) | ✅ | deploy/ |
| Tests (2002 строк) | ✅ | tests/test_autolycus_*.py |
| Merge policy + GitHub Actions sync | ✅ | `MERGE_POLICY.md`, `.github/workflows/autolycus-sync.yml` |

---

## ❌ Не влито — P0

### P0.1 — Entry point `autolycus`
**Проблема:** В `pyproject.toml` нет `autolycus = "hermes_cli.main:main"`. Вместо этого install.sh делает fragile alias:
```bash
alias autolycus='cd ... && source .../venv/bin/activate && python3 -m hermes_cli.main'
```
**Что не работает:** systemd, gateway, cron, вызов из другого каталога, сброс shell.
**Решение:** Добавить `[project.scripts] autolycus = "hermes_cli.main:main"` + `autolycus-acp`, переписать install.sh.

### P0.2 — Merge upstream (170 коммитов)
**Проблема:** Форк отстаёт на 170 коммитов. Пропущены:
- NovitaAI provider
- `/subgoal` команда
- Web plugin migration (firecrawl, tavily, exa, parallel, searxng)
- Docker dashboard side-process
- Security fixes (shell=True reduction, файловые permissions)
- CLI/TUI fixes (skin parsing, scrollback, @-file crash)
- Gateway fixes (QQBot reconnect, Feishu ws, PID detection)
- `fix(tools): wrap bare scalars in single-element list` (merged PR #???)
- 32K строк документации
**Риск:** Конфликты с нашими кастомными коммитами (P0-P6, plugins).

### P0.3 — Rebranding: pyproject.toml
```toml
name = "autolycus-agent"  # было: hermes-agent
version = "0.13.0"
description = "Enterprise AI Assistant for Business"
authors = [{ name = "Autolycus Team" }]
```
Плюс все `[project.optional-dependencies]` со ссылками `hermes-agent[...]` → `autolycus-agent[...]`.

---

## ❌ Не влито — P1

### P1.1 — validate-then-repair-tool-args
**Ветка:** `fork/validate-then-repair-tool-args` — существует
**Коммиты:** 30cf909b (feat: validate-then-repair layer) + 3 предшествующих
**Суть:** 5 классов автопочинки tool-аргументов для open-weight моделей.
**Задача:** merge в main.

### P1.2 — fix/defensive-hardening
**Ветка:** `fork/fix/defensive-hardening` — существует
**Коммит:** 847ee2039 (fix: defensive hardening — logging, dedup, locks, dead code)
**Суть:** Защитное усиление ядра агента.
**Задача:** merge в main.

### P1.3 — fix/pr-22093-draft-v2 (Telegram draft bugfixes)
**Статус:** Ветка отсутствует на fork remote.
**Суть:** 6 багфиксов Telegram draft transport, выявленных через Prism+Premortem.
**Задача:** Найти в истории, если нет — восстановить по памяти.

---

## ❌ Не влито — P2

### P2.1 — Rebranding CLI (критично для white label)
**Что нужно сменить:**
- `cli.py` — banner, docstring ("Hermes Agent CLI")
- `hermes_cli/main.py` — help text, commands description
- `hermes_cli/skin_engine.py` — default skin: agent_name, response_label
- `scripts/install.sh` — заголовок, брендинг
- `README.md` — полная замена
- Все docstrings в корневых модулях
- `hermes_cli/commands.py` — branding в help

### P2.2 — install.sh в корне репозитория
Сейчас install.sh только в `deploy/scripts/`. Нужен `install.sh` в корне для `curl | bash` через GitHub RAW.

---

## ❌ Не влито — P3 (отложено)

- Role models (airules) — отдельный проект, не портируется в autolycus
- Free model degradation fix — пока неактуально

---

## 📊 Сводка по PR от upstream

| Ветка/PR | Статус в upstream | Нужен merge? |
|---|---|---|
| feat/sanitize-pipeline | PR #23193 | ✅ Уже в autolycus |
| feat/sbl-goal | RFC open | ✅ Уже в autolycus |
| feat/clickhouse-memory-provider | Open | ✅ Уже в autolycus |
| feat/findings-to-wiki-provider | Open | ✅ Уже в autolycus |
| validate-then-repair-tool-args | Open | ❌ Нужен merge |
| fix/defensive-hardening | Open | ❌ Нужен merge |
| fix/pr-22093-draft-v2 | Open | ❌ Утерян — восстановить |
| Merged: bare scalars fix | ✅ Merged upstream | Автоматически при merge upstream |

---

## ⚡ Приоритет выполнения

```
P0.1 Entry point      ██████████   ✅
P0.2 Merge upstream   ██████████   ✅
P0.3 Rebranding pkg   ░░░░░░░░░░   ❌
P1.1 validate-repair  ░░░░░░░░░░   ❌
P1.2 defensive-harden ░░░░░░░░░░   ❌
P1.3 draft-v2 restore ░░░░░░░░░░   ❌
P2.1 CLI rebranding   ░░░░░░░░░░   ❌
P2.2 install.sh корень████████░░   ✅
```

---

## 🔗 Ссылки

- Репозиторий: https://github.com/NikolayGusev-astra/autolycus
- Wiki инфраструктура: concepts/autolycus-infrastructure.md
- Wiki деплой: concepts/autolycus-deployment-plan.md
- STATUS.md: `/root/autolycus/STATUS.md`
- Airules: `/root/airules/` (role models)
- Nous upstream: https://github.com/NousResearch/hermes-agent

---

*План создан: 2026-05-14. Отмечай ход работ — меняй `[ ]` на `[x]`.*
