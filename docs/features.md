# Возможности Autolycus Agent

> Enterprise AI-ассистент для бизнеса. Оптимизирован для безопасности,
> производительности и on-premise развёртывания.

---

## 1. Безопасность (Security)

### System Boundary Layer (SBL)

Детерминированный FHS-классификатор на уровне tool call dispatch.
Перехватывает **все** write-инструменты (`write_file`, `patch`, `terminal`,
`execute_code`) и классифицирует целевые пути:

| Класс | Примеры | Действие |
|-------|---------|----------|
| **USER** | `/home/`, `/tmp/`, `/root/` | Пропуск (безопасно) |
| **SYSTEM** | `/etc/`, `/opt/`, `/usr/`, `/var/lib/` | Снапшот + проверка зависимостей |
| **UNKNOWN** | Всё остальное | Блокировка по умолчанию |

**Перед записью в SYSTEM-путь** SBL:

1. Снимает **снапшот** инфраструктуры: `systemctl list-units`,
   `ss -tlnp`, /proc/fd — определяет какие сервисы работают, какие
   порты слушают, какие конфиги открыты
2. **Сопоставляет** целевой файл с владельцем в service map
3. Показывает предупреждение: *«Запись в /etc/nginx/nginx.conf
   затронет: nginx [systemd] — порт 443»*
4. **Блокирует** запись до подтверждения пользователя

**После записи** SBL учится: новые пути добавляются в `learned_deps.json`
и учитываются при следующих проверках. Знания сохраняются между
сессиями (на диске, не в LLM-контексте).

**Конфигурация:**

```yaml
plugins:
  sbl:
    enabled: true
```

**Ключевое свойство:** Ни один LLM-вызов не участвует в pre-write
critical path — SBL чисто детерминированный, regex-based.

**Файлы:**
- `plugins/sbl/__init__.py` — классификатор, снапшот, хуки
- `plugins/sbl/deep_audit.py` — полный аудит (fd + rg + сертификаты)
- `docs/rfcs/system-boundary-layer.md` — RFC проекта

---

### Ultra Governance Plugin

Пред- и пост-обработка всех tool calls: политики безопасности +
RTK-фильтрация вывода.

**Режимы политик (4 режима):**

| Режим | Поведение | Применение |
|-------|-----------|------------|
| `off` | Полный пропуск | Локальная разработка |
| `audit` | Логирует нарушения, не блокирует | **Умолчание** — мониторинг |
| `simulate` | Блокирует + имитация («было бы заблокировано») | Тестирование политик |
| `enforce` | Блокирует с сообщением об ошибке | Production / сервер |

**Правила проверки (в порядке приоритета):**

1. **Allow-list** — если инструмент в allow, он всегда разрешён
2. **Deny-list** — инструмент из deny-list блокируется
3. **Param rules** — regex-паттерны в аргументах (например, `rm -rf /`,
   fork-бомбы, `dd if=`, `mkfs`, pipe-to-shell)
4. **Max param bytes** — лимит суммарного размера строковых аргументов

**Пресеты:**

| Пресет | Режим | Deny | Max bytes |
|--------|-------|------|-----------|
| `dev` | off | — | 8192 |
| `balanced` | audit | dangerous_shell, wipe_disk | 4096 |
| `strict` | enforce | dangerous_shell, wipe_disk | 2048 |

**Конфигурация:**

```yaml
plugins:
  ultra_governance:
    policy:
      preset: strict        # strict | balanced | dev
      mode: enforce         # off | audit | simulate | enforce
      deny_tools:
        - "dangerous_shell"
        - "wipe_disk"
      max_param_bytes: 2048
      param_blocklist:
        - pattern: "rm -rf /"
          tool: terminal
          reason: Destructive recursive delete
```

**Аудит-лог:** Все решения записываются в `~/.autolycus/ultra-governance/audit.log`
с таймстемпом, tool name, аргументами и решением.

**Файлы:**
- `plugins/ultra-governance/__init__.py` — хуки пре-пост-трансформ
- `plugins/ultra-governance/policy.py` — Policy Engine (363 строки)
- `plugins/ultra-governance/rtk.py` — RTK-фильтр

---

## 2. Производительность (Performance)

### Sanitize MCP Pipeline

Плагин предварительной обработки MCP-инструментов. Фильтрует
опасные/нежелательные инструменты до того как они попадут к агенту.

- Отключает инструменты, не нужные в enterprise-среде
- Предотвращает случайные вызовы опасных MCP-команд
- Работает на уровне регистрации MCP-серверов

**Файл:** `plugins/sanitize_mcp/__init__.py`

### RTK-фильтр (Reduced Token Kernel)

Пост-процессинг tool-результатов, сокращающий токены в контексте агента.
Три стратегии:

1. **Head/Tail truncation** — сохраняет первые N (2000) и последние M (1000)
   символов длинных выводов, середину заменяет заметкой о сокращении
2. **Repeat compaction** — обнаруживает повторяющиеся строки (5+) и
   сворачивает в `⏱ (repeated N times)`
3. **Max output cap** — жёсткий лимит 10,000 символов на результат

По замеренным данным: **средняя экономия ~84% токенов** на tool-выводах.
Все данные сохраняются на диск и восстанавливаются через `rtk_recover`.

**Ограничения:**
- Не трогает результаты <500 символов (оверхед не окупается)
- Per-call bypass: аргумент `rtk_raw=True` для полного вывода
- Только string-результаты (не JSON, не бинарные)

**Конфигурация:**

```yaml
plugins:
  ultra_governance:
    rtk:
      enabled: true
      head_chars: 2000
      tail_chars: 1000
      min_repeat_lines: 5
      max_output_chars: 10000
```

**Файл:** `plugins/ultra-governance/rtk.py` (211 строк)

---

## 3. Enterprise-ready

### On-premise развёртывание

Полная поддержка on-premise: без внешних зависимостей кроме
OpenRouter (или собственного LLM-endpoint). Развёртывается:

- **curl | bash** — однострочник за 30 секунд
- **Docker Compose** — с nginx, SSL, gateway
- **Systemd user service** — без root, портабельно

Ключевые скрипты:
- `install.sh` — автономная установка в `~/.autolycus/`
- `deploy/` — Docker Compose, nginx, systemd unit

### White Label

Autolycus — самостоятельный продукт (не «форк Hermes Agent»). Поддерживает:

- **Полное переименование:** entry points, banner, version string,
  ACP adapter, setup wizard — всё через `patches/` (`001`-`009`)
- **Изолированный home:** `AUTOLYCUS_HOME=~/.autolycus` не пересекается
  с `~/.hermes`
- **Автономный entry point:** `autolycus_entry.py` сам ставит
  `AUTOLYCUS_HOME` и `HERMES_HOME`
- **Брендирование:** собственный `.env.example`, banner с «☤ Autolycus»,
  logo, дизайн-система (золотой #d4a843, тёмная тема)

### ClickHouse Memory Provider (опционально)

Enterprise-уровень постоянной памяти. Вместо filesystem-based памяти
использует ClickHouse для:
- Масштабирования на тысячи сессий
- Полного аудита обращений к памяти
- SQL-запросов к истории взаимодействий

Включён в репозиторий как опциональный memory provider:
`plugins/memory/clickhouse/`

### Полный аудит

Все инструменты безопасности (SBL, Ultra Governance) пишут
структурированные JSON-логи с таймстемпами. Аудит-лог хранится
в `~/.autolycus/ultra-governance/audit.log` и включает:

- Каждый tool call с аргументами
- Решение политики (allowed/blocked/simulated)
- Режим, действовавший в момент проверки
- ID сессии и задачи

---

## 4. Постоянная память (Persistent Memory)

### findings_to_wiki + ContextWriter

Двухуровневая система памяти, устойчивая к компрессии контекста:

1. **findings_to_wiki** — авто-наполнение wiki-базы знаний после каждого
   turn. Ключевые факты, структурированные результаты (Prism, ADR,
   analysis) сохраняются в `~/wiki/raw/auto-findings/`.

2. **ContextWriter / memory()** — быстрые факты, инжектируемые в каждый turn.
   Автоматически наполняется — пользователь не вызывает вручную.
   Хранится в `~/.autolycus/memories/MEMORY.md` и `USER.md`.

3. **LLM Wiki (Karpathy-style)** — полноценная markdown-база знаний:
   - `~/wiki/index.md` — оглавление
   - `~/wiki/concepts/` — концепты и понятия
   - `~/wiki/entities/` — сущности (серверы, проекты, сервисы)
   - `~/wiki/raw/auto-findings/` — сырые находки
   - `~/wiki/log.md` — хронология изменений

4. **HippoRAG v2** — co-occurrence граф знаний (без LLM/API).
   Поиск: `hipporag-lite.py search <query>`.
   Работает на numpy + scipy + networkx + nltk.
   Индекс строится автоматически по крону.

**Ключевое свойство:** агент помнит архитектуру и контекст ПОСЛЕ
перезапуска. Компрессия контекста не сбрасывает знания — они на
диске в HIPPO-графе и wiki.

### Механизмы

| Компонент | Что хранит | Как читается | Время жизни |
|-----------|-----------|--------------|-------------|
| `memory()` | USER.md, MEMORY.md | Инжект в каждый turn | Навсегда |
| `session_search()` | FTS5 по state.db | Поиск по истории | С момента появления |
| `assoc_search()` | PPR граф co-occurrence | Ассоциативный поиск | Все сессии |
| LLM Wiki | Markdown страницы | `~/wiki/` | Ручное обновление |
| HippoRAG | Co-occurrence граф | `hipporag-lite.py search` | Каждые :30 |

---

## 5. Интеграции (All Integrations)

Autolycus поддерживает подключение через плагины к любым сервисам.

### Messaging Gateway

Один gateway-процесс на все платформы:

| Платформа | Возможности |
|-----------|-------------|
| **Telegram** | Бот, группы, голосовые сообщения, inline-кнопки |
| **Discord** | Каналы, треды, DM |
| **Slack** | Workspace-интеграция |
| **WhatsApp** | Business API |
| **Signal** | E2E-зашифрованные сообщения |
| **Matrix** | Децентрализованный чат |
| **Email** | IMAP/SMTP — читает и отправляет |
| **Mattermost** | Enterprise-мессенджер |
| **API (REST)** | HTTP-эндпоинт для внешних вызовов |

### DevOps & Инфраструктура

- **Docker** — управление контейнерами
- **Nginx** — конфигурация, тестирование, reload
- **Xray (VLESS REALITY)** — VPN-нода
- **SSH** — управление удалёнными серверами
- **DuckDB / SQLite** — аналитика данных
- **Webhook Subscriptions** — event-driven запуск

### Корпоративные системы

- **Jira** — создание задач, мониторинг
- **Confluence** — чтение/поиск страниц
- **Kanban** — мульти-агентный board dispatcher
- **Smart Home (Tuya, Philips Hue)** — управление устройствами

### LLM Провайдеры

| Провайдер | Модели | Особенность |
|-----------|--------|-------------|
| **OpenRouter** | 200+ моделей | Основной провайдер |
| **Nous Portal** | 300+ моделей | $20/мес flat |
| **Anthropic** | Claude | Enterprise |
| **OpenAI** | GPT-4o, o3 | Fallback |
| **Ollama** | Локальные модели | Offline / on-premise |
| **Custom** | Любой endpoint | BYO LLM |

### Плагины

Autolycus использует модульную систему плагинов:

```
plugins/
├── memory/              — Memory providers (filesystem, clickhouse, honcho, ...)
├── model-providers/     — LLM backend провайдеры
├── kanban/              — Multi-agent board dispatcher
├── sbl/                 — System Boundary Layer
├── ultra-governance/    — Policy Engine + RTK
├── sanitize_mcp/        — MCP tool sanitizer
├── hermes-achievements/ — Gamification
├── observability/       — Metrics / traces / logs
└── ... (20+ плагинов)
```

---

## 6. Скорость (Speed)

### Оптимизация под low-resource

Autolycus спроектирован для работы на минимальных VPS:

| Ресурс | Минимум | Рекомендуется |
|--------|---------|---------------|
| CPU | 1 ядро | 2 ядра |
| RAM | 512 MB | 1 GB |
| Диск | 512 MB | 5 GB |

### unload after use

После каждого вызова LLM или выполнения команды агент освобождает
ресурсы. Модули, библиотеки и подключения выгружаются из памяти,
когда не используются.

### malloc_trim(0) после каждого вызова

После завершения каждого tool call и LLM-запроса вызывается
`malloc_trim(0)` — системный вызов glibc, который возвращает
неиспользуемую память ОС. Предотвращает фрагментацию кучи и
«распухание» RSS-памяти агента со временем.

**Результаты:**
- Файловые операции до **33× быстрее** (shell → stdlib замена,
  FMC — File Metadata Correlation methodology)
- Потребление памяти стабильно, не растёт в течение сессии
- Не требует swap на 1GB VPS
- RTK-фильтр экономит ~**58% токенов** на tool выводах

### Производительность файловых операций

Через методологию **FMC (File Metadata Correlation)**:
- Замена shell-подпроцессов на stdlib (pathlib, os, shutil)
- Пакетная обработка через DuckDB вместо shell pipe
- `search_files(target='files')` — ripgrep-бэкенд (быстрее find/ls)
- Результат: до 33-кратного ускорения типовых операций

---

## Архитектура плагинов

Все фичи безопасности и производительности реализованы как
**плагины к Hermes Core**:

```
Hermes Agent Core
    ├── plugins/sbl/              — System Boundary Layer
    ├── plugins/ultra-governance/  — Policy Engine + RTK Filter
    ├── plugins/sanitize_mcp/      — MCP sanitizer
    └── plugins/memory/            — Memory providers
```

Каждый плагин регистрирует хуки в жизненном цикле агента:

| Хук | Триггер | Плагины |
|-----|---------|---------|
| `pre_tool_call` | Перед вызовом инструмента | SBL, Ultra Governance |
| `post_tool_call` | После вызова инструмента | Ultra Governance (аудит) |
| `transform_tool_result` | Перед возвратом результата | SBL (обучение), RTK (фильтр) |
| `on_session_start` | Старт новой сессии | SBL (снапшот) |

Плагины активируются через `config.yaml` → `plugins.<имя>.enabled`.
Можно комбинировать: SBL + Ultra Governance + RTK работают вместе.

---

## Применение: карта фичи → компонент

| Фича на сайте | Компонент | Где в коде |
|---------------|-----------|------------|
| Безопасность | SBL (System Boundary Layer) | `plugins/sbl/` |
| Безопасность | Ultra Governance Policy Engine | `plugins/ultra-governance/policy.py` |
| Производительность | RTK Filter | `plugins/ultra-governance/rtk.py` |
| Производительность | Sanitize MCP | `plugins/sanitize_mcp/` |
| Enterprise | White Label патчи | `patches/001`-`009` |
| Enterprise | On-premise install | `install.sh`, `deploy/` |
| Enterprise | ClickHouse memory | `plugins/memory/clickhouse/` |
| Память | findings_to_wiki + ContextWriter | Система памяти Hermes Core |
| Интеграции | Gateway плагины | `plugins/platforms/` |
| Скорость | unload after use + malloc_trim | Система управления памятью |
| Скорость | FMC shell→stdlib оптимизация | Методология, плагины |

---

## Установка

```bash
curl -fsSL https://autolycus-agent.ru/install.sh | bash
```

После установки — сразу готов к работе. Никаких ручных шагов.

**Важно:** начиная с v0.1.1, все плагины (SBL, Ultra Governance, findings_to_wiki)
включены по умолчанию в конфиге. При обновлении с более старой версии —
добавь в `~/.autolycus/config.yaml` секцию вручную:

```yaml
plugins:
  enabled:
    - ultra-governance
    - sbl
    - findings-to-wiki
```
