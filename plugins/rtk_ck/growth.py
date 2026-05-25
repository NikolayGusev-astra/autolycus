"""RTK-CK GrowthDetector — conversation growth rate monitoring.

Pure functions. No I/O. Returns Signal or None.

Detects:
- TURN_COST_WARNING: single turn exceeds soft_max (advise to reduce)
- GROWTH_SPIKE: single turn exceeds hard_max (warn strongly)
- GROWTH_ACCEL: history grew N× in check_window turns

Thresholds tuned for 128K context. For 1M context, raise via config:
  plugins:
    rtk_ck:
      soft_max_tokens_per_turn: 250000
      hard_max_tokens_per_turn: 500000
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from plugins.rtk.pattern import Signal

# Defaults — tuned for 128K context (aggressive monitoring)
DEFAULT_SOFT_MAX_TOKENS_PER_TURN = 32_000   # 25% of 128K — warning
DEFAULT_HARD_MAX_TOKENS_PER_TURN = 64_000   # 50% of 128K — spike
DEFAULT_MAX_GROWTH_RATE = 2.0
DEFAULT_CHECK_WINDOW = 3


def detect_turn_cost(last_turn_tokens: int, config: Optional[Dict[str, Any]] = None) -> Optional[Signal]:
    """Warn when a single turn adds too many tokens.

    Two levels:
    - TURN_COST_WARNING: soft limit exceeded → advise reducing reads
    - GROWTH_SPIKE: hard limit exceeded → strong warning about context overflow
    """
    if last_turn_tokens <= 0:
        return None

    cfg = config or {}
    soft_max = cfg.get("soft_max_tokens_per_turn", DEFAULT_SOFT_MAX_TOKENS_PER_TURN)
    hard_max = cfg.get("hard_max_tokens_per_turn", DEFAULT_HARD_MAX_TOKENS_PER_TURN)

    # Hard limit — danger zone, context overflow risk
    if last_turn_tokens > hard_max:
        ratio = last_turn_tokens / hard_max
        return Signal(
            code="GROWTH_SPIKE",
            severity="critical" if ratio > 2.0 else "warn",
            message=(
                f"Turn added {last_turn_tokens:,} tokens "
                f"({ratio:.1f}× above {hard_max:,} limit). "
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
    ) -> Optional[Signal]:
        """Detect abnormal growth patterns.

        Args:
            history: dict with keys:
                - turn_count: int
                - history_tokens: int (total tokens in current history)
                - history_tokens_n_turns_ago: int (total N turns ago, for accel check)
                - last_turn_tokens: int (tokens added in most recent turn)
            config: Override thresholds (soft_max_tokens_per_turn, hard_max_tokens_per_turn,
                    max_growth_rate, check_window).

        Returns:
            Signal or None. Priority: GROWTH_SPIKE > TURN_COST_WARNING > GROWTH_ACCEL.
        """
        if not history or not history.get("turn_count", 0):
            return None

        cfg = config or {}
        last_turn = history.get("last_turn_tokens", 0)

        # 1. Per-turn cost check (new two-level system)
        turn_signal = detect_turn_cost(last_turn, cfg)
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