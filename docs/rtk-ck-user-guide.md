# RTK-CK User Guide

RTK-CK (Conversation Kernel) — система управления контекстом AI-агента. Сжимает историю диалога, детектирует аномалии, предотвращает переполнение контекста.

---

## Быстрый старт

RTK-CK включён по умолчанию. Минимальная конфигурация:

```yaml
plugins:
  rtk_ck:
    enabled: true
```

Этого достаточно для работы. Все параметры имеют разумные значения по умолчанию.

---

## Основные компоненты

### 1. Сжатие контекста (Compressor)

Сжимает длинные tool-ответы в истории диалога, сохраняя начало и конец.

**Когда сжимается:**
- Tool-ответы длиннее 8000 символов → `head(500) + tail(500) + marker`
- Пары `tool_call + tool_result` → коллапсируются в 1 строку
- Ошибки → сокращаются до 1 строки

**Когда НЕ сжимается:**
- Сообщения пользователя — всегда сохраняются
- MCP/RAG инструменты (имена начинаются на `mcp_`) — никогда не сжимаются
- Короткие ответы (<8000 символов) — сохраняются как есть
- Первые и последние N сообщений — защищены

### 2. Детекторы аномалий

**BudgetScanner** — следит за заполненностью контекста:
- 80% → предупреждение
- 95% → критическое предупреждение
- 100% → circuit breaker (остановка сессии)

**GrowthDetector** — следит за скоростью роста:
- Turn > 25% контекста → предупреждение
- Turn > 50% контекста → критическое предупреждение

**PatternDetector** — находит паттерны:
- 3+ одинаковых `read_file` → REDUNDANT_READS
- 3+ ошибок подряд → STALLED_SESSION → circuit breaker

### 3. ResultCache

Кэширует результаты `read_file` и `search_files`. Если один и тот же файл читается повторно — результат берётся из кэша, не тратятся токены.

---

## Настройка MCP/RAG инструментов

По умолчанию инструменты с префиксом `mcp_` не сжимаются. Если ваш RAG сервер использует другой префикс:

```yaml
plugins:
  rtk_ck:
    compress:
      mcp_tool_prefixes:
        - "mcp_"           # по умолчанию
        - "rag_"           # ваш RAG сервер
        - "knowledge_"     # ещё один префикс
```

**Важно:** каждый префикс проверяется через `startswith()`. Префикс `mcp_` покроет `mcp_lodestone`, `mcp_jira`, `mcp_confluence` и т.д. Все инструменты с этим префиксом будут сохраняться полностью, независимо от длины ответа.

---

## Настройка детектора REDUNDANT_READS

Детектор считает повторные чтения одного файла. Учитывает `offset` и `limit` — чтение разных частей файла НЕ считается дубликатом.

**Настройка порога:**

```yaml
plugins:
  rtk_ck:
    patterns:
      redundant_read_threshold: 3    # количество чтений для срабатывания
      stalled_threshold: 3           # количество ошибок для STALLED_SESSION
```

**Увеличьте порог**, если детектор ложно срабатывает на вашей работе:

```yaml
plugins:
  rtk_ck:
    patterns:
      redundant_read_threshold: 5    # 5 чтений вместо 3
```

---

## Настройка сжатия

```yaml
plugins:
  rtk_ck:
    compress:
      protect_first_n: 2             # первые N user-сообщений не сжимать
      protect_last_n: 3              # последних N tool-результатов не сжимать
      tool_head_chars: 500           # сколько символов оставить сначала
      tool_tail_chars: 500           # сколько символов оставить с конца
```

**Если нужно сохранять больше контекста:**

```yaml
plugins:
  rtk_ck:
    compress:
      tool_head_chars: 1000
      tool_tail_chars: 1000
      protect_last_n: 5
```

---

## Отключение компонентов

Можно отключить RTK-CK целиком:

```yaml
plugins:
  rtk_ck:
    enabled: false
```

Или отключить отдельные детекторы через конфигурацию плагина.

---

## Мониторинг (rtk_ck_stat)

Встроенный tool для проверки состояния RTK-CK:

```
rtk_ck_stat(format="text")   # текстовый отчёт
rtk_ck_stat(format="json")   # JSON для машинной обработки
rtk_ck_stat(reset=true)      # сбросить счётчики
```

Показывает:
- Количество budget/growth/pattern signals
- Cache hits/misses
- Количество предотвращённых вызовов (blocks)
- Экономия токенов

---

## Типичные сценарии

### RAG-сервер отдаёт короткие ответы (2-4K)

По умолчанию работает корректно — MCP инструменты не сжимаются. Если ваш RAG сервер не использует префикс `mcp_` — добавьте его префикс в конфигурацию.

### Агент часто читает один файл по частям (offset/limit)

Это нормальное поведение. Детектор REDUNDANT_READS учитывает offset/limit — чтение разных частей файла не считается дубликатом.

### Ложные срабатывания REDUNDANT_READS

Если детектор срабатывает слишком часто:
1. Увеличьте `redundant_read_threshold`
2. Убедитесь что вы не читаете один файл с одинаковым offset/limit

### Контекст всё равно переполняется

1. Проверьте BudgetScanner — на каком проценте срабатывает
2. Увеличьте `tool_head_chars` и `tail_chars`
3. Увеличьте `protect_last_n`
4. Проверьте GrowthDetector — какой turn добавляет больше всего токенов

---

## Пример полной конфигурации

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
        - "rag_"
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
