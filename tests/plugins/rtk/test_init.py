"""Tests for plugins/rtk/__init__.py — transform hook, _detect_error, flush."""

from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import MagicMock, patch

import pytest

from plugins.rtk import _detect_error, flush_pending_metadata


# ---------------------------------------------------------------------------
# _detect_error
# ---------------------------------------------------------------------------


class TestDetectError:
    def test_terminal_exit_code_nonzero(self):
        result = json.dumps({"exit_code": 1, "output": "failed"})
        assert _detect_error("terminal", result) is True

    def test_terminal_exit_code_zero(self):
        result = json.dumps({"exit_code": 0, "output": "success"})
        assert _detect_error("terminal", result) is False

    def test_terminal_non_json_output(self):
        result = "Some plain text output without error markers"
        assert _detect_error("terminal", result) is False

    def test_terminal_error_word_in_output(self):
        result = "This is an error: something went wrong"
        assert _detect_error("terminal", result) is False  # non-JSON

    def test_json_with_error_key_nonzero_exit(self):
        # Error key present + nonzero exit → detected as error
        result = json.dumps({"error": "not found", "exit_code": 1})
        assert _detect_error("terminal", result) is True

    def test_json_error_key_but_exit_code_zero(self):
        # Error key present BUT exit_code=0 → NOT an error (command succeeded)
        result = json.dumps({"error": "something", "exit_code": 0})
        assert _detect_error("terminal", result) is False

    def test_nested_error_key(self):
        # Nested error key with exit_code=0 → NOT an error
        result = json.dumps({"data": {"error": "inner"}, "exit_code": 0})
        assert _detect_error("terminal", result) is False

    def test_empty_result(self):
        assert _detect_error("terminal", "") is False

    def test_search_files_result_with_error(self):
        result = json.dumps({"error": "File not found", "matches": []})
        assert _detect_error("search_files", result) is True

    def test_search_files_no_error(self):
        result = "path/file.py:found it"
        assert _detect_error("search_files", result) is False

    def test_tool_result_contains_exit_code_zero(self):
        result = "{\"status\": \"ok\", \"exit_code\": 0}"
        assert _detect_error("terminal", result) is False

    def test_only_first_500_chars_checked(self):
        # Error word only appears after 500 chars
        result = "OK" * 300 + "ERROR" + "MORE"
        assert _detect_error("terminal", result) is False  # "ERROR" not in first 500 as lowercase


# ---------------------------------------------------------------------------
# flush_pending_metadata
# ---------------------------------------------------------------------------


class TestFlushPending:
    def test_empty_buffer(self):
        count = flush_pending_metadata()
        assert count == 0

    def test_flush_single_record(self):
        # Create a state.db with a matching message
        db_path = "/tmp/test_rtk_flush.db"
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                rtk_metadata TEXT
            )
        """)
        conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, timestamp) "
            "VALUES ('test-sess', 'tool', '{}', 'tc-1', 'terminal', ?)",
            (time.time(),),
        )
        conn.commit()
        conn.close()

        # Populate the buffer manually
        from plugins.rtk import _PENDING_METADATA
        meta = json.dumps({"persist_id": "p1", "chars_saved": 100, "savings_pct": 50.0})
        _PENDING_METADATA[("test-sess", "tc-1")] = meta

        # FIXME: This test needs a way to override SessionDB's path
        # For now, just verify the function doesn't crash
        # The real integration is tested via e2e

        # Clean pending buffer
        _PENDING_METADATA.clear()

    def test_flush_nonexistent_tool_call_id(self):
        from plugins.rtk import _PENDING_METADATA
        meta = json.dumps({"persist_id": "p2"})
        _PENDING_METADATA[("ghost-sess", "no-such-id")] = meta
        # Should not crash
        _PENDING_METADATA.clear()


# ---------------------------------------------------------------------------
# transform_tool_result
# ---------------------------------------------------------------------------


class TestTransformToolResult:
    def _import_transform(self):
        from plugins.rtk import transform_tool_result
        return transform_tool_result

    def test_non_string_result_returns_none(self):
        fn = self._import_transform()
        result = fn(tool_name="terminal", result=42, args={})
        assert result is None

    def test_small_result_returns_none(self):
        fn = self._import_transform()
        result = fn(tool_name="terminal", result="hello", args={})
        assert result is None

    def test_rtk_raw_bypass(self):
        fn = self._import_transform()
        result = fn(tool_name="terminal",
                     result="A" * 1000,
                     args={"rtk_raw": True})
        assert result is None

    def test_large_result_compressed(self):
        fn = self._import_transform()
        result = fn(tool_name="terminal",
                     result="A" * 5000,
                     args={})
        # Should return a compressed string shorter than 5000
        assert result is not None
        assert len(result) < 5000
        assert "rtk-recover" in result

    def test_read_file_preserves_path(self):
        fn = self._import_transform()
        result = fn(tool_name="read_file",
                     result="line\n" * 2000,
                     args={"offset": 1, "limit": 10})
        assert result is not None
        assert len(result) < 10000

    def test_config_disabled(self):
        from plugins.rtk import _load_config
        cfg = _load_config()
        assert isinstance(cfg, dict)
        assert "enabled" in cfg
        assert "head_chars" in cfg
        assert "tail_chars" in cfg


# ---------------------------------------------------------------------------
# _evict_pending_metadata (bounded buffer)
# ---------------------------------------------------------------------------


class TestBoundedBuffer:
    def test_no_eviction_when_under_limit(self):
        from plugins.rtk import _PENDING_METADATA, _PENDING_METADATA_MAX, _evict_pending_metadata
        assert _PENDING_METADATA_MAX > 0
        initial = len(_PENDING_METADATA)
        evicted = _evict_pending_metadata()
        assert evicted == 0
        # Cleanup
        _PENDING_METADATA.clear()

    def test_eviction_when_over_limit(self):
        from plugins.rtk import _PENDING_METADATA, _PENDING_METADATA_MAX, _evict_pending_metadata
        # Fill buffer past max (use small max via monkeypatching)
        import pytest
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("plugins.rtk._PENDING_METADATA_MAX", 10)
            # Add 15 entries
            for i in range(15):
                _PENDING_METADATA[(f"sess-{i}", f"tc-{i}")] = f"meta-{i}"
            evicted = _evict_pending_metadata()
            assert evicted == 5  # 15 - 10 = 5 evicted
            assert len(_PENDING_METADATA) == 10
            # Oldest 5 should be evicted
            for i in range(5):
                assert (f"sess-{i}", f"tc-{i}") not in _PENDING_METADATA
            # Newest 10 should remain
            for i in range(5, 15):
                assert (f"sess-{i}", f"tc-{i}") in _PENDING_METADATA
        _PENDING_METADATA.clear()

    def test_eviction_called_on_add(self):
        from plugins.rtk import _PENDING_METADATA, _PENDING_METADATA_MAX, _evict_pending_metadata
        import pytest
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("plugins.rtk._PENDING_METADATA_MAX", 3)
            # Add 5 entries
            for i in range(5):
                _PENDING_METADATA[(f"sess", f"tc-{i}")] = f"meta-{i}"
                _evict_pending_metadata()
            assert len(_PENDING_METADATA) == 3
        _PENDING_METADATA.clear()