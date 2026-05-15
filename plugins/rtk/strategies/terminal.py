"""
plugins/rtk/strategies/terminal.py — Terminal output strategy.

Preserves:
- Head (first N chars) — command headers, initial output
- Tail (last M chars) — error messages, exit codes, result summaries
- Error-like lines anywhere in the output are extracted and appended

Recovery link is injected at the end with the persist_id.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Tuple

# Patterns that indicate error/critical lines worth preserving anywhere
_ERROR_PATTERNS = [
    re.compile(r"(?i)(error|exception|traceback|failed|failure|fatal|critical)"),
    re.compile(r"(?i)(exit\s+\d+|returned\s+non-zero|cannot|unable|denied|refused)"),
    re.compile(r"(?i)(segmentation\s+fault|core\s+dumped|killed|abort)"),
]


def _extract_error_lines(text: str) -> list[str]:
    """Extract lines matching error patterns (for mid-output preservation)."""
    errors = []
    for i, line in enumerate(text.splitlines()):
        for pat in _ERROR_PATTERNS:
            if pat.search(line):
                errors.append(f"  [{i}] {line.strip()}")
                break
    return errors


def compress(
    text: str,
    head_chars: int = 500,
    tail_chars: int = 1000,
    persist_id: str = "",
    tool_args: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Compress terminal output: head + error extraction + tail.

    Returns (compressed_text, stats_dict).
    """
    original_len = len(text)

    if not text or original_len <= head_chars + tail_chars + 200:  # ignore tiny middle
        return text, {"chars_saved": 0, "original_len": original_len, "compressed_len": original_len}

    # Don't bother if overhead exceeds savings
    estimated_overhead = 200  # truncation note + error header
    if original_len - head_chars - tail_chars < estimated_overhead:
        return text, {"chars_saved": 0, "original_len": original_len, "compressed_len": original_len}

    head = text[:head_chars]
    tail = text[-tail_chars:]
    errors = _extract_error_lines(text)

    # Build compressed output
    parts: list[str] = []
    parts.append(head)
    middle_len = original_len - head_chars - tail_chars
    if middle_len > 100:  # Only note if significant
        parts.append(f"\n... [truncated {middle_len} chars of terminal output]\n")

    if errors:
        parts.append(f"\n--- errors found in output ({len(errors)} lines) ---\n")
        parts.extend(errors[:20])  # cap at 20 error lines
        if len(errors) > 20:
            parts.append(f"... and {len(errors) - 20} more error lines\n")
        parts.append("\n")

    parts.append(tail)

    if persist_id:
        parts.append(f"\n--- full output: rtk-recover {persist_id} ---\n")

    compressed = "".join(parts)
    saved = original_len - len(compressed)
    stats = {
        "chars_saved": saved,
        "original_len": original_len,
        "compressed_len": len(compressed),
        "errors_found": len(errors),
    }
    return compressed, stats
