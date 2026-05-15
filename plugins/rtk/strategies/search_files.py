"""
plugins/rtk/strategies/search_files.py — Search files output strategy.

Keeps all file paths (they're short), truncates content per match.
When there are many matches, group by directory first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

# Max matches to show in detail; beyond this, show only path counts
_MAX_DETAIL_MATCHES = 50
# Max chars of context per match (line content)
_MAX_CONTEXT_PER_MATCH = 120


def _group_by_directory(lines: list[str]) -> dict[str, list[str]]:
    """Group search result lines by their directory."""
    groups: dict[str, list[str]] = {}
    for line in lines:
        path_part = line.split(":")[0] if ":" in line else line
        try:
            dirname = str(Path(path_part).parent)
        except Exception:
            dirname = "."
        groups.setdefault(dirname, []).append(line)
    return groups


def compress(
    text: str,
    head_chars: int = 500,
    tail_chars: int = 1000,
    persist_id: str = "",
    tool_args: Dict[str, Any] | None = None,
) -> Tuple[str, Dict[str, Any]]:
    """Compress search_files output: keep paths, truncate line content.

    Strategies:
    - Total matches ≤ _MAX_DETAIL_MATCHES → keep all, truncate per-line content
    - Total matches > _MAX_DETAIL_MATCHES → group by directory, show counts + paths
    """
    original_len = len(text)

    if not text or original_len <= 3000:  # search results are usually compact
        return text, {"chars_saved": 0, "original_len": original_len, "compressed_len": original_len}

    lines = text.splitlines(keepends=True)
    total_matches = len(lines)

    if total_matches <= _MAX_DETAIL_MATCHES:
        # Keep all paths, truncate per-line content
        compressed_lines = []
        for line in lines:
            line_stripped = line.rstrip("\n\r")
            if len(line_stripped) > _MAX_CONTEXT_PER_MATCH:
                line_stripped = line_stripped[:_MAX_CONTEXT_PER_MATCH] + "..."
            compressed_lines.append(line_stripped + "\n")
        compressed = "".join(compressed_lines)
    else:
        # Group by directory, show counts
        groups = _group_by_directory(lines)
        compressed_lines = [f"Total: {total_matches} matches across {len(groups)} directories\n\n"]
        for dirname, group_lines in sorted(groups.items()):
            compressed_lines.append(f"{dirname}/ ({len(group_lines)} matches)\n")
            # Show first 3 paths per directory
            for gl in group_lines[:3]:
                path_part = gl.split(":")[0] if ":" in gl else gl.strip()
                compressed_lines.append(f"  {path_part}\n")
            if len(group_lines) > 3:
                compressed_lines.append(f"  ... and {len(group_lines) - 3} more\n")
            compressed_lines.append("\n")
        compressed = "".join(compressed_lines)

    if persist_id:
        compressed += f"--- full results: rtk-recover {persist_id} ---\n"

    saved = original_len - len(compressed)
    stats = {
        "chars_saved": saved,
        "original_len": original_len,
        "compressed_len": len(compressed),
        "total_matches": total_matches,
    }
    return compressed, stats
