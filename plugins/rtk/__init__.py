"""
plugins/rtk — RTK Filter (Reduced Token Kernel)

Non-destructive tool result compressor with recovery.

Architecture:
  1. Persistence — save full result to disk (store.py)
  2. Type-aware compression — per-tool strategy (compressor.py)
  3. Recovery — agent reads file if needed (rtk_recover tool)
  4. Measurement — per-tool aggregated stats (monitor.py)

Registered hooks:
  transform_tool_result  — save + compress tool outputs

Registered tools:
  rtk_recover            — retrieve full data by persist_id
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from plugins.rtk import compressor, monitor, store

logger = logging.getLogger(__name__)

# Tools whose non-string results we should still try to stringify
_STRINGIFIABLE = {"search_files", "terminal"}

# Default thresholds for when to process
_MIN_RESULT_CHARS = 500

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
    **_: Any,
) -> Optional[str]:
    """``transform_tool_result`` hook: save + compress tool output.

    Returns compressed string, or None to leave result unchanged.
    Full data is always persisted to disk — never destroyed.
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
    try:
        persist_id = store.save(result)
    except Exception as exc:
        logger.debug("RTK: persist failed for %s: %s", tool_name, exc)
        return None

    # 2. Compress
    try:
        compressed, stats = compressor.compress(
            tool_name, result, tool_args=args,
            persist_id=persist_id, config=cfg,
        )
    except Exception as exc:
        logger.debug("RTK: compress failed for %s: %s — returning raw", tool_name, exc)
        return result

    # 3. Record metrics
    monitor.record(tool_name, before=len(result), after=len(compressed))

    if stats.get("chars_saved", 0) > 0:
        logger.debug(
            "RTK: %s → %d chars (saved %d, %.0f%%)",
            tool_name, len(compressed), stats["chars_saved"],
            (stats["chars_saved"] / max(len(result), 1)) * 100,
        )

    return compressed


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

    cfg = _load_config()
    logger.info("rtk filter loaded (enabled=%s)", cfg["enabled"])
