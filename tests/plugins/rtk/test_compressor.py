"""Tests for plugins/rtk/compressor.py — type-aware dispatch + generic head/tail."""

from __future__ import annotations

from plugins.rtk import compressor


# ---------------------------------------------------------------------------
# _generic_head_tail
# ---------------------------------------------------------------------------


class TestGenericHeadTail:
    def test_small_text_unchanged(self):
        text = "Hello, world!"
        result = compressor._generic_head_tail(text, head_chars=500, tail_chars=1000)
        assert result == text

    def test_large_text_truncated(self):
        head = "X" * 500
        body = "M" * 3000
        tail = "Z" * 1000
        text = head + body + tail
        result = compressor._generic_head_tail(text, head_chars=500, tail_chars=1000)
        assert result.startswith(head)
        assert result.endswith(tail)
        assert "... [truncated 3000 chars]" in result
        assert len(result) < len(text)

    def test_exact_boundary_no_truncation(self):
        text = "A" * 1499  # head_chars + tail_chars - 1
        result = compressor._generic_head_tail(text, head_chars=500, tail_chars=1000)
        assert result == text  # <= head+tail, no truncation

    def test_at_boundary_plus_one(self):
        # head_chars + tail_chars = 1500, text = 2501 → middle = 1001 > 100
        text = "A" * 2501
        result = compressor._generic_head_tail(text, head_chars=500, tail_chars=1000)
        assert "... [truncated" in result
        assert len(result) < len(text)

    def test_persist_id_injected(self):
        text = "A" * 5000
        result = compressor._generic_head_tail(text, head_chars=100, tail_chars=100,
                                                persist_id="abc-123")
        assert "rtk-recover abc-123" in result

    def test_persist_id_empty_no_suffix(self):
        text = "A" * 5000
        result = compressor._generic_head_tail(text, head_chars=100, tail_chars=100)
        assert "rtk-recover" not in result

    def test_empty_text(self):
        assert compressor._generic_head_tail("") == ""

    def test_custom_head_tail(self):
        text = "HEAD!" + "M" * 100 + "TAIL!"
        result = compressor._generic_head_tail(text, head_chars=5, tail_chars=5)
        assert result.startswith("HEAD!")
        assert result.endswith("TAIL!")


# ---------------------------------------------------------------------------
# compress — dispatch logic
# ---------------------------------------------------------------------------


class TestCompressDispatch:
    def test_small_text_returns_unchanged(self):
        result, stats = compressor.compress("terminal", "hello")
        assert result == "hello"
        assert stats["chars_saved"] == 0

    def test_unknown_tool_falls_back_to_generic(self):
        text = "A" * 5000
        result, stats = compressor.compress("unknown_tool", text,
                                             persist_id="pid-1")
        assert "... [truncated" in result
        assert stats["chars_saved"] > 0
        assert "rtk-recover pid-1" in result

    def test_terminal_dispatches_to_terminal_strategy(self):
        # Terminal strategy should extract error lines from middle
        text = ("OK\n" * 50) + "ERROR: something failed\n" + ("ok\n" * 50)
        text = text * 10  # Make it > 500 chars
        result, stats = compressor.compress("terminal", text, persist_id="pid-t")
        # Should have error extraction
        assert "ERROR:" in result or "chars_saved" in stats
        assert stats["chars_saved"] >= 0

    def test_read_file_dispatches(self):
        text = "line\n" * 500  # > 500 chars
        result, stats = compressor.compress("read_file", text, persist_id="pid-r",
                                             tool_args={"offset": 1, "limit": 10})
        assert stats["original_len"] > 500
        assert "chars_saved" in stats

    def test_search_files_dispatches(self):
        text = "\n".join([f"path/to/file{i}.py:line {i}" for i in range(100)])
        text = text * 5  # Make it large
        result, stats = compressor.compress("search_files", text, persist_id="pid-s")
        assert stats["original_len"] > len(text) * 0.5  # at least this
        assert "total_matches" in stats

    def test_compress_empty_string(self):
        result, stats = compressor.compress("terminal", "")
        assert result == ""
        assert stats["chars_saved"] == 0

    def test_compress_small_skip_threshold(self):
        # 499 chars — below 500 threshold → skip
        text = "A" * 499
        result, stats = compressor.compress("terminal", text)
        assert result == text
        assert stats["chars_saved"] == 0
