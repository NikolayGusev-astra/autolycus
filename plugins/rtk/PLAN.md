# RTK v2 — План реализации

## Что меняем

### 1. Metadata — добавляем RTK-данные в state.db

**messages** таблица получит новую колонку `rtk_metadata TEXT`:
```json
{
  "persist_id": "uuid",
  "chars_saved": 42000,
  "original_len": 50000,
  "compression_strategy": "read_file",
  "error": false,
  "tool_name": "search_files",
  "duration_ms": 15.3
}
```

**Где:** `plugins/rtk/metadata.py`
**Триггер:** внутри `transform_tool_result` после компрессии
**Читается:** pattern_detector, verifier

### 2. Key-value store — rtk.put/get

```
~/.autolycus/rtk-cache/{session_id}/{key}.json
```

- `rtk.put(sid, "claims", data)` → `rtk-cache/{sid}/claims.json`
- `rtk.get(sid, "flags")` → читает `rtk-cache/{sid}/flags.json`
- `rtk.delete_session(sid)` → удаляет `rtk-cache/{sid}/`

**Где:** `plugins/rtk/kvstore.py`
**Использует существующий** `store._resolve_cache_dir()` и threading.Lock

### 3. Pattern detector — semantic error detection

Расширяет `agent/tool_guardrails.py`:

```
existing: hash(args) == hash(failed_args) → exact_failure
new:      rtk_metadata.error == true AND 
          error_text_similar(rtk_metadata, last_3_errors) → semantic_failure
```

```
existing: hash(result) == hash(prev_result) → no_progress  
new:      rtk_metadata.persist_id exists AND
          3+ последовательных terminal.calls с error=true → loop_detected
```

**Где:** `plugins/rtk/pattern.py`
**Читает:** `state.db messages.rtk_metadata` через metadata.get_tool_sequence(sid, N=5)

### 4. Signal injector — pre-turn system prompt

```
pre-turn → rtk.get(sid, "signal")
  if signal == "STOP":
    inject: "⚠ SYSTEM: 3 ошибки подряд. Прерви текущую стратегию."
  if signal == "BUDGET":
    inject: "⚠ SYSTEM: Бюджет сессии $X из $Y. Оптимизируй вызовы."
```

**Где:** `plugins/rtk/signal.py`
**Интеграция:** через `transform_tool_result` hook (пишет сигнал) и run_agent.py pre-turn (читает)

### 5. Tests

`tests/plugins/rtk/`:
- `test_metadata.py` — запись и чтение rtk_metadata из state.db
- `test_kvstore.py` — put/get/delete_session
- `test_pattern.py` — semantic error detection
- `test_signal.py` — injector format and conditions
- `test_integration.py` — full pipeline: tool call → RTK → metadata → detector → signal

## Порядок реализации

1. metadata.py + тесты
2. kvstore.py + тесты  
3. pattern.py + тесты
4. signal.py + тесты
5. Интеграция в __init__.py
6. integration test
7. Запуск всех тестов
