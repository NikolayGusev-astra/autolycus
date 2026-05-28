"""Tests for RTK-CK comparative metrics — before/after comparison.

Verifies that RTK-CK metrics show meaningful savings compared to baseline.
"""
from __future__ import annotations

import pytest


class TestComparativeMetrics:
    """Before/after comparison metrics."""

    def test_baseline_no_rtk_ck(self):
        """Without RTK-CK: 0 tokens saved, 0 compressions."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        m = mc.get_metrics()
        assert m["total_tokens_saved"] == 0
        assert m["compression_count"] == 0
        assert m["cost_saved_usd"] == 0.0

    def test_with_rtk_ck_compression(self):
        """With RTK-CK compression: tokens saved > 0."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        # Simulate 3 compression events
        mc.record_compression(100_000, 20_000)  # 80K saved
        mc.record_compression(200_000, 50_000)  # 150K saved
        mc.record_compression(150_000, 30_000)  # 120K saved

        m = mc.get_metrics()
        assert m["total_tokens_saved"] == 350_000
        assert m["compression_count"] == 3

    def test_cost_savings_calculation(self):
        """Cost savings calculated correctly."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.set_cost_per_million_tokens(3.0)  # $3 per 1M tokens
        mc.record_compression(1_000_000, 200_000)  # 800K saved

        m = mc.get_metrics()
        # 800K tokens = 0.8M * $3 = $2.40
        assert m["cost_saved_usd"] == 2.4

    def test_before_after_format(self):
        """format_stat_line shows before/after comparison."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_stat_line

        mc = MetricsCollector()
        mc.record_compression(1_000_000, 200_000)
        mc.record_signal("BUDGET_WARN")
        mc.record_pattern("REDUNDANT_READS")

        line = format_stat_line(mc.get_metrics())
        assert "800K" in line or "saved" in line.lower()
        assert "2" in line  # 2 signals

    def test_full_pipeline_metrics(self):
        """Full pipeline: compression + signals + dedup + pointers."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_stat_line

        mc = MetricsCollector()
        mc.set_cost_per_million_tokens(3.0)

        # Compression
        mc.record_compression(500_000, 100_000)  # 400K saved
        mc.record_compression(300_000, 60_000)   # 240K saved

        # Signals
        mc.record_signal("BUDGET_WARN")
        mc.record_signal("GROWTH_SPIKE")
        mc.record_pattern("REDUNDANT_READS")

        # Dedup
        mc.record_dedup(15_000)  # 15K chars

        # Pointers
        mc.record_pointer_compression()
        mc.record_pointer_compression()

        m = mc.get_metrics()
        assert m["total_tokens_saved"] == 640_000
        assert m["compression_count"] == 2
        assert m["budget_signals"] == 1
        assert m["growth_signals"] == 1
        assert m["pattern_signals"] == 1
        assert m["dedup_chars_saved"] == 15_000
        assert m["pointer_compressions"] == 2
        assert m["cost_saved_usd"] == 1.92  # 640K * $3/M

        line = format_stat_line(m)
        assert "640K" in line
        assert "2" in line  # signals count
        assert "$1.92" in line

    def test_reset_clears_all(self):
        """Reset clears all metrics."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_compression(100_000, 20_000)
        mc.record_signal("BUDGET_WARN")
        mc.record_dedup(5_000)

        mc.reset()
        m = mc.get_metrics()
        assert m["total_tokens_saved"] == 0
        assert m["compression_count"] == 0
        assert m["budget_signals"] == 0
        assert m["dedup_chars_saved"] == 0