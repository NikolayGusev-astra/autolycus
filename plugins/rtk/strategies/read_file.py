"""
plugins/rtk/strategies/read_file.py — Read file output strategy.

Preserves the section the agent was actually reading (from tool_args offset/limit).
If args are available, keep a window around the requested range.
Falls back to head/tail if no args.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

# Window size around the requested offset (chars before and after)
_CONTEXT_WINDOW = 2000


def compress(
    text: str,
    head_chars: int = 500,
    tail_chars: int = 1000,
    persist_id: str = "",
    tool_args: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Compress read_file output, trying to keep the requested section.

    If *tool_args* contains ``offset`` and ``limit``, a window around that
    range is preserved in addition to head and tail.
    """
    original_len = len(text)

    if not text or original_len <= head_chars + tail_chars + _CONTEXT_WINDOW:
        return text, {"chars_saved": 0, "original_len": original_len, "compressed_len": original_len}

    head = text[:head_chars]
    tail = text[-tail_chars:]

    # Extract the section the agent asked for
    section_text = ""
    if tool_args and isinstance(tool_args, dict):
        offset = tool_args.get("offset", None)
        limit = tool_args.get("limit", None)
        if offset is not None and limit is not None:
            try:
                off = int(offset) - 1  # 1-indexed → 0-indexed
                lim = int(limit)
                lines = text.splitlines(keepends=True)
                if 0 <= off < len(lines):
                    start = max(0, off - _CONTEXT_WINDOW)
                    end = min(len(lines), off + lim + _CONTEXT_WINDOW)
                    section_lines = lines[start:end]
                    # Estimate char positions
                    section_start_char = sum(len(l) for l in lines[:start])
                    section_end_char = section_start_char + sum(len(l) for l in section_lines)
                    if section_start_char > head_chars and section_end_char < (original_len - tail_chars):
                        # Section is in the middle — extract it
                        section_text = "".join(section_lines)
            except (ValueError, TypeError):
                pass

    middle_len = original_len - head_chars - tail_chars
    compressed = head

    if section_text:
        # Replace the truncated area with the relevant section
        compressed += f"\n... [truncated {middle_len} chars; showing requested section below]\n\n"
        compressed += section_text

        # If the section doesn't include the tail, add the tail
        section_end_char_est = head_chars + len(section_text) + 200  # rough
        if section_end_char_est < original_len - tail_chars:
            compressed += f"\n... [tail of file]\n\n"
            compressed += tail
    else:
        if middle_len > 100:
            compressed += f"\n... [truncated {middle_len} chars of file content]\n"
        compressed += tail

    if persist_id:
        compressed += f"\n--- full file: rtk-recover {persist_id} ---\n"

    saved = original_len - len(compressed)
    stats = {
        "chars_saved": saved,
        "original_len": original_len,
        "compressed_len": len(compressed),
        "section_preserved": bool(section_text),
    }
    return compressed, stats
