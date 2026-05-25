"""RTK-CK Metrics — aggregation, formatting, and cost tracking.

Collects stats from all RTK-CK components and formats them for:
- /rtk_ck stat command (human-readable)
- Context bar extension (compact status line)
- Cost savings estimation
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default cost per 1M tokens (USD) — can be overridden per provider
_DEFAULT_COST_PER_MILLION = 3.0


class MetricsCollector:
    """Aggregates RTK-CK stats across all components."""

    def __init__(self):
        self._total_tokens_saved: int = 0
        self._compression_count: int = 0
        self._budget_signals: int = 0
        self._growth_signals: int = 0
        self._pattern_signals: int = 0
        self._dedup_chars_saved: int = 0
        self._prefetch_stale_signals: int = 0
        self._pointer_compressions: int = 0
        self._cost_per_million_tokens: float = _DEFAULT_COST_PER_MILLION

    def record_compression(self, original_tokens: int, compressed_tokens: int) -> None:
        """Record a compression event."""
        self._total_tokens_saved += max(0, original_tokens - compressed_tokens)
        self._compression_count += 1

    def record_signal(self, code: str) -> None:
        """Record a signal (budget, growth, pattern, etc.)."""
        if code.startswith("BUDGET_"):
            self._budget_signals += 1
        elif code.startswith("GROWTH_") or code == "TURN_COST_WARNING":
            self._growth_signals += 1
        elif code in ("REDUNDANT_READS", "STALLED_SESSION"):
            self._pattern_signals += 1
        elif code == "PREFETCH_STALE":
            self._prefetch_stale_signals += 1
        elif code == "CACHE_HIT":
            self._cache_hits = getattr(self, '_cache_hits', 0) + 1

    def record_pattern(self, code: str) -> None:
        """Record a pattern detection."""
        self._pattern_signals += 1

    def record_dedup(self, saved_chars: int) -> None:
        """Record a dedup event."""
        self._dedup_chars_saved += saved_chars

    def record_pointer_compression(self) -> None:
        """Record a pointer compression."""
        self._pointer_compressions += 1

    def set_cost_per_million_tokens(self, cost_usd: float) -> None:
        """Set cost per 1M tokens for savings estimation."""
        self._cost_per_million_tokens = cost_usd

    def get_metrics(self) -> Dict[str, Any]:
        """Return aggregated metrics dict."""
        cost_saved = (self._total_tokens_saved / 1_000_000) * self._cost_per_million_tokens
        return {
            "total_tokens_saved": self._total_tokens_saved,
            "compression_count": self._compression_count,
            "budget_signals": self._budget_signals,
            "growth_signals": self._growth_signals,
            "pattern_signals": self._pattern_signals,
            "dedup_chars_saved": self._dedup_chars_saved,
            "prefetch_stale_signals": self._prefetch_stale_signals,
            "pointer_compressions": self._pointer_compressions,
            "cost_saved_usd": round(cost_saved, 4),
            "cost_per_million_tokens": self._cost_per_million_tokens,
        }

    def reset(self) -> None:
        """Reset all metrics."""
        self._total_tokens_saved = 0
        self._compression_count = 0
        self._budget_signals = 0
        self._growth_signals = 0
        self._pattern_signals = 0
        self._dedup_chars_saved = 0
        self._prefetch_stale_signals = 0
        self._pointer_compressions = 0


def _format_number(n: int) -> str:
    """Format number with K/M suffix."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def format_stat_line(metrics: Dict[str, Any]) -> str:
    """Format metrics as a human-readable status line.

    Example output:
    "RTK-CK: 800K tokens saved (3 compressions) | 2 signals | $2.40 saved"
    """
    parts = ["RTK-CK:"]

    tokens_saved = metrics.get("total_tokens_saved", 0)
    if tokens_saved > 0:
        parts.append(f"{_format_number(tokens_saved)} tokens saved")
        comp_count = metrics.get("compression_count", 0)
        if comp_count > 0:
            parts.append(f"({comp_count} compressions)")

    total_signals = (
        metrics.get("budget_signals", 0)
        + metrics.get("growth_signals", 0)
        + metrics.get("pattern_signals", 0)
        + metrics.get("prefetch_stale_signals", 0)
    )
    if total_signals > 0:
        parts.append(f"| {total_signals} signals")

    cost_saved = metrics.get("cost_saved_usd", 0.0)
    if cost_saved > 0:
        parts.append(f"| ${cost_saved:.2f} saved")

    dedup_chars = metrics.get("dedup_chars_saved", 0)
    if dedup_chars > 0:
        parts.append(f"| dedup {_format_number(dedup_chars)} chars")

    pointer = metrics.get("pointer_compressions", 0)
    if pointer > 0:
        parts.append(f"| {pointer} pointers")

    if len(parts) == 1:
        parts.append("no activity")

    return " ".join(parts)


def format_context_bar_line(metrics: Optional[Dict[str, Any]]) -> str:
    """Format a compact RTK-CK info string for the context bar.

    Returns empty string if no metrics or no activity.
    Example: "│ RTK-CK: 800K saved │"
    """
    if not metrics:
        return ""

    tokens_saved = metrics.get("total_tokens_saved", 0)
    if tokens_saved == 0:
        return ""

    total_signals = (
        metrics.get("budget_signals", 0)
        + metrics.get("growth_signals", 0)
        + metrics.get("pattern_signals", 0)
    )

    parts = [f"RTK-CK: {_format_number(tokens_saved)} saved"]
    if total_signals > 0:
        parts.append(f"{total_signals} signals")

    return "│ " + " | ".join(parts) + " │"


# Module-level singleton
_metrics_collector = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Return the module-level MetricsCollector singleton."""
    return _metrics_collector