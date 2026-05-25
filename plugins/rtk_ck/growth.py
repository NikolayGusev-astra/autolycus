"""RTK-CK GrowthDetector — conversation growth rate monitoring.

Pure functions. No I/O. Returns Signal or None.

Detects:
- GROWTH_SPIKE: single turn exceeds max_tokens_per_turn
- GROWTH_ACCEL: history doubled in N turns (check_window)
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from plugins.rtk.pattern import Signal

# Defaults
DEFAULT_MAX_TOKENS_PER_TURN = 150_000
DEFAULT_MAX_GROWTH_RATE = 2.0
DEFAULT_CHECK_WINDOW = 3


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
            config: Override thresholds.

        Returns:
            Signal or None.
        """
        if not history or not history.get("turn_count", 0):
            return None

        cfg = config or {}
        max_per_turn = cfg.get("max_tokens_per_turn", DEFAULT_MAX_TOKENS_PER_TURN)
        max_rate = cfg.get("max_growth_rate", DEFAULT_MAX_GROWTH_RATE)
        window = cfg.get("check_window", DEFAULT_CHECK_WINDOW)

        # Check per-turn spike
        last_turn = history.get("last_turn_tokens", 0)
        if last_turn > max_per_turn and last_turn > 0:
            ratio = last_turn / max_per_turn
            return Signal(
                code="GROWTH_SPIKE",
                severity="warn",
                message=(
                    f"Single turn added {last_turn:,} tokens "
                    f"({ratio:.1f}× above {max_per_turn:,} limit). "
                    f"Large reads may cause context overflow."
                ),
                count=last_turn,
            )

        # Check acceleration: is history growing faster than expected?
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