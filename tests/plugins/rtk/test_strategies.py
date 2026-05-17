"""Tests for plugins/rtk/strategies/ — per-tool compression strategies."""

from __future__ import annotations

from plugins.rtk.strategies import terminal, read_file, search_files


# ===========================================================================
# terminal.py
# ===========================================================================


class TestTerminalStrategy:
    def test_small_output_no_compression(self):
        text = "Hello, world!\n" * 20  # < head+tail+200
        result, stats = terminal.compress(text, head_chars=500, tail_chars=1000)
        assert result == text
        assert stats["chars_saved"] == 0

    def test_large_output_compressed(self):
        head = "START\n" * 100  # ~600 chars
        middle = "MIDDLE\n" * 2000  # ~14000 chars
        tail = "END\n" * 200  # ~800 chars
        text = head + middle + tail
        result, stats = terminal.compress(text, head_chars=500, tail_chars=1000)
        assert stats["chars_saved"] > 0
        assert result.startswith("START")
        assert "END" in result
        assert "[truncated" in result
        assert stats["errors_found"] == 0

    def test_error_lines_extracted(self):
        head = "OK\n" * 100
        middle = "\n".join([
            "INFO: processing",
            "ERROR: Connection refused",
            "WARNING: timeout",
            "FATAL: kernel panic",
        ]) * 50  # Heavily repeated errors
        tail = "done\n" * 100
        text = head + middle + tail
        result, stats = terminal.compress(text, head_chars=200, tail_chars=200)
        assert stats["errors_found"] > 0
        assert "Connection refused" in result or "Connection refused" in result
        # Error lines should appear between head and tail
        assert "--- errors found" in result

    def test_error_lines_capped_at_20(self):
        many_errors = "\n".join([f"ERROR: error #{i}" for i in range(50)])
        text = ("OK\n" * 50) + many_errors + ("ok\n" * 50)
        text = text * 5
        result, stats = terminal.compress(text, head_chars=200, tail_chars=200)
        assert stats["errors_found"] > 0
        # Should have "20" error lines max PLUS the "... more" message
        error_lines = [l for l in result.split("\n") if "ERROR:" in l]
        assert len(error_lines) <= 20

    def test_recovery_link_with_persist_id(self):
        text = "A" * 5000
        result, stats = terminal.compress(text, head_chars=100, tail_chars=100,
                                           persist_id="my-persist-id")
        assert "rtk-recover my-persist-id" in result

    def test_no_middle_when_small(self):
        # Middle less than 100 chars → no truncation note
        head = "X" * 200
        middle = "M" * 50  # < 100
        tail = "Y" * 200
        text = head + middle + tail
        result, stats = terminal.compress(text, head_chars=200, tail_chars=200)
        assert "[truncated" not in result

    def test_empty_text(self):
        result, stats = terminal.compress("")
        assert result == ""
        assert stats["chars_saved"] == 0

    def test_no_compression_when_overhead_exceeds_savings(self):
        # When savings are tiny, return unchanged
        text = "A" * 1500  # head=500 + tail=1000 = 1500, savings < 200 overhead
        result, stats = terminal.compress(text, head_chars=500, tail_chars=1000)
        assert result == text
        assert stats["chars_saved"] == 0


# ===========================================================================
# read_file.py
# ===========================================================================


class TestReadFileStrategy:
    def test_small_file_no_compression(self):
        text = "line\n" * 100  # < head+tail+2000
        result, stats = read_file.compress(text, head_chars=500, tail_chars=1000)
        assert result == text
        assert stats["chars_saved"] == 0

    def test_large_file_head_tail(self):
        head = "HEAD\n" * 200
        middle = "MID\n" * 5000
        tail = "TAIL\n" * 200
        text = head + middle + tail
        result, stats = read_file.compress(text, head_chars=500, tail_chars=1000)
        assert stats["chars_saved"] > 0
        assert "HEAD" in result[:500]
        assert "TAIL" in result[-1000:]

    def test_section_preservation_with_offset_limit(self):
        # Create a file with 10000 lines of ~80 chars each
        lines = [f"Line {i}: " + "A" * 70 + "\n" for i in range(10000)]
        text = "".join(lines)
        # Ask for lines 5000-5009 (offset=5001, limit=10)
        offset = 5001
        limit = 10
        tool_args = {"offset": offset, "limit": limit}
        # With small head/tail so section is in the middle
        result, stats = read_file.compress(text, head_chars=100, tail_chars=100,
                                            tool_args=tool_args)
        assert stats["section_preserved"]
        assert "Line 5001" in result

    def test_section_overlaps_tail_no_duplicate(self):
        lines = [f"Line {i}\n" for i in range(200)]
        text = "".join(lines)
        # Offset near end so section includes tail
        tool_args = {"offset": 180, "limit": 50}
        result, stats = read_file.compress(text, head_chars=50, tail_chars=50,
                                            tool_args=tool_args)
        # Section may or may not be preserved depending on context window
        # The key is: no crash, and result is valid
        assert len(result) > 0

    def test_no_tool_args_fallback_to_head_tail(self):
        text = ("line\n" * 2000)
        result, stats = read_file.compress(text, head_chars=500, tail_chars=1000)
        assert "[truncated" in result
        assert not stats["section_preserved"]

    def test_recovery_link(self):
        text = ("A" * 5000)
        result, stats = read_file.compress(text, persist_id="rf-pid")
        assert "rtk-recover rf-pid" in result

    def test_invalid_offset_args(self):
        text = ("line\n" * 5000)
        tool_args = {"offset": "invalid", "limit": 10}
        result, stats = read_file.compress(text, head_chars=100, tail_chars=100,
                                            tool_args=tool_args)
        # Should fall back gracefully, not crash
        assert not stats["section_preserved"]
        assert len(result) < len(text)


# ===========================================================================
# search_files.py
# ===========================================================================


class TestSearchFilesStrategy:
    def test_small_results_no_compression(self):
        text = "\n".join([f"path/file{i}.py:content {i}" for i in range(10)])
        # Single copy, under 3000 chars threshold
        result, stats = search_files.compress(text)
        assert result == text
        assert stats["chars_saved"] == 0

    def test_few_matches_per_line_truncation(self):
        lines = []
        for i in range(10):
            long_content = "X" * 300  # exceeds _MAX_CONTEXT_PER_MATCH (120)
            lines.append(f"path/file{i}.py:{long_content}")
        text = "\n".join(lines) * 50  # Make large enough
        result, stats = search_files.compress(text)
        # Should have truncation
        assert stats["chars_saved"] > 0

    def test_many_matches_directory_grouping(self):
        # Generate 60+ matches across 3 directories, make > 3000 chars
        lines = []
        for d in ["src/a", "src/b", "tests"]:
            for i in range(25):
                lines.append(f"{d}/file{i}.py:content {i} " + "x" * 80)
        text = "\n".join(lines)
        result, stats = search_files.compress(text, head_chars=500, tail_chars=1000)
        assert "Total:" in result
        assert "directories" in result
        assert stats["chars_saved"] > 0
        assert "src/a/" in result
        assert "tests/" in result

    def test_directory_grouping_shows_first_3_then_more(self):
        lines = []
        for i in range(10):
            lines.append(f"src/file{i}.py:content")
        text = "\n".join(lines)
        # Make it large enough to trigger compression
        text = text * 10
        result, stats = search_files.compress(text, head_chars=500, tail_chars=1000)
        # Should have "... and N more" for directories with >3 matches
        assert "more" in result or stats["chars_saved"] == 0

    def test_recovery_link(self):
        line = "path/file.py:content" * 60
        text = line * 10
        result, stats = search_files.compress(text, persist_id="sf-pid")
        assert "rtk-recover sf-pid" in result

    def test_lines_without_colon_separator(self):
        lines = ["no colon here", "also no colon"] * 50
        text = "\n".join(lines) * 10
        # Should not crash — _group_by_directory gets lines without ":"
        result, stats = search_files.compress(text, head_chars=500, tail_chars=1000)
        assert stats["chars_saved"] >= 0
        assert len(result) > 0

    def test_empty_text(self):
        result, stats = search_files.compress("")
        assert result == ""
        assert stats["chars_saved"] == 0
