"""Tests for RTK-CK GrowthDetector — conversation growth rate monitoring.

Detects:
- TURN_COST_WARNING: single turn exceeds soft_max_tokens_per_turn
- GROWTH_SPIKE: single turn exceeds hard_max_tokens_per_turn
- GROWTH_ACCEL: history grew N× in check_window turns

New two-level system (replaced old single max_tokens_per_turn):
- soft_max_tokens_per_turn: 32K default (25% of 128K context)
- hard_max_tokens_per_turn: 64K default (50% of 128K context)
"""
from __future__ import annotations

import pytest

from plugins.rtk.pattern import Signal


# ---------------------------------------------------------------------------
# Tests for detect_turn_cost (new two-level system)
# ---------------------------------------------------------------------------


class TestDetectTurnCost:
    """Tests for the standalone detect_turn_cost function."""

    def test_no_tokens_no_signal(self):
        """Zero tokens → no signal."""
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(0) is None

    def test_small_turn_no_signal(self):
        """Turn under soft limit → no signal."""
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(10_000) is None

    def test_at_soft_limit_no_signal(self):
        """Turn exactly at soft limit → no signal."""
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(32_000) is None

    def test_above_soft_limit_warning(self):
        """Turn above soft limit → TURN_COST_WARNING."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(40_000)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"
        assert result.severity == "warn"
        assert "40,000" in result.message

    def test_above_hard_limit_spike(self):
        """Turn above hard limit → GROWTH_SPIKE."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(80_000)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "warn"
        assert "80,000" in result.message

    def test_far_above_hard_limit_critical(self):
        """Turn 3×+ above hard limit → GROWTH_SPIKE critical + should_halt."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(200_000)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "critical"
        assert result.should_halt is True

    def test_custom_thresholds(self):
        """Custom soft/hard thresholds via config."""
        from plugins.rtk_ck.growth import detect_turn_cost
        config = {"soft_max_tokens_per_turn": 50_000, "hard_max_tokens_per_turn": 100_000}
        # 75K: above soft (50K) but below hard (100K) → warning
        result = detect_turn_cost(75_000, config)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

        # 150K: above hard (100K) → spike
        result = detect_turn_cost(150_000, config)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"

    def test_negative_tokens_no_signal(self):
        """Negative token count → no signal."""
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(-1000) is None


# ---------------------------------------------------------------------------
# GrowthDetector.detect() integration tests
# ---------------------------------------------------------------------------


class TestGrowthDetect:
    """GrowthDetector.detect() returns Signal or None."""

    def _config(self, **overrides):
        """Build config with new keys plus overrides."""
        base = {
            "soft_max_tokens_per_turn": 32_000,
            "hard_max_tokens_per_turn": 64_000,
            "max_growth_rate": 2.0,
            "check_window": 3,
        }
        base.update(overrides)
        return base

    def test_small_growth_no_signal(self):
        """Growth well under threshold → no signal."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 5,
            "history_tokens": 50_000,
            "last_turn_tokens": 5_000,
        }
        result = GrowthDetector.detect(history, config=self._config())
        assert result is None

    def test_turn_cost_warning(self):
        """Single turn 40K (above 32K soft) → TURN_COST_WARNING."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 1,
            "history_tokens": 40_000,
            "last_turn_tokens": 40_000,
        }
        result = GrowthDetector.detect(history, config=self._config())
        assert result is not None
        assert result.code == "TURN_COST_WARNING"
        assert result.severity == "warn"

    def test_growth_spike_above_hard_limit(self):
        """Single turn 80K (above 64K hard) → GROWTH_SPIKE."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 1,
            "history_tokens": 80_000,
            "last_turn_tokens": 80_000,
        }
        result = GrowthDetector.detect(history, config=self._config())

        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "warn"
        assert "80,000" in result.message

    def test_growth_spike_critical_at_3x(self):
        """Single turn 200K (3×+ above 64K hard) → critical + halt."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 1,
            "history_tokens": 200_000,
            "last_turn_tokens": 200_000,
        }
        result = GrowthDetector.detect(history, config=self._config())

        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "critical"
        assert result.should_halt is True

    def test_growth_accel_doubled_in_3_turns(self):
        """History ×2+ in 3 turns → GROWTH_ACCEL (no turn cost)."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "history_tokens_n_turns_ago": 100_000,
            "last_turn_tokens": 20_000,  # under soft limit
        }
        result = GrowthDetector.detect(history, config=self._config())

        assert result is not None
        assert result.code == "GROWTH_ACCEL"
        assert result.severity == "warn"

    def test_growth_accel_boundary_not_reached(self):
        """History ×1.5 in 3 turns → no signal."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 3,
            "history_tokens": 150_000,
            "history_tokens_n_turns_ago": 100_000,
            "last_turn_tokens": 20_000,
        }
        result = GrowthDetector.detect(history, config=self._config())
        assert result is None

    def test_turn_cost_takes_priority_over_accel(self):
        """When both turn cost and accel would fire → turn cost wins."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "history_tokens_n_turns_ago": 100_000,  # would trigger ACCEL
            "last_turn_tokens": 40_000,  # triggers TURN_COST_WARNING
        }
        result = GrowthDetector.detect(history, config=self._config())
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

    def test_no_history_tokens_n_turns_ago_skips_accel(self):
        """Missing n_turns_ago → skip accel check."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "last_turn_tokens": 20_000,
        }
        result = GrowthDetector.detect(history, config=self._config())
        assert result is None

    def test_empty_history_no_signal(self):
        """Empty/zero history → no signal."""
        from plugins.rtk_ck.growth import GrowthDetector

        history = {"turn_count": 0, "history_tokens": 0}
        result = GrowthDetector.detect(history, config=self._config())
        assert result is None

    def test_custom_thresholds_via_config(self):
        """Custom soft/hard thresholds via config."""
        from plugins.rtk_ck.growth import GrowthDetector

        config = self._config(soft_max_tokens_per_turn=50_000, hard_max_tokens_per_turn=100_000)

        # 75K: above soft, below hard → warning
        history = {"turn_count": 1, "history_tokens": 75_000, "last_turn_tokens": 75_000}
        result = GrowthDetector.detect(history, config=config)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

        # 150K: above hard → spike
        history = {"turn_count": 1, "history_tokens": 150_000, "last_turn_tokens": 150_000}
        result = GrowthDetector.detect(history, config=config)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"