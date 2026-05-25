"""RTK-CK GrowthDetector — conversation growth rate monitoring.

Pure functions. No I/O. Returns Signal or None.

Detects:
- TURN_COST_WARNING: single turn exceeds soft_max_pct of context window
- GROWTH_SPIKE: single turn exceeds hard_max_pct of context window
- GROWTH_ACCEL: history grew N× in check_window turns

Thresholds are automatic percentages of the model's context length:
- soft_max_pct: 25% of context (warning — reduce large reads)
- hard_max_pct: 50% of context (spike  — dangerous)
- config overrides still work for fine-tuning
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from plugins.rtk.pattern import Signal

logger = logging.getLogger(__name__)

# Thresholds as percentages of context window
DEFAULT_SOFT_MAX_PCT = 0.25   # 25% of context — TURN_COST_WARNING
DEFAULT_HARD_MAX_PCT = 0.50   # 50% of context — GROWTH_SPIKE
DEFAULT_MAX_GROWTH_RATE = 2.0
DEFAULT_CHECK_WINDOW = 3


def _resolve_thresholds(context_length: int, config: Optional[Dict[str, Any]] = None) -> tuple[int, int]:
    """Calculate soft/hard token thresholds from context length.

    Resolution order:
    1. Explicit config overrides (soft_max_tokens_per_turn, hard_max_tokens_per_turn)
    2. Config percent overrides (soft_max_pct, hard_max_pct)
    3. Default percentages of context length (25%, 50%)
    """
    cfg = config or {}

    # Explicit token-level overrides take priority
    explicit_soft = cfg.get("soft_max_tokens_per_turn")
    explicit_hard = cfg.get("hard_max_tokens_per_turn")
    if explicit_soft and explicit_hard:
        return int(explicit_soft), int(explicit_hard)

    # Percent overrides (0.0-1.0)
    soft_pct = cfg.get("soft_max_pct", DEFAULT_SOFT_MAX_PCT)
    hard_pct = cfg.get("hard_max_pct", DEFAULT_HARD_MAX_PCT)

    # Clamp to sensible ranges
    soft_pct = max(0.05, min(soft_pct, 0.80))
    hard_pct = max(soft_pct + 0.10, min(hard_pct, 0.95))

    soft_max = int(context_length * soft_pct)
    hard_max = int(context_length * hard_pct)

    # Hard must be > soft
    if hard_max <= soft_max:
        hard_max = int(soft_max * 1.5)

    logger.debug(
        "GrowthDetector thresholds: ctx=%d soft=%d (%.0f%%) hard=%d (%.0f%%)",
        context_length, soft_max, soft_pct * 100, hard_max, hard_pct * 100,
    )

    return soft_max, hard_max


def detect_turn_cost(
    last_turn_tokens: int,
    context_length: int,
    config: Optional[Dict[str, Any]] = None,
) -> Optional[Signal]:
    """Warn when a single turn adds too many tokens.

    Two levels:
    - TURN_COST_WARNING: soft limit exceeded → advise reducing reads
    - GROWTH_SPIKE: hard limit exceeded → strong warning about context overflow
    """
    if last_turn_tokens <= 0 or context_length <= 0:
        return None

    soft_max, hard_max = _resolve_thresholds(context_length, config)

    # Hard limit — danger zone, context overflow risk
    if last_turn_tokens > hard_max:
        ratio = last_turn_tokens / hard_max
        return Signal(
            code="GROWTH_SPIKE",
            severity="critical" if ratio > 2.0 else "warn",
            message=(
                f"Turn added {last_turn_tokens:,} tokens "
                f"({ratio:.1f}× above {hard_max:,} hard limit). "
                f"Critical: avoid large reads or use tools that filter output."
            ),
            count=last_turn_tokens,
            should_halt=ratio > 3.0,
        )

    # Soft limit — getting expensive, suggest optimization
    if last_turn_tokens > soft_max:
        ratio = last_turn_tokens / soft_max
        return Signal(
            code="TURN_COST_WARNING",
            severity="warn",
            message=(
                f"Turn added {last_turn_tokens:,} tokens "
                f"({ratio:.1f}× above {soft_max:,} soft limit). "
                f"Consider: grep/filter instead of full read, or fetch only needed sections."
            ),
            count=last_turn_tokens,
        )

    return None


class GrowthDetector:
    """Monitors conversation growth rate across turns."""

    @staticmethod
    def detect(
        history: Dict[str, Any],
        config: Optional[Dict[str, Any]] = None,
        context_length: int = 128_000,
    ) -> Optional[Signal]:
        """Detect abnormal growth patterns.

        Args:
            history: dict with keys:
                - turn_count: int
                - history_tokens: int (total tokens in current history)
                - history_tokens_n_turns_ago: int (total N turns ago, for accel check)
                - last_turn_tokens: int (tokens added in most recent turn)
            config: Override thresholds (soft_max_tokens_per_turn, hard_max_tokens_per_turn,
                    soft_max_pct, hard_max_pct, max_growth_rate, check_window).
            context_length: Model's context window in tokens (used for % thresholds).

        Returns:
            Signal or None. Priority: GROWTH_SPIKE > TURN_COST_WARNING > GROWTH_ACCEL.
        """
        if not history or not history.get("turn_count", 0):
            return None

        cfg = config or {}
        last_turn = history.get("last_turn_tokens", 0)

        # 1. Per-turn cost check (auto-scaled to context window)
        turn_signal = detect_turn_cost(last_turn, context_length, cfg)
        if turn_signal:
            return turn_signal

        # 2. Acceleration check: history growing faster than expected?
        max_rate = cfg.get("max_growth_rate", DEFAULT_MAX_GROWTH_RATE)
        window = cfg.get("check_window", DEFAULT_CHECK_WINDOW)
        turn_count = history.get("turn_count", 0)
        history_tokens = history.get("history_tokens", 0)
        prev_tokens = history.get("history_tokens_n_turns_ago")

        if (
            turn_count >= window
            and prev_tokens is not None
            and prev_tokens > 0
            and history_tokens > 0
        ):
            growth_rate = history_tokens / prev_tokens
            if growth_rate >= max_rate:
                return Signal(
                    code="GROWTH_ACCEL",
                    severity="warn",
                    message=(
                        f"History grew {growth_rate:.1f}× in {window} turns "
                        f"({prev_tokens:,} → {history_tokens:,} tokens). "
                        f"Consider completing the current task."
                    ),
                    count=int(history_tokens),
                )

        return None