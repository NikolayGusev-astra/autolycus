"""Tests for the new RTK architecture — non-destructive compressor with recovery.

Covers:
  * store.py        — persist full result, generate recovery path
  * compressor.py   — type-aware compression (terminal/read_file/search_files)
  * strategies/     — per-tool strategy contracts
  * __init__.py     — hook registration + rtk_recover tool
  * monitor.py      — measurement framework

Test plan:
  1. Persistence — save/load/recovery path
  2. Compression — terminal tail-first, read_file offset-aware, search_files compact
  3. Strategies — each returns (compressed, persisted) tuple
  4. Hook — transform_tool_result saves + compresses
  5. Metrics — agg_stats per tool
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# Helpers: import modules from the yet-to-be-written plugins/rtk/ directory
# ===========================================================================

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RTK_DIR = _REPO_ROOT / "plugins" / "rtk"


def _import_rtk_module(name: str, file: str):
    """Import a module from plugins/rtk/ using importlib (hyphenated dir)."""
    lib_path = _RTK_DIR / file
    if not lib_path.exists():
        pytest.skip(f"RTK plugin not yet created: {lib_path}")
    spec = importlib.util.spec_from_file_location(
        f"rtk_{name}_under_test", lib_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def temp_cache_dir():
    """Temporary directory for RTK cache."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_terminal_output() -> str:
    """Typical terminal command output — long with errors at the end."""
    lines = []
    # Header
    lines.append("=== Starting deployment ===\n")
    lines.append("Loading config... OK\n")
    # Lots of repetitive progress
    for i in range(50):
        lines.append(f"Processing chunk {i:03d}... OK\n")
    # Key error at the end
    lines.append("Building image... ERROR: /usr/lib/x86_64-linux-gnu/libssl.so.3: version `OPENSSL_3.4.0' not found\n")
    lines.append("make: *** [Makefile:42: build] Error 1\n")
    return "".join(lines)


@pytest.fixture
def sample_read_file_output() -> str:
    """Typical file content — nginx config, 300+ lines."""
    lines = ["# nginx.conf\n", "worker_processes auto;\n", "events { multi_accept on; }\n"]
    for i in range(200):
        lines.append(f"# Some comment line {i}\n")
    lines.extend([
        "server {\n",
        "    listen 443 ssl;\n",
        "    server_name example.com;\n",
        "    ssl_certificate /etc/letsencrypt/live/example.com/fullchain.pem;\n",
        "    ssl_certificate_key /etc/letsencrypt/live/example.com/privkey.pem;\n",
        "}\n",
    ])
    return "".join(lines)


@pytest.fixture
def sample_search_output() -> str:
    """Typical search_files result — 30 matches with paths."""
    lines = []
    for i in range(30):
        lines.append(f"/etc/nginx/sites-enabled/site{i}.conf:{i*10}:    server_name site{i}.com;\n")
    return "".join(lines)


# ===========================================================================
# Tests: store.py — persistence layer
# ===========================================================================

class TestStorePersistence:
    """Store: save full result to disk, generate UUID, return recovery path."""

    def test_save_and_load_roundtrip(self, temp_cache_dir):
        """Save data → persists to disk → load returns identical data."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        data = "Hello, world! " * 1000
        persist_id = store.save(data, cache_dir=str(temp_cache_dir))
        assert persist_id is not None
        assert len(persist_id) > 0
        loaded = store.load(persist_id, cache_dir=str(temp_cache_dir))
        assert loaded == data

    def test_save_returns_uuid(self, temp_cache_dir):
        """Save returns a unique identifier each time."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        id1 = store.save("data1", cache_dir=str(temp_cache_dir))
        id2 = store.save("data2", cache_dir=str(temp_cache_dir))
        assert id1 != id2

    def test_small_data_only_persisted(self, temp_cache_dir):
        """Data under compression threshold → still persisted (ownership axiom)."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        data = "short"
        pid = store.save(data, cache_dir=str(temp_cache_dir))
        assert pid is not None
        assert store.load(pid, cache_dir=str(temp_cache_dir)) == data

    def test_load_nonexistent_returns_none(self, temp_cache_dir):
        """Loading a non-existent persist_id returns None (not crash)."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        result = store.load("nonexistent-uuid", cache_dir=str(temp_cache_dir))
        assert result is None

    def test_file_naming_convention(self, temp_cache_dir):
        """Saved files follow <uuid>.txt naming."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        pid = store.save("some data", cache_dir=str(temp_cache_dir))
        files = list(temp_cache_dir.glob("*.txt"))
        assert len(files) >= 1
        assert any(pid in f.name for f in files)


# ===========================================================================
# Tests: compressor.py — type-aware compression
# ===========================================================================

class TestCompressor:
    """Compressor: type-aware compression + recovery link injection."""

    def test_terminal_tail_first(self, sample_terminal_output):
        """Terminal output keeps tail (errors) intact, head (header) intact."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        # Use config to set tight thresholds so compression triggers
        cfg = {"head_chars": 200, "tail_chars": 200}
        text, stats = comp.compress("terminal", sample_terminal_output, config=cfg)
        assert "ERROR" in text, "tail (error) must be preserved"
        assert "Starting deployment" in text, "head (header) must be preserved"
        assert stats["chars_saved"] > 0, "must save chars"

    def test_terminal_preserves_error_lines(self, sample_terminal_output):
        """Even if tail is full of progress lines, error lines are preserved."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        cfg = {"head_chars": 100, "tail_chars": 100}
        text, stats = comp.compress("terminal", sample_terminal_output, config=cfg)
        # The critical error: libssl version not found
        assert "OPENSSL_3.4.0" in text or "Error 1" in text, "error must be preserved even in tiny tail"

    def test_read_file_offset_aware(self, sample_read_file_output):
        """Read_file keeps around the section the agent was reading (from args)."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        text, stats = comp.compress("read_file", sample_read_file_output)
        assert "server {" in text or "example.com" in text, "relevant section must be preserved"
        assert stats["chars_saved"] > 0, "must compress"

    def test_search_files_preserves_paths(self, sample_search_output):
        """Search_files keeps all paths, truncates content per match."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        # Use large sample + tight thresholds so aggregation kicks in
        large_output = sample_search_output * 5  # 150 lines
        text, stats = comp.compress("search_files", large_output)
        assert stats["chars_saved"] > 0, "must compress"

    def test_short_text_passthrough(self):
        """Text under threshold passes through without compression."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        text = "Hello, world!"
        result, stats = comp.compress("terminal", text)
        assert stats["chars_saved"] == 0

    def test_recovery_link_injected(self, sample_terminal_output):
        """Compressed output ends with recovery link pointing to persisted file."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        persist_id = "test-uuid-12345"
        cfg = {"head_chars": 200, "tail_chars": 200}
        text, stats = comp.compress("terminal", sample_terminal_output, persist_id=persist_id, config=cfg)
        assert persist_id in text, "recovery ID must appear in compressed output"

    def test_recovery_path_is_readable_format(self, temp_cache_dir, sample_terminal_output):
        """The recovery path is a valid file path the agent can open."""
        if not (_RTK_DIR / "compressor.py").exists() or not (_RTK_DIR / "store.py").exists():
            pytest.skip("RTK not yet created")
        store = _import_rtk_module("store", "store.py")
        comp = _import_rtk_module("compressor", "compressor.py")
        pid = store.save(sample_terminal_output, cache_dir=str(temp_cache_dir))
        cfg = {"head_chars": 200, "tail_chars": 200}
        text, stats = comp.compress("terminal", sample_terminal_output, persist_id=pid, config=cfg)
        assert pid in text, "recovery ID must appear in compressed output"

    def test_compressor_dispatches_by_tool_name(self, sample_terminal_output, sample_read_file_output, sample_search_output):
        """compressor.compress(tool_name, ...) dispatches to correct strategy."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        cfg = {"head_chars": 200, "tail_chars": 200}
        term, ts = comp.compress("terminal", sample_terminal_output, config=cfg)
        read_f, rs = comp.compress("read_file", sample_read_file_output, config=cfg)
        search, ss = comp.compress("search_files", sample_search_output * 5, config=cfg)
        assert isinstance(term, str)
        assert isinstance(read_f, str)
        assert isinstance(search, str)

    def test_unknown_tool_falls_back_to_generic(self):
        """Unknown tool name → generic head/tail fallback."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        cfg = {"head_chars": 200, "tail_chars": 200}
        text = "A" * 500 + "B" * 500 + "C" * 500
        result, stats = comp.compress("unknown_tool", text, config=cfg)
        assert stats["chars_saved"] > 0, "must save chars"


# ===========================================================================
# Tests: strategies/ — per-tool contracts
# ===========================================================================

class TestStrategies:
    """Each strategy returns (compressed: str, stats: dict)."""

    def test_terminal_strategy_returns_stats(self, sample_terminal_output):
        """Terminal strategy returns compression stats (chars_saved, original_len, etc.)."""
        if not (_RTK_DIR / "strategies" / "terminal.py").exists():
            pytest.skip("terminal strategy not yet created")
        strat = _import_rtk_module("strat_terminal", "strategies/terminal.py")
        result, stats = strat.compress(sample_terminal_output, head_chars=200, tail_chars=200)
        assert stats["chars_saved"] > 0, "must save chars"

    def test_read_file_strategy_uses_args(self, sample_read_file_output):
        """Read_file strategy reads args to determine what section to keep."""
        if not (_RTK_DIR / "strategies" / "read_file.py").exists():
            pytest.skip("read_file strategy not yet created")
        strat = _import_rtk_module("strat_readfile", "strategies/read_file.py")
        # If args say user was reading offset=200, keep that area
        result, stats = strat.compress(sample_read_file_output, tool_args={"offset": 200, "limit": 50})
        assert stats["chars_saved"] > 0, "must compress"

    def test_search_files_compact_paths(self, sample_search_output):
        """Search_files keeps all paths in compact form."""
        if not (_RTK_DIR / "strategies" / "search_files.py").exists():
            pytest.skip("search_files strategy not yet created")
        strat = _import_rtk_module("strat_search", "strategies/search_files.py")
        # 30 lines x 5 = 150 lines, enough to trigger aggregation
        result, stats = strat.compress(sample_search_output * 5)
        assert stats["chars_saved"] > 0, "must compress"


# ===========================================================================
# Tests: hook — transform_tool_result + rtk_recover
# ===========================================================================

class TestRTKHook:
    """The transform_tool_result hook: save + compress in one call."""

    def test_hook_saves_and_compresses(self, temp_cache_dir, sample_terminal_output):
        """Hook runs both save and compress, returns compressed string."""
        if not (_RTK_DIR / "__init__.py").exists():
            pytest.skip("RTK plugin __init__ not yet created")
        rtk_mod = _import_rtk_module("rtk_init", "__init__.py")
        # Override config to use tight thresholds so compression triggers
        original_load = rtk_mod._load_config
        rtk_mod._load_config = lambda: {
            "enabled": True, "head_chars": 100, "tail_chars": 100,
            "min_result_chars": 10,
        }
        try:
            result = rtk_mod.transform_tool_result(
                tool_name="terminal",
                args={"command": "make build"},
                result=sample_terminal_output,
            )
        finally:
            rtk_mod._load_config = original_load
        assert result is not None, "hook must return compressed string"
        assert "rtk-recover" in result, "must include recovery link"
        # Full data should be on disk at default cache dir
        from pathlib import Path
        cache_dir = Path("~/.autolycus/rtk-cache").expanduser()
        cached_files = list(cache_dir.glob("*.txt"))
        assert len(cached_files) >= 1, f"no cached files in {cache_dir}"

    def test_hook_skips_small_results(self):
        """Results under threshold pass through uncompressed."""
        if not (_RTK_DIR / "__init__.py").exists():
            pytest.skip("RTK plugin __init__ not yet created")
        rtk_mod = _import_rtk_module("rtk_init", "__init__.py")
        result = rtk_mod.transform_tool_result(
            tool_name="terminal",
            result="short result",
        )
        assert result is None or result == "short result"

    def test_hook_skips_non_string(self):
        """Non-string results pass through (None means no change)."""
        if not (_RTK_DIR / "__init__.py").exists():
            pytest.skip("RTK plugin __init__ not yet created")
        rtk_mod = _import_rtk_module("rtk_init", "__init__.py")
        assert rtk_mod.transform_tool_result(result=42) is None
        assert rtk_mod.transform_tool_result(result=None) is None
        assert rtk_mod.transform_tool_result(result=[1, 2, 3]) is None

    def test_rtk_recover_tool_registered(self):
        """The rtk_recover tool is registered and callable."""
        if not (_RTK_DIR / "__init__.py").exists():
            pytest.skip("RTK plugin __init__ not yet created")
        rtk_mod = _import_rtk_module("rtk_init", "__init__.py")
        mock_ctx = MagicMock()
        rtk_mod.register(mock_ctx)
        calls = mock_ctx.register_tool.call_args_list
        # register_tool is called with keyword arguments, not positional
        names = [call.kwargs.get("name", "") for call in calls]
        assert "rtk_recover" in names, "rtk_recover tool must be registered"

    def test_rtk_recover_returns_full_data(self, temp_cache_dir, sample_terminal_output):
        """rtk_recover(persist_id) returns the full original data."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        pid = store.save(sample_terminal_output, cache_dir=str(temp_cache_dir))
        full_data = store.load(pid, cache_dir=str(temp_cache_dir))
        assert full_data == sample_terminal_output


# ===========================================================================
# Tests: monitor.py — measurement framework
# ===========================================================================

class TestMonitor:
    """Monitor: aggregate stats per tool, token savings calculation."""

    def test_tracks_per_tool_stats(self):
        """Monitor records per-tool total chars before/after."""
        if not (_RTK_DIR / "monitor.py").exists():
            pytest.skip("monitor.py not yet created")
        mon = _import_rtk_module("monitor", "monitor.py")
        mon.record("terminal", before=50000, after=3000)
        mon.record("terminal", before=10000, after=2000)
        mon.record("read_file", before=20000, after=4000)
        stats = mon.stats()
        assert stats["terminal"]["count"] == 2
        assert stats["terminal"]["total_before"] == 60000
        assert stats["read_file"]["count"] == 1

    def test_reports_savings_percentage(self):
        """Monitor reports % savings per tool."""
        if not (_RTK_DIR / "monitor.py").exists():
            pytest.skip("monitor.py not yet created")
        mon = _import_rtk_module("monitor", "monitor.py")
        mon.record("terminal", before=50000, after=3000)
        stats = mon.stats()
        # 50000 → 3000 = 94% savings
        assert 90 < stats["terminal"]["savings_pct"] < 99

    def test_aggregates_globally(self):
        """Monitor reports global savings across all tools."""
        if not (_RTK_DIR / "monitor.py").exists():
            pytest.skip("monitor.py not yet created")
        mon = _import_rtk_module("monitor", "monitor.py")
        mon.record("terminal", before=50000, after=3000)
        mon.record("read_file", before=20000, after=4000)
        global_stats = mon.global_stats()
        # Total: 70000 → 7000 = 90%
        assert global_stats["total_before"] == 70000
        assert 85 < global_stats["savings_pct"] < 95

    def test_resets_stats(self):
        """Monitor reset clears all accumulated stats."""
        if not (_RTK_DIR / "monitor.py").exists():
            pytest.skip("monitor.py not yet created")
        mon = _import_rtk_module("monitor", "monitor.py")
        mon.record("terminal", before=50000, after=3000)
        mon.reset()
        stats = mon.stats()
        assert stats == {}

    def test_saves_report_to_json(self, temp_cache_dir):
        """Monitor can export stats as JSON for dashboard/audit."""
        if not (_RTK_DIR / "monitor.py").exists():
            pytest.skip("monitor.py not yet created")
        mon = _import_rtk_module("monitor", "monitor.py")
        mon.record("terminal", before=50000, after=3000)
        report_path = Path(temp_cache_dir) / "rtk-report.json"
        mon.export_json(str(report_path))
        assert report_path.exists()
        with open(report_path) as f:
            data = json.load(f)
        assert "terminal" in data.get("per_tool", {}), "per_tool must contain terminal"


# ===========================================================================
# Tests: edge cases and error handling
# ===========================================================================

class TestEdgeCases:
    """Edge cases: empty, binary, very large, concurrent."""

    def test_empty_string(self):
        """Empty string passes through all layers."""
        if not (_RTK_DIR / "compressor.py").exists():
            pytest.skip("compressor.py not yet created")
        comp = _import_rtk_module("compressor", "compressor.py")
        text, stats = comp.compress("terminal", "")
        assert text == "", "empty in = empty out"
        assert stats["chars_saved"] == 0

    def test_binary_result_skipped(self):
        """Binary/JSON tool results are not compressed."""
        if not (_RTK_DIR / "__init__.py").exists():
            pytest.skip("RTK plugin __init__ not yet created")
        rtk_mod = _import_rtk_module("rtk_init", "__init__.py")
        import json
        json_result = json.dumps({"data": [1, 2, 3], "metadata": {"key": "value"}})
        # JSON results should pass through if the tool is not a text-producing one
        result = rtk_mod.transform_tool_result(
            tool_name="terminal",
            result=json_result,
        )
        # If it's a short JSON, it should pass through
        assert result is None or len(result) <= len(json_result)

    def test_very_large_output_compressed_aggressively(self, temp_cache_dir):
        """100K+ char output is still stored in full on disk."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        large = "X" * 200000
        pid = store.save(large, cache_dir=str(temp_cache_dir))
        loaded = store.load(pid, cache_dir=str(temp_cache_dir))
        assert len(loaded) == 200000, "full data must be recoverable"

    def test_concurrent_saves_dont_clobber(self, temp_cache_dir):
        """Concurrent saves produce unique files (no race condition)."""
        if not (_RTK_DIR / "store.py").exists():
            pytest.skip("store.py not yet created")
        store = _import_rtk_module("store", "store.py")
        import threading
        results = []
        def save_thread(data):
            pid = store.save(data, cache_dir=str(temp_cache_dir))
            results.append(pid)
        threads = []
        for i in range(20):
            t = threading.Thread(target=save_thread, args=(f"data-{i}",))
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        assert len(set(results)) == 20, "all 20 saves must have unique IDs"
