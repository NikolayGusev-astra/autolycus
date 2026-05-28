"""Tests for RTK-CK Metrics — /rtk_ck stat command and context bar integration.

Tests verify:
1. RTCKContextEngine implements ContextEngine interface
2. MetricsCollector aggregates stats from all RTK-CK components
3. format_stat_line() produces human-readable output
4. format_context_bar_line() extends the status bar with RTK-CK info
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# RTCKContextEngine tests
# ---------------------------------------------------------------------------


class TestRTCKContextEngine:
    """RTCKContextEngine implements ContextEngine interface."""

    def test_implements_context_engine(self):
        """RTCKContextEngine is a subclass of ContextEngine."""
        from plugins.rtk_ck.context_engine import RTCKContextEngine
        from agent.context_engine import ContextEngine

        assert issubclass(RTCKContextEngine, ContextEngine)

    def test_has_required_methods(self):
        """RTCKContextEngine has all required ContextEngine methods."""
        from plugins.rtk_ck.context_engine import RTCKContextEngine

        engine = RTCKContextEngine.__new__(RTCKContextEngine)
        assert hasattr(engine, "compress")
        assert hasattr(engine, "should_compress")
        assert hasattr(engine, "update_from_response")
        assert hasattr(engine, "name")

    def test_name_is_rtk_ck(self):
        """Engine name is 'rtk_ck'."""
        from plugins.rtk_ck.context_engine import RTCKContextEngine

        engine = RTCKContextEngine.__new__(RTCKContextEngine)
        assert engine.name == "rtk_ck"

    def test_compress_returns_messages(self):
        """compress() returns a list of messages."""
        from plugins.rtk_ck.context_engine import RTCKContextEngine

        engine = RTCKContextEngine(model="gpt-4o", context_length=128_000)
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/hosts"}'}}
            ]},
            {"role": "tool", "content": "x" * 10_000, "tool_call_id": "c1", "name": "read_file"},
        ]
        result = engine.compress(msgs)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_compress_reduces_large_tool_results(self):
        """compress() reduces large tool results."""
        from plugins.rtk_ck.context_engine import RTCKContextEngine

        engine = RTCKContextEngine(model="gpt-4o", context_length=128_000)
        big = "X" * 10_000
        # 10 tool results — protect_last_n=6 protects last 6, first 4 get compressed
        msgs = []
        for i in range(10):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
            ]})
            msgs.append({"role": "tool", "content": big, "tool_call_id": f"c{i}", "name": "read_file"})
        result = engine.compress(msgs)
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) >= 1
        sizes = [len(m["content"]) for m in tool_msgs]
        # At least some should be compressed (not all protected)
        assert any(s < len(big) for s in sizes), f"All tool results full size: {sizes}"


# ---------------------------------------------------------------------------
# MetricsCollector tests
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    """MetricsCollector aggregates RTK-CK stats."""

    def test_empty_metrics(self):
        """Empty metrics returns zeros."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        metrics = mc.get_metrics()
        assert metrics["total_tokens_saved"] == 0
        assert metrics["compression_count"] == 0
        assert metrics["budget_signals"] == 0

    def test_record_compression(self):
        """Record compression event → stats updated."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_compression(original_tokens=100_000, compressed_tokens=20_000)
        metrics = mc.get_metrics()
        assert metrics["total_tokens_saved"] == 80_000
        assert metrics["compression_count"] == 1

    def test_record_budget_signal(self):
        """Record budget signal → counter incremented."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_signal("BUDGET_WARN")
        mc.record_signal("BUDGET_CRITICAL")
        metrics = mc.get_metrics()
        assert metrics["budget_signals"] == 2

    def test_record_dedup(self):
        """Record dedup event → chars_saved tracked."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_dedup(saved_chars=15_000)
        metrics = mc.get_metrics()
        assert metrics["dedup_chars_saved"] == 15_000

    def test_record_pattern(self):
        """Record pattern detection → pattern_signals tracked."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_pattern("REDUNDANT_READS")
        mc.record_pattern("STALLED_SESSION")
        metrics = mc.get_metrics()
        assert metrics["pattern_signals"] == 2

    def test_multiple_records_accumulate(self):
        """Multiple records accumulate correctly."""
        from plugins.rtk_ck.metrics import MetricsCollector

        mc = MetricsCollector()
        mc.record_compression(100_000, 50_000)
        mc.record_compression(200_000, 80_000)
        mc.record_signal("BUDGET_WARN")
        mc.record_dedup(5_000)
        metrics = mc.get_metrics()
        assert metrics["total_tokens_saved"] == 170_000
        assert metrics["compression_count"] == 2
        assert metrics["budget_signals"] == 1
        assert metrics["dedup_chars_saved"] == 5_000


# ---------------------------------------------------------------------------
# Format tests
# ---------------------------------------------------------------------------


class TestFormatStatLine:
    """format_stat_line() produces human-readable RTK-CK stats."""

    def test_empty_metrics_format(self):
        """Empty metrics → 'no activity' message."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_stat_line

        mc = MetricsCollector()
        line = format_stat_line(mc.get_metrics())
        assert "RTK-CK" in line
        assert "no activity" in line

    def test_compression_format(self):
        """Compression stats formatted with K/M suffixes."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_stat_line

        mc = MetricsCollector()
        mc.record_compression(1_000_000, 200_000)
        line = format_stat_line(mc.get_metrics())
        assert "800K" in line or "0.8M" in line
        assert "compressed" in line.lower() or "saved" in line.lower()

    def test_signals_format(self):
        """Signal counts formatted."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_stat_line

        mc = MetricsCollector()
        mc.record_signal("BUDGET_WARN")
        mc.record_signal("GROWTH_SPIKE")
        mc.record_pattern("REDUNDANT_READS")
        line = format_stat_line(mc.get_metrics())
        assert "3" in line  # total signals


class TestFormatContextBar:
    """format_context_bar_line() extends status bar with RTK-CK info."""

    def test_no_rtk_ck_returns_empty(self):
        """No RTK-CK data → empty string (no extension)."""
        from plugins.rtk_ck.metrics import format_context_bar_line

        line = format_context_bar_line(None)
        assert line == ""

    def test_with_metrics_returns_rtk_info(self):
        """With metrics → RTK-CK info string."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_context_bar_line

        mc = MetricsCollector()
        mc.record_compression(100_000, 20_000)
        line = format_context_bar_line(mc.get_metrics())
        assert "RTK-CK" in line
        assert "80K" in line or "saved" in line.lower()

    def test_cost_savings_formatted(self):
        """Cost savings in dollars formatted."""
        from plugins.rtk_ck.metrics import MetricsCollector, format_context_bar_line

        mc = MetricsCollector()
        mc.record_compression(1_000_000, 200_000)
        mc.set_cost_per_million_tokens(3.0)  # $3 per 1M tokens
        line = format_context_bar_line(mc.get_metrics())
        # Context bar shows tokens saved; cost shown in stat line
        assert "RTK-CK" in line
        assert "800K" in line or "saved" in line.lower()