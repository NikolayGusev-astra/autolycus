"""
plugins/rtk/compressor.py — Type-aware compression dispatcher.

Dispatches to the right strategy based on tool_name.
Each strategy returns (compressed_text, stats_dict).
Unknown tools fall back to generic head/tail.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from plugins.rtk import store
from plugins.rtk.strategies import terminal, read_file, search_files

_HOOK_IGNORE_TOOLS = {
    "write_file",      # result is a short dict/status
    "patch",           # result is a short diff
    "send_message",    # result is a short confirmation
    "delegate_task",   # result is managed by parent
    "memory",          # result is small
    "skill_view",      # result was already formatted
    "read_file",       # handled by read_file strategy
    "terminal",        # handled by terminal strategy
    "search_files",    # handled by search_files strategy
}


def _generic_head_tail(text: str, head_chars: int = 500, tail_chars: int = 1000,
                       persist_id: str = "") -> str:
    """Generic head/tail fallback for unknown tool types."""
    if len(text) <= head_chars + tail_chars:
        return text
    head = text[:head_chars]
    tail = text[-tail_chars:]
    middle_len = len(text) - head_chars - tail_chars
    result = f"{head}\n... [truncated {middle_len} chars]\n{tail}"
    if persist_id:
        result += f"\n--- full output: rtk-recover {persist_id} ---\n"
    return result


def compress(
    tool_name: str,
    text: str,
    tool_args: Optional[Dict[str, Any]] = None,
    persist_id: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Run type-aware compression on *text*.

    Returns (compressed_text, stats_dict).
    Stats includes at minimum ``chars_saved``, ``original_len``, ``compressed_len``.
    """
    cfg = config or {}
    head = cfg.get("head_chars", 500)
    tail = cfg.get("tail_chars", 1000)

    # Skip small results — not worth the overhead
    if len(text) < 500:
        return text, {"chars_saved": 0, "original_len": len(text), "compressed_len": len(text)}

    if tool_name == "terminal":
        return terminal.compress(text, head_chars=head, tail_chars=tail,
                                 persist_id=persist_id, tool_args=tool_args,
                                 config=cfg)
    elif tool_name == "read_file":
        return read_file.compress(text, head_chars=head, tail_chars=tail,
                                  persist_id=persist_id, tool_args=tool_args,
                                  config=cfg)
    elif tool_name == "search_files":
        return search_files.compress(text, head_chars=head, tail_chars=tail,
                                     persist_id=persist_id, tool_args=tool_args,
                                     config=cfg)
    else:
        # Generic fallback
        compressed = _generic_head_tail(text, head_chars=head, tail_chars=tail,
                                        persist_id=persist_id)
        chars_saved = len(text) - len(compressed)
        return compressed, {
            "chars_saved": chars_saved,
            "original_len": len(text),
            "compressed_len": len(compressed),
        }
