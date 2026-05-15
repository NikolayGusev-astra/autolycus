"""
plugins/rtk/monitor.py — Measurement framework for RTK filter.

Tracks per-tool and global compression stats.
Exports to JSON for dashboard/audit.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from typing import Any

_lock = threading.Lock()
_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
    "count": 0,
    "total_before": 0,
    "total_after": 0,
})


def record(tool_name: str, before: int, after: int) -> None:
    """Record a compression event for *tool_name*.

    Thread-safe. Updates per-tool aggregated stats.
    """
    with _lock:
        s = _stats[tool_name]
        s["count"] += 1
        s["total_before"] += before
        s["total_after"] += after


def stats() -> dict[str, dict[str, Any]]:
    """Return per-tool stats with savings percentage computed."""
    with _lock:
        result = {}
        for tool, s in _stats.items():
            total_before = s["total_before"]
            total_after = s["total_after"]
            savings_pct = 0.0
            if total_before > 0:
                savings_pct = ((total_before - total_after) / total_before) * 100
            result[tool] = {
                "count": s["count"],
                "total_before": total_before,
                "total_after": total_after,
                "savings_pct": round(savings_pct, 1),
            }
        return result


def global_stats() -> dict[str, Any]:
    """Return global stats across all tools."""
    per_tool = stats()
    total_before = sum(s["total_before"] for s in per_tool.values())
    total_after = sum(s["total_after"] for s in per_tool.values())
    savings_pct = 0.0
    if total_before > 0:
        savings_pct = ((total_before - total_after) / total_before) * 100
    return {
        "total_before": total_before,
        "total_after": total_after,
        "savings_pct": round(savings_pct, 1),
        "tools": len(per_tool),
        "total_calls": sum(s["count"] for s in per_tool.values()),
    }


def reset() -> None:
    """Clear all accumulated stats."""
    with _lock:
        _stats.clear()


def export_json(path: str) -> None:
    """Export stats as JSON file."""
    data = {
        "global": global_stats(),
        "per_tool": stats(),
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
