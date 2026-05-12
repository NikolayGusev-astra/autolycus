"""
Ultra Governance — RTK Filter (Reduced Token Kernel)

Post-execution output compression for tool results.  Three strategies:

1. **Head/Tail truncation** — keep the first N chars and last M chars
   of long outputs, emit a summary line showing how many chars were cut.

2. **Repeat compaction** — detect repeated blocks of N+ identical lines
   and collapse them into a single "repeated X times" line.

3. **Max output cap** — hard character limit that truncates aggressively
   when a tool result exceeds it.

Configuration reads from ``config.yaml`` → ``plugins.ultra_governance.rtk``.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@staticmethod
def _load_rtk_config() -> Dict[str, Any]:
    """Load RTK-specific config from YAML with sensible defaults."""
    config = {
        "enabled": True,
        "head_chars": 2000,
        "tail_chars": 1000,
        "min_repeat_lines": 5,
        "max_output_chars": 10000,
    }

    try:
        from hermes_cli.config import cfg_get

        root = cfg_get("plugins", "ultra_governance", default={})
        rtk_cfg = root.get("rtk", {})
        if isinstance(rtk_cfg, dict):
            for key in config:
                val = rtk_cfg.get(key)
                if val is not None:
                    if isinstance(val, bool) or isinstance(val, (int, float)):
                        config[key] = val
    except Exception as exc:
        logger.debug("ultra-governance RTK config load error: %s", exc)

    return config


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def _compact_repeats(
    text: str, min_repeat_lines: int = 5
) -> str:
    """Collapse repeated line sequences of *min_repeat_lines* or more.

    "foo\\nbar\\nbar\\nbar\\nbar\\nbar\\nbaz" →
    "foo\\nbar\\n    (repeated 5 times)\\nbaz"
    """
    lines = text.splitlines(keepends=True)
    if len(lines) < min_repeat_lines:
        return text

    result: list[str] = []
    i = 0
    while i < len(lines):
        j = i + 1
        # Count consecutive identical lines
        while j < len(lines) and lines[j] == lines[i]:
            j += 1
        count = j - i
        if count >= min_repeat_lines:
            # Emit one copy + a note
            result.append(lines[i].rstrip("\n"))
            result.append(f"\n    ⏱ (repeated {count} times, {count} lines)\n")
        elif count > 1:
            # Fewer than threshold — still worth noting but less aggressively
            result.extend(lines[i:j])
        else:
            result.append(lines[i])
        i = j

    return "".join(result)


def _head_tail_truncate(
    text: str,
    head_chars: int = 2000,
    tail_chars: int = 1000,
    max_total: int = 10000,
) -> str:
    """Keep first N + last M chars, with a truncation note in the middle."""
    if len(text) <= max_total:
        # Under the hard cap — still apply head/tail only if it saves tokens
        if len(text) <= head_chars + tail_chars:
            return text
        # Apply head/tail to long but under-limit text
        head = text[:head_chars]
        tail = text[-tail_chars:]
        middle_len = len(text) - head_chars - tail_chars
        if middle_len <= 0:
            return text
        return (
            f"{head}\n\n"
            f"... [truncated {middle_len} chars of intermediate output]\n\n"
            f"{tail}"
        )

    # Exceeds hard cap — aggressive truncation
    head = text[:head_chars]
    tail = text[-tail_chars:]
    cut = len(text) - head_chars - tail_chars
    return (
        f"{head}\n\n"
        f"... [WARNING: output capped at {max_total} chars, "
        f"truncated {cut} chars]\n\n"
        f"{tail}"
    )


def apply(text: str, raw: bool = False) -> str:
    """Run the full RTK filter pipeline on *text*.

    1. Compact repeated lines
    2. Head/tail truncation
    3. Hard character cap

    If *raw* is True, the pipeline is bypassed (per-call bypass).
    """
    if raw:
        return text

    config = _load_rtk_config()
    if not config["enabled"]:
        return text

    if not isinstance(text, str) or not text:
        return text

    # Step 1: Repeat compaction
    text = _compact_repeats(text, min_repeat_lines=config["min_repeat_lines"])

    # Step 2: Head/tail + cap
    text = _head_tail_truncate(
        text,
        head_chars=config["head_chars"],
        tail_chars=config["tail_chars"],
        max_total=config["max_output_chars"],
    )

    return text


# ---------------------------------------------------------------------------
# Transform hook
# ---------------------------------------------------------------------------


def transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[str]:
    """``transform_tool_result`` hook: applies RTK filtering.

    Only processes string results. Returns a filtered string or ``None``
    to leave the result unchanged.

    Per-call bypass: if ``args`` contains ``rtk_raw=True``, filtering is
    skipped for this call.  Useful for debugging or when the LLM needs
    the complete unfiltered output (e.g. large JSON, logs).
    """
    if not isinstance(result, str) or not result:
        return None

    # Skip small results — not worth the overhead
    if len(result) < 500:
        return None

    # Per-call bypass via args
    raw_bypass = isinstance(args, dict) and args.get("rtk_raw", False)
    filtered = apply(result, raw=raw_bypass)

    if filtered != result:
        saved = len(result) - len(filtered)
        if saved > 0:
            logger.debug(
                "RTK: %s → %d chars (saved %d, %.0f%%)",
                tool_name,
                len(filtered),
                saved,
                (saved / len(result)) * 100,
            )

    return filtered
