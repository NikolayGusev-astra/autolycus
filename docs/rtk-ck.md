# RTK-CK — Conversation Kernel (RTK v3)

**Статус:** verified — Phase 1 реализован, 213 тестов

## Что это

RTK-CK — единый Conversation Kernel для AI-агента. Смотрит на **последовательность** сообщений, а не на каждое по отдельности. Type-aware compression + 6 детекторов аномалий + circuit breaker.

**Проблема:** Conversation history растёт на 200M+ токенов за сессию. RTK v2 сжимает каждый tool result отдельно (84%), но sequence сообщений целиком не сжимается. Prefetch дублирует volatile tier.

**Решение:** RTK-CK применяет compression, pattern detection, budget monitoring ко всему messages[] и system prompt volatile tier.

---

## Архитектура

```
plugins/rtk_ck/
├── __init__.py          # register(): pre_llm_call, pre_tool_call, post_tool_call + rtk_ck_stat tool
├── budget.py            # BudgetScanner: 80%/95%/100%
├── growth.py            # GrowthDetector: 25%/50% auto-scale
├── patterns.py          # PatternDetector: REDUNDANT_READS, STALLED_SESSION
├── dedup.py             # Deduplicator: volatile vs prefetch
├── result_cache.py      # ResultCache: pre_tool_call blocking
├── prefetch_cache.py    # PrefetchCache: stale detection
├── compress.py          # Compressor: type-aware compression
├── context_engine.py    # RTCKContextEngine: compress без LLM
├── verifier.py          # Post-compression verify
├── metrics.py           # MetricsCollector: rtk_ck_stat tool
└── plugin.yaml          # metadata
```

### Hook Chain

| Hook | Модуль | Что делает |
|------|--------|-----------|
| `pre_llm_call` | `__init__.py` | budget → growth → patterns → compress stats → prefetch → dedup |
| `pre_tool_call` | `__init__.py` | ResultCache.check() — блокирует повторные read_file/search_files |
| `post_tool_call` | `__init__.py` | ResultCache.store() — кэширует результат |
| tool `rtk_ck_stat` | `__init__.py` | Экспорт метрик (text/JSON) |

---

## Детекторы аномалий

### BudgetScanner (budget.py)
- `BUDGET_WARN` — >80% context → inject в user message
- `BUDGET_CRITICAL` — >95% context → inject + RBE
- `BUDGET_HALT` — ≥100% → circuit breaker

### GrowthDetector (growth.py)
- `TURN_COST_WARNING` — turn > 25% context → inject warning
- `GROWTH_SPIKE` — turn > 50% context → critical warning
- `GROWTH_ACCEL` — history ×2 за 3 turns → inject advice

Thresholds авто-масштабируются от context_length модели.

### PatternDetector (patterns.py)
- `REDUNDANT_READS` — ≥3 одинаковых read_file (с учётом offset/limit) → inject warning
- `STALLED_SESSION` — ≥3 consecutive error cycles → circuit breaker

**Важно:** `_extract_read_path` учитывает `offset` и `limit` — чтение одного файла с разными offset (чанкинг) НЕ считается дубликатом.

### ResultCache (result_cache.py)
- `pre_tool_call`: cache hit → tool не вызывается, 0 токенов
- `post_tool_call`: cache store для read_file, search_files
- Инвалидация на write_file, patch, terminal
- LRU eviction, TTL, per-session isolation

---

## Compressor (compress.py)

### Правила сжатия

| Тип сообщения | Действие |
|--------------|----------|
| user | Всегда сохраняется |
| assistant (text) | Сохраняется (protect_last_n) |
| tool (MCP/RAG, любой размер) | **Никогда не сжимается** |
| tool (result >8K chars) | head(500) + tail(500) + marker |
| tool (result ≤8K) | Сохраняется как есть |
| tool (error) | Коллапсируется в 1 строку |
| pairs tool_call+result | Коллапсируется в 1 строку |

### MCP/RAG инструменты

Инструменты с именем начинающимся на `mcp_` (по умолчанию) **никогда не сжимаются**. Это критически важно для RAG систем (Lodestone, Jira, Confluence) которые отдают результаты 2-4K символов.

---

## Конфигурация

```yaml
plugins:
  rtk_ck:
    enabled: true
    budget:
      warn_pct: 80
      critical_pct: 95
      halt_pct: 100
    growth:
      soft_max_pct: 0.25
      hard_max_pct: 0.50
      max_growth_rate: 2.0
      check_window: 3
    compress:
      protect_first_n: 2
      protect_last_n: 3
      tool_head_chars: 500
      tool_tail_chars: 500
      mcp_tool_prefixes:
        - "mcp_"
    patterns:
      redundant_read_threshold: 3
      stalled_threshold: 3
    result_cache:
      max_size: 100
      ttl_turns: 20

context:
  engine: rtk_ck
  threshold_percent: 0.50
  protect_first_n: 3
  protect_last_n: 6
```

---

## Метрики (rtk_ck_stat)

```
rtk_ck_stat(format="text")  # текстовый отчёт
rtk_ck_stat(format="json")  # JSON для парсинга
rtk_ck_stat(reset=true)     # сброс счётчиков
```

Показывает: budget signals, growth signals, pattern signals, cache hits/misses, compression savings.

---

## Тесты: 213

---

## Связанные документы

- [rtk-v2.md](rtk-v2.md) — RTK v2: Reduced Token Kernel (tool result compression)
