"""Tests for plugins/rtk/monitor.py — compression metrics."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from plugins.rtk import monitor


# ---------------------------------------------------------------------------
# record / stats
# ---------------------------------------------------------------------------


class TestRecordAndStats:
    def setup_method(self):
        monitor.reset()

    def test_record_updates_per_tool(self):
        monitor.record("terminal", before=1000, after=200)
        stats = monitor.stats()
        assert "terminal" in stats
        assert stats["terminal"]["count"] == 1
        assert stats["terminal"]["total_before"] == 1000
        assert stats["terminal"]["total_after"] == 200

    def test_multiple_calls_aggregated(self):
        monitor.record("terminal", before=1000, after=200)
        monitor.record("terminal", before=2000, after=300)
        stats = monitor.stats()
        assert stats["terminal"]["count"] == 2
        assert stats["terminal"]["total_before"] == 3000
        assert stats["terminal"]["total_after"] == 500

    def test_multiple_tools_separate(self):
        monitor.record("terminal", before=1000, after=200)
        monitor.record("read_file", before=500, after=500)
        stats = monitor.stats()
        assert set(stats.keys()) == {"terminal", "read_file"}

    def test_savings_pct_correct(self):
        monitor.record("terminal", before=1000, after=100)
        stats = monitor.stats()
        assert stats["terminal"]["savings_pct"] == 90.0

    def test_savings_pct_rounding(self):
        monitor.record("terminal", before=1000, after=333)
        stats = monitor.stats()
        assert stats["terminal"]["savings_pct"] == 66.7  # (1000-333)/1000 * 100 = 66.7

    def test_zero_before_no_crash(self):
        monitor.record("terminal", before=0, after=0)
        stats = monitor.stats()
        assert stats["terminal"]["savings_pct"] == 0.0

    def test_empty_stats(self):
        assert monitor.stats() == {}


# ---------------------------------------------------------------------------
# global_stats
# ---------------------------------------------------------------------------


class TestGlobalStats:
    def setup_method(self):
        monitor.reset()

    def test_aggregates_all_tools(self):
        monitor.record("terminal", before=1000, after=200)
        monitor.record("read_file", before=500, after=500)
        gs = monitor.global_stats()
        assert gs["total_before"] == 1500
        assert gs["total_after"] == 700
        assert gs["tools"] == 2
        assert gs["total_calls"] == 2

    def test_savings_pct(self):
        monitor.record("terminal", before=8000, after=2000)
        gs = monitor.global_stats()
        assert gs["savings_pct"] == 75.0

    def test_zero_before_global(self):
        gs = monitor.global_stats()
        assert gs["savings_pct"] == 0.0


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    def test_clears_all(self):
        monitor.record("terminal", before=100, after=50)
        monitor.reset()
        assert monitor.stats() == {}

    def test_after_reset_empty_global(self):
        monitor.record("terminal", before=100, after=50)
        monitor.reset()
        gs = monitor.global_stats()
        assert gs["total_before"] == 0


# ---------------------------------------------------------------------------
# export_json
# ---------------------------------------------------------------------------


class TestExportJson:
    def setup_method(self):
        monitor.reset()

    def test_exported_content(self):
        monitor.record("terminal", before=1000, after=100)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            monitor.export_json(path)
            with open(path) as f:
                data = json.load(f)
            assert "global" in data
            assert "per_tool" in data
            assert data["per_tool"]["terminal"]["savings_pct"] == 90.0
        finally:
            os.unlink(path)

    def test_export_empty(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            monitor.export_json(path)
            with open(path) as f:
                data = json.load(f)
            assert data["per_tool"] == {}
            assert data["global"]["total_calls"] == 0
        finally:
            os.unlink(path)


# ===========================================================================
# Thread safety (smoke test)
# ===========================================================================


class TestThreadSafety:
    def setup_method(self):
        monitor.reset()

    def test_concurrent_records(self):
        import threading

        def record_tool(tool: str, count: int):
            for _ in range(count):
                monitor.record(tool, before=1000, after=100)

        threads = [
            threading.Thread(target=record_tool, args=("terminal", 50)),
            threading.Thread(target=record_tool, args=("read_file", 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stats = monitor.stats()
        assert stats["terminal"]["count"] == 50
        assert stats["read_file"]["count"] == 50
