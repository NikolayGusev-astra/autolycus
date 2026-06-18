# RTK v2 — Plugin Reference

## Overview

RTK (Reduced Token Kernel) v2 is a plugin that provides:

1. **Tool output compression** — saves ~84% tokens by head/tail truncation
2. **Metadata tracking** — per-tool-call compression stats in state.db
3. **Pattern detection** — semantic error/loop/budget detection
4. **Signal injection** — pre-turn system prompt alerts
5. **Verification storage** — claims/flags via kvstore (for Verifier middleware)

## Quick Start

```bash
# Enable in ~/.hermes/config.yaml
plugins:
  enabled:
    - rtk

# Configure (optional)
plugins:
  rtk:
    head_chars: 500
    tail_chars: 1000
    min_result_chars: 500
```

## Architecture

```
┌──────────────────────────────────────────────────┐
│                  transform_tool_result           │
│  1. store.save(output) → rtk-cache/{uuid}.txt   │
│  2. compressor.compress(output) → context        │
│  3. metadata.build() → pending buffer            │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              flush_pending_metadata()             │
│  (called before pattern detection)                │
│  writes to state.db messages.rtk_metadata         │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              Pattern Detectors                    │
│  • CONSECUTIVE_ERRORS — 3+ errors in a row       │
│  • TOOL_LOOP — same tool failing repeatedly      │
│  • BUDGET_EXCEEDED — cost exceeds limit          │
│  • NO_PROGRESS — identical read-only results     │
└─────────────────────┬────────────────────────────┘
                      │
┌─────────────────────▼────────────────────────────┐
│              Signal Injector                     │
│  signal.pre_turn() → inject into system prompt   │
│  One-shot: cleared after first read              │
└──────────────────────────────────────────────────┘
```

## Files

### `plugins/rtk/__init__.py`
Main plugin entry point. Registers `transform_tool_result` hook and `rtk_recover` tool.

**Key functions:**
- `transform_tool_result(tool_name, args, result, session_id, tool_call_id, duration_ms)` — hook
- `flush_pending_metadata()` — writes buffered metadata to state.db
- `_detect_error(tool_name, result)` — heuristic error detection

### `plugins/rtk/metadata.py`
State.db integration.

**Key functions:**
- `build_metadata(tool_name, persist_id, original_len, compressed_len, strategy, error, duration_ms)` → JSON
- `attach_by_tool_call_id(db, session_id, tool_call_id, metadata_json)` → writes to state.db
- `get_tool_sequence(db, session_id, limit, offset)` → recent tool calls with metadata
- `get_recent_errors(db, session_id, count)` → last N consecutive errors
- `get_session_cost(db, session_id)` → cost from sessions table

### `plugins/rtk/kvstore.py`
Session-scoped key-value store on disk.

**Key functions:**
- `put(session_id, key, data)` → `rtk-cache/{session_id}/{key}.json`
- `get(session_id, key)` → data
- `delete(session_id, key)` / `delete_session(session_id)`
- `list_sessions()` / `list_keys(session_id)`
- `get_usage_budget(session_id)` → spent/budget/remaining

### `plugins/rtk/pattern.py`
Semantic pattern detection.

**Detectors:**
- `detect_consecutive_errors(db, session_id, threshold=3)` → Signal
- `detect_tool_loop(db, session_id, window=6)` → Signal
- `detect_budget_exceeded(db, session_id, budget_limit=10.0)` → Signal
- `detect_no_progress(db, session_id, threshold=3)` → Signal
- `run_all(db, session_id, ...)` → List[Signal]
- `best_signal(db, session_id, ...)` → most severe Signal

### `plugins/rtk/signal.py`
Pre-turn signal injector.

**Key functions:**
- `pre_turn(db, session_id, budget_limit=10.0)` → injection string (empty if nothing)
- `store(session_id, signal)` / `clear(session_id)` / `read(session_id)`
- `get_injection(session_id)` → one-shot injection text

## State.db Schema Change

Added column to `messages` table:
```sql
rtk_metadata TEXT  -- JSON: {persist_id, chars_saved, original_len, compressed_len, 
                   --         savings_pct, strategy, tool, error, ts, duration_ms}
```

Auto-migrated via SessionDB._ensure_schema() — no manual migration needed.

## Configuration

```yaml
plugins:
  rtk:
    enabled: true
    head_chars: 500       # chars to keep from start
    tail_chars: 1000      # chars to keep from end
    min_result_chars: 500  # skip compression for smaller results
```

## Integration Points

- **transform_tool_result** hook — receives `session_id`, `tool_call_id`, `duration_ms`
- **state.db messages.rtk_metadata** — per-tool-call compression stats
- **rtk-cache/{session_id}/{key}.json** — verifier/flags/usage storage

## Testing

```bash
# Run RTK v2 tests
cd /opt/autolycus
python3 -m pytest tests/plugins/rtk/ -v

# Expected: 56 passed
```
