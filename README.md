<p align="center">
  <img src="assets/banner.png" alt="Autolycus Agent" width="100%">
</p>

# Autolycus Agent ☤ v0.1.2

<p align="center">
  <a href="https://github.com/NikolayGusev-astra/autolycus"><img src="https://img.shields.io/badge/Repo-autolycus-blue?style=for-the-badge" alt="Repository"></a>
  <a href="https://github.com/NikolayGusev-astra/autolycus/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
</p>

**Enterprise AI Assistant.** Built on [Hermes Agent](https://github.com/NousResearch/hermes-agent) by Nous Research.  
Оптимизирован для бизнес-автоматизации, безопасности и on-premise развёртывания.

---

## Возможности Autolycus vs vanilla Hermes Agent

### 🔒 Безопасность

| Компонент | Описание |
|-----------|----------|
| **SBL (System Boundary Layer)** | Детерминированный FHS-классификатор на уровне tool call dispatch. Перехватывает write_file/patch/terminal, классифицирует пути (SYSTEM/USER/UNKNOWN), снимает снапшот инфраструктуры, проверяет зависимости. Без LLM на critical path. |
| **Ultra Governance** | Policy Engine (4 режима: off/audit/simulate/enforce) + Governance Coordinator — централизованное pre-dispatch управление. Deny-list, allow-list, param rules, max param bytes. |

### 🚀 Производительность

| Компонент | Описание |
|-----------|----------|
| **RTK v2 (Reduced Token Kernel)** | Недеструктивный компрессор tool output. Head/tail truncation, repeat compaction, type-aware per-tool стратегии. Сохранение ~84% токенов. Recovery через rtk_recover. Bounded buffer + circuit breaker + pre-turn integration. |
| **ContextWriter** | Rebuild памяти: все-туры логирование, rg fallback, config.yaml window_size, shutdown hook, findings_to_wiki без LLM. |

### 🏗️ Инфраструктура

| Компонент | Описание |
|-----------|----------|
| **Tacops** | Terraform + Ansible CoPilot. Автоматическое развёртывание тестовых стендов (OpenNebula). Модели: StandSpec, VmSpec, BastionSpec. 8 Ansible фаз. Весь инструментарий в portable/ — без sudo. |
| **Portable Toolchain** | Статические musl бинарники: terraform, fd, rg, gh, jq, yq. Виртуальное окружение: ansible-core, pyone. |

### 🧩 Плагины

- **Doc Session** — создание многостраничных документов через session-based запись по разделам
- **Disk Cleanup** — автоматическая чистка временных файлов по правилам
- **Teams Pipeline** — pipeline обработки Microsoft Teams встреч
- **Kanban** — мультиагентная доска задач
- **Spotify** — управление воспроизведением
- **20+ model providers** (Alibaba, Arcee, Bedrock, Copilot, DeepSeek, Gemini, GMI, HuggingFace, Kilocode, Kimi, MiniMax, Nous, Novita, Nvidia, Ollama Cloud, OpenAI Codex, OpenCode Zen, OpenRouter, Qwen, StepFun, xAI, Xiaomi, ZAI)
- **7 web search providers** (Brave, DuckDuckGo, Exa, Firecrawl, SearXNG, Tavily, Parallel)
- **4+ platform adapters** (Google Chat, IRC, Line, Teams)

### 📚 Кастомные навыки

DevOps: kanban-orchestrator/worker, webhook-subscriptions, local-infra-audit, portable-toolchain, infrastructure-deployment, managed-infra-deploy, logpull, pmi-development, aldpro-automation, sosreport-diagnostics, astrasos-rust-audit, triple-memory  
Telegram: telegram-habr-content  
Productivity: jira-api, lodestone-api

---

## Установка

```bash
# Установка Node.js 22+ (требуется для некоторых функций)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# Клонирование
git clone --recurse-submodules https://github.com/NikolayGusev-astra/autolycus.git
cd autolycus

# Установка Python зависимостей
uv venv venv --python 3.11
source venv/bin/activate
uv pip install -e .

# Установка portable инструментария (опционально)
bash portable/install.sh
```

---

## Changelog v0.1.2 (2026-05-15)

### Новые возможности

**SBL (System Boundary Layer)**
- FHS path classification: SYSTEM/USER/UNKNOWN
- Pre-write dependency snapshot (systemctl, ports, processes)
- Service map with dependency lookup
- Learned deps persistence между сессиями
- Deep audit: FMC (Find, Map, Collect), /proc/comm, port/domain/cert extraction
- Fix shell redirect blocking (/dev/null, &1, &2)
- Многоуровневая архитектура: pre_tool_call + transform_tool_result + on_session_start

**Ultra Governance**
- Policy Engine: 4 режима (off/audit/simulate/enforce)
- Governance Coordinator — единый pre_tool_call хук
- Deny-list, allow-list, param rules, max param bytes
- 18 E2E тестов с реальной политикой + SBL классификацией

**RTK v2**
- Недеструктивный компрессор (сохранение полных данных, recovery через rtk_recover)
- Bounded buffer + circuit breaker
- Pre-turn integration + metadata tracking
- Pattern detection: CONSECUTIVE_ERRORS, TOOL_LOOP, BUDGET_EXCEEDED, NO_PROGRESS
- Signal injection в system prompt
- Двухуровневая конфигурация: YAML overrides + dynamic thresholds
- 56 тестов, 31/31 pass

**Tacops**
- Terraform + Ansible CoPilot: stand.py, terraform.py, ansible.py, orchestrator.py
- OpenNebula клиент (XML-RPC через pyone)
- 8 Ansible фаз: bastion → infrastructure → aldpro → keycloak → external_users → keycloak_apps → smartcards → smartcard_test
- Portable toolchain: terraform, ansible-core, pyone, fd, rg, gh, jq, yq
- Модели: StandSpec, VmSpec, BastionSpec, NetworkSpec, Credentials

**Doc Session**
- Многостраничные документы через разделы (file_doc_create/write/finalize)
- Три уровня защиты write_file для больших документов
- Универсальный 12K порог блокировки

**ContextWriter**
- Rebuild: все-туры логирование, rg fallback, config.yaml window_size
- Shutdown hook для корректного завершения
- findings_to_wiki без LLM вызова
- Интеграция с portable toolchain

### Исправления
- max_tokens:4096 cap removed — позволял output truncation на больших ответах
- SBL shell redirect blocking (/dev/null, &1, &2)
- RTK не-деструктивное сжатие с recovery
- Doc session универсальный порог блокировки
- Upstream URL: NousResearch → autolycus (для корректной работы autolycus update)

### Обновления инфраструктуры
- Все upstream-ссылки заменены на NikolayGusev-astra/autolycus
- Создан docs/rtk-v2.md
- Создан docs/autolycs/sbl-pmi.md
- Создан docs/autolycs/ultra-governance.md
- Обновлён docs/features.md
- Обновлён CONTRIBUTING.md
