"""
plugins/rtk — RTK Filter (Reduced Token Kernel)

Non-destructive tool result compressor with recovery.

Architecture:
  1. Persistence — save full result to disk (store.py)
  2. Type-aware compression — per-tool strategy (compressor.py)
  3. Recovery — agent reads file if needed (rtk_recover tool)
  4. Measurement — per-tool aggregated stats (monitor.py)
  5. Metadata — per-call compression stats in state.db (metadata.py)
  6. Pattern detection — anomaly detection over tool history (pattern.py)
  7. Signal injection — pre-turn warnings in system prompt (signal.py)

Registered hooks:
  transform_tool_result  — save + compress tool outputs

Registered tools:
  rtk_recover            — retrieve full data by persist_id
  rtk_cleanup            — remove old cached files
"""

from __future__ import annotations

import json
import logging
import os
import time as time_module
from typing import Any, Dict, Optional

from plugins.rtk import compressor, metadata as rtk_metadata, monitor, store

logger = logging.getLogger(__name__)

# Tools whose non-string results we should still try to stringify
_STRINGIFIABLE = {"search_files", "terminal"}

# Default thresholds for when to process
_MIN_RESULT_CHARS = 500

# In-memory buffer for deferred metadata writes (written to state.db later)
# key: (session_id, tool_call_id), value: metadata JSON string
_PENDING_METADATA: Dict[tuple, str] = {}
_PENDING_METADATA_MAX = 500  # cap to prevent OOM on runaway loops


def _evict_pending_metadata() -> int:
    """Evict oldest entries when buffer exceeds max size.
    Returns number of evicted entries.
    """
    global _PENDING_METADATA
    overflow = len(_PENDING_METADATA) - _PENDING_METADATA_MAX
    if overflow <= 0:
        return 0
    # Evict oldest (FIFO)
    keys = list(_PENDING_METADATA.keys())[:overflow]
    for k in keys:
        del _PENDING_METADATA[k]
    logger.debug("RTK: evicted %d pending metadata entries (buffer=%d)", overflow, len(_PENDING_METADATA))
    return overflow


_RTK_RECOVER_SCHEMA = {
    "type": "object",
    "properties": {
        "persist_id": {
            "type": "string",
            "description": "The persist_id from the compressed output to recover full data",
        },
    },
    "required": ["persist_id"],
}

_RTK_CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {
        "max_age_days": {
            "type": "integer",
            "description": "Remove cache files older than N days (default: 30)",
            "default": 30,
        },
    },
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load RTK config from YAML with sensible defaults."""
    config = {
        "enabled": True,
        "head_chars": 500,
        "tail_chars": 1000,
        "min_result_chars": 500,
    }
    try:
        from hermes_cli.config import cfg_get
        root = cfg_get("plugins", "rtk", default={})
        if isinstance(root, dict):
            for key in config:
                val = root.get(key)
                if val is not None:
                    if isinstance(val, bool) or isinstance(val, (int, float)):
                        config[key] = val
    except Exception:
        pass
    return config


# ---------------------------------------------------------------------------
# Transform hook
# ---------------------------------------------------------------------------


def transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    session_id: str = "",
    tool_call_id: str = "",
    duration_ms: float = 0.0,
    **_: Any,
) -> Optional[str]:
    """``transform_tool_result`` hook: save + compress + metadata.

    Returns compressed string, or None to leave result unchanged.
    Full data is always persisted to disk — never destroyed.
    Compression metadata is buffered and written to state.db lazily.
    """
    cfg = _load_config()
    if not cfg["enabled"]:
        return None

    # Skip non-string results that can't be meaningfully compressed
    if not isinstance(result, str):
        return None

    if not result or len(result) < cfg.get("min_result_chars", _MIN_RESULT_CHARS):
        return None

    # Per-call bypass
    if isinstance(args, dict) and args.get("rtk_raw", False):
        return None

    # 1. Persist full result
    _t0 = time_module.time()
    try:
        persist_id = store.save(result)
    except Exception as exc:
        logger.debug("RTK: persist failed for %s: %s", tool_name, exc)
        return None
    _save_dur = (time_module.time() - _t0) * 1000

    # 2. Compress
    _t1 = time_module.time()
    try:
        compressed, stats = compressor.compress(
            tool_name, result, tool_args=args,
            persist_id=persist_id, config=cfg,
        )
    except Exception as exc:
        logger.debug("RTK: compress failed for %s: %s — returning raw", tool_name, exc)
        return result
    _compress_dur = (time_module.time() - _t1) * 1000

    # 3. Record metrics
    monitor.record(tool_name, before=len(result), after=len(compressed))

    # 4. Build metadata and buffer for later state.db write
    try:
        strategy = tool_name if tool_name in ("terminal", "read_file", "search_files") else "head_tail"
        original_len = stats.get("original_len", len(result))
        compressed_len = stats.get("compressed_len", len(compressed))
        error = _detect_error(tool_name, result)
        meta_json = rtk_metadata.build_metadata(
            tool_name=tool_name,
            persist_id=persist_id,
            original_len=original_len,
            compressed_len=compressed_len,
            strategy=strategy,
            error=error,
            duration_ms=_save_dur + _compress_dur,
        )
        if session_id and tool_call_id:
            _PENDING_METADATA[(session_id, tool_call_id)] = meta_json
            _evict_pending_metadata()  # bounded buffer
    except Exception as exc:
        logger.debug("RTK: metadata build failed: %s", exc)

    if stats.get("chars_saved", 0) > 0:
        logger.debug(
            "RTK: %s → %d chars (saved %d, %.0f%%)",
            tool_name, len(compressed), stats["chars_saved"],
            (stats["chars_saved"] / max(len(result), 1)) * 100,
        )

    return compressed


def _detect_error(tool_name: str, result: str) -> bool:
    """Quick heuristic: does the result look like an error?"""
    if not result:
        return False
    lower = result[:500].lower()
    if '"error"' in lower and '"exit_code": 0' not in lower:
        return True
    if tool_name == "terminal" and '"exit_code"' in result:
        import json
        try:
            data = json.loads(result)
            if isinstance(data, dict) and data.get("exit_code", 0) != 0:
                return True
        except (json.JSONDecodeError, TypeError):
            pass
    return False


# ---------------------------------------------------------------------------
# rtk_recover tool
# ---------------------------------------------------------------------------


def _handle_rtk_recover(persist_id: str, **_: Any) -> str:
    """Handle rtk_recover tool call — retrieve full data."""
    data = store.load(persist_id)
    if data is None:
        return json.dumps({"error": f"Persist ID not found: {persist_id}"})
    return data


# ---------------------------------------------------------------------------
# rtk_cleanup tool
# ---------------------------------------------------------------------------


def _handle_rtk_cleanup(max_age_days: int = 30, **_: Any) -> str:
    """Handle rtk_cleanup tool call — remove old cache files."""
    removed = store.cleanup(max_age_days=max_age_days)
    return json.dumps({
        "removed": removed,
        "max_age_days": max_age_days,
    })


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register RTK hooks and tools."""
    ctx.register_hook("transform_tool_result", transform_tool_result)

    ctx.register_tool(
        name="rtk_recover",
        toolset="default",
        schema=_RTK_RECOVER_SCHEMA,
        handler=_handle_rtk_recover,
        emoji="💾",
    )

    ctx.register_tool(
        name="rtk_cleanup",
        toolset="default",
        schema=_RTK_CLEANUP_SCHEMA,
        handler=_handle_rtk_cleanup,
        emoji="🧹",
    )

    cfg = _load_config()
    logger.info("rtk filter loaded (enabled=%s)", cfg["enabled"])


# ---------------------------------------------------------------------------
# Pending metadata flush (called by pattern detector / signal)
# ---------------------------------------------------------------------------


def flush_pending_metadata() -> int:
    """Write buffered metadata to state.db.

    Called before pattern detection runs. Finds messages by
    (session_id, tool_call_id) and updates their rtk_metadata column.

    Returns:
        Number of metadata records flushed.
    """
    import time as time_module  # noqa: F811 — re-import for local use
    if not _PENDING_METADATA:
        return 0

    try:
        from hermes_state import SessionDB
        db = SessionDB()
    except Exception as exc:
        logger.debug("RTK: flush_pending_metadata: cannot open SessionDB: %s", exc)
        return 0

    flushed = 0
    pending = list(_PENDING_METADATA.items())
    for (sid, tcid), meta_json in pending:
        try:
            ok = rtk_metadata.attach_by_tool_call_id(db, sid, tcid, meta_json)
            if ok:
                del _PENDING_METADATA[(sid, tcid)]
                flushed += 1
        except Exception as exc:
            logger.debug("RTK: flush failed for %s/%s: %s", sid, tcid, exc)

    return flushed
