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


class TestDetectTurnCost128K:
    """Tests for detect_turn_cost with 128K context (default)."""

    CONTEXT = 128_000
    # Expected thresholds: soft=25%=32K, hard=50%=64K

    def test_no_tokens_no_signal(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(0, self.CONTEXT) is None

    def test_small_turn_no_signal(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(10_000, self.CONTEXT) is None

    def test_at_soft_limit_no_signal(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(32_000, self.CONTEXT) is None

    def test_above_soft_limit_warning(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(40_000, self.CONTEXT)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"
        assert result.severity == "warn"
        assert "40,000" in result.message

    def test_above_hard_limit_spike(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(80_000, self.CONTEXT)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "warn"

    def test_far_above_hard_limit_critical(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(200_000, self.CONTEXT)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "critical"
        assert result.should_halt is True

    def test_custom_pct_overrides(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        # 100K context, soft_pct=0.5, hard_pct=0.8 → soft=50K, hard=80K
        config = {"soft_max_pct": 0.5, "hard_max_pct": 0.8}
        # 60K: above soft (50K) but below hard (80K) → warning
        result = detect_turn_cost(60_000, 100_000, config)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

        # 90K: above hard (80K) → spike
        result = detect_turn_cost(90_000, 100_000, config)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"

    def test_explicit_token_overrides(self):
        """Explicit soft_max_tokens_per_turn overrides percentages."""
        from plugins.rtk_ck.growth import detect_turn_cost
        config = {"soft_max_tokens_per_turn": 50_000, "hard_max_tokens_per_turn": 100_000}
        # With 100K context, defaults would be soft=25K, hard=50K
        # But explicit overrides: soft=50K, hard=100K
        result = detect_turn_cost(60_000, 100_000, config)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

    def test_negative_tokens_no_signal(self):
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(-1000, self.CONTEXT) is None


class TestDetectTurnCost1M:
    """Tests for detect_turn_cost with 1M context (owl-alpha)."""

    CONTEXT = 1_048_576
    # Expected thresholds: soft=25%=262K, hard=50%=524K

    def test_large_turn_under_soft(self):
        """200K turn under 262K soft → no signal."""
        from plugins.rtk_ck.growth import detect_turn_cost
        assert detect_turn_cost(200_000, self.CONTEXT) is None

    def test_turn_above_soft_warning(self):
        """300K turn above 262K soft → TURN_COST_WARNING."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(300_000, self.CONTEXT)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

    def test_turn_above_hard_spike(self):
        """600K turn above 524K hard → GROWTH_SPIKE."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(600_000, self.CONTEXT)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"

    def test_huge_turn_critical_halt(self):
        """2M turn (4×+ above 524K hard) → critical + halt."""
        from plugins.rtk_ck.growth import detect_turn_cost
        result = detect_turn_cost(2_000_000, self.CONTEXT)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "critical"
        assert result.should_halt is True


class TestResolveThresholds:
    """Tests for _resolve_thresholds helper."""

    def test_default_percentages_128k(self):
        from plugins.rtk_ck.growth import _resolve_thresholds
        soft, hard = _resolve_thresholds(128_000)
        assert soft == 32_000  # 25%
        assert hard == 64_000  # 50%

    def test_default_percentages_1m(self):
        from plugins.rtk_ck.growth import _resolve_thresholds
        soft, hard = _resolve_thresholds(1_048_576)
        assert soft == 262_144  # 25%
        assert hard == 524_288  # 50%

    def test_explicit_pct_overrides(self):
        from plugins.rtk_ck.growth import _resolve_thresholds
        soft, hard = _resolve_thresholds(128_000, {"soft_max_pct": 0.1, "hard_max_pct": 0.3})
        assert soft == 12_800  # 10%
        assert hard == 38_400  # 30%

    def test_explicit_token_overrides(self):
        from plugins.rtk_ck.growth import _resolve_thresholds
        soft, hard = _resolve_thresholds(
            128_000, {"soft_max_tokens_per_turn": 10_000, "hard_max_tokens_per_turn": 20_000}
        )
        assert soft == 10_000
        assert hard == 20_000

    def test_hard_always_greater_than_soft(self):
        from plugins.rtk_ck.growth import _resolve_thresholds
        # Edge case: percentages too close
        soft, hard = _resolve_thresholds(128_000, {"soft_max_pct": 0.4, "hard_max_pct": 0.45})
        assert hard > soft

    def test_clamp_soft_minimum(self):
        """soft_pct clamped to minimum 5%."""
        from plugins.rtk_ck.growth import _resolve_thresholds
        soft, hard = _resolve_thresholds(128_000, {"soft_max_pct": 0.01})
        assert soft >= 6_400  # 5% of 128K


# ---------------------------------------------------------------------------
# GrowthDetector.detect() integration tests
# ---------------------------------------------------------------------------


class TestGrowthDetect:
    """GrowthDetector.detect() returns Signal or None."""

    CONTEXT = 128_000

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

    def _detect(self, history, config=None):
        """Helper: call GrowthDetector.detect with context_length."""
        from plugins.rtk_ck.growth import GrowthDetector
        return GrowthDetector.detect(history, config=config or self._config(), context_length=self.CONTEXT)

    def test_small_growth_no_signal(self):
        """Growth well under threshold → no signal."""
        history = {
            "turn_count": 5,
            "history_tokens": 50_000,
            "last_turn_tokens": 5_000,
        }
        result = self._detect(history)
        assert result is None

    def test_turn_cost_warning(self):
        """Single turn 40K (above 32K soft) → TURN_COST_WARNING."""
        history = {
            "turn_count": 1,
            "history_tokens": 40_000,
            "last_turn_tokens": 40_000,
        }
        result = self._detect(history)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"
        assert result.severity == "warn"

    def test_growth_spike_above_hard_limit(self):
        """Single turn 80K (above 64K hard) → GROWTH_SPIKE."""
        history = {
            "turn_count": 1,
            "history_tokens": 80_000,
            "last_turn_tokens": 80_000,
        }
        result = self._detect(history)

        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "warn"
        assert "80,000" in result.message

    def test_growth_spike_critical_at_3x(self):
        """Single turn 200K (3×+ above 64K hard) → critical + halt."""
        history = {
            "turn_count": 1,
            "history_tokens": 200_000,
            "last_turn_tokens": 200_000,
        }
        result = self._detect(history)

        assert result is not None
        assert result.code == "GROWTH_SPIKE"
        assert result.severity == "critical"
        assert result.should_halt is True

    def test_growth_accel_doubled_in_3_turns(self):
        """History ×2+ in 3 turns → GROWTH_ACCEL (no turn cost)."""
        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "history_tokens_n_turns_ago": 100_000,
            "last_turn_tokens": 20_000,  # under soft limit
        }
        result = self._detect(history)

        assert result is not None
        assert result.code == "GROWTH_ACCEL"
        assert result.severity == "warn"

    def test_growth_accel_boundary_not_reached(self):
        """History ×1.5 in 3 turns → no signal."""
        history = {
            "turn_count": 3,
            "history_tokens": 150_000,
            "history_tokens_n_turns_ago": 100_000,
            "last_turn_tokens": 20_000,
        }
        result = self._detect(history)
        assert result is None

    def test_turn_cost_takes_priority_over_accel(self):
        """When both turn cost and accel would fire → turn cost wins."""
        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "history_tokens_n_turns_ago": 100_000,  # would trigger ACCEL
            "last_turn_tokens": 40_000,  # triggers TURN_COST_WARNING
        }
        result = self._detect(history)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

    def test_no_history_tokens_n_turns_ago_skips_accel(self):
        """Missing n_turns_ago → skip accel check."""
        history = {
            "turn_count": 3,
            "history_tokens": 300_000,
            "last_turn_tokens": 20_000,
        }
        result = self._detect(history)
        assert result is None

    def test_empty_history_no_signal(self):
        """Empty/zero history → no signal."""
        history = {"turn_count": 0, "history_tokens": 0}
        result = self._detect(history)
        assert result is None

    def test_custom_thresholds_via_config(self):
        """Custom soft/hard thresholds via config."""
        config = self._config(soft_max_tokens_per_turn=50_000, hard_max_tokens_per_turn=100_000)

        # 75K: above soft, below hard → warning
        history = {"turn_count": 1, "history_tokens": 75_000, "last_turn_tokens": 75_000}
        result = self._detect(history, config)
        assert result is not None
        assert result.code == "TURN_COST_WARNING"

        # 150K: above hard → spike
        history = {"turn_count": 1, "history_tokens": 150_000, "last_turn_tokens": 150_000}
        result = self._detect(history, config)
        assert result is not None
        assert result.code == "GROWTH_SPIKE"