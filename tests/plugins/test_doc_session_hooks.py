"""
Tests for doc_session plugin — Layer 2: Hook tests (runtime guard).

Run: pytest tests/plugins/test_doc_session_hooks.py -v
"""

from __future__ import annotations

import json

import pytest

# Import hook functions directly
from plugins.doc_session import _on_pre_tool_call, _on_transform_tool_result


# ── pre_tool_call hook tests (Level 2) ─────────────────────────────────────


class TestPreToolCallBlock:
    """Level 2: write_file with large content must be blocked."""

    def test_block_large_write_file(self):
        """write_file with 20K content → blocked."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/report.md", "content": "A" * 20000},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "file_doc_create" in result.get("message", "")

    def test_allow_small_write_file(self):
        """write_file with 5K content → allowed."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/config.yaml", "content": "B" * 5000},
        )
        assert result is None  # not blocked

    def test_allow_non_doc_extension(self):
        """write_file with 20K .py → blocked now (universal 12K threshold)."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/script.py", "content": "C" * 20000},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "execute_code" in result.get("message", "")

    def test_allow_small_py(self):
        """write_file with 5K .py → allowed (under 12K)."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/script.py", "content": "C" * 5000},
        )
        assert result is None

    def test_block_at_exact_threshold(self):
        """12001 chars → blocked, 11999 → allowed."""
        # Just above threshold
        blocked = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/doc.md", "content": "D" * 12001},
        )
        assert blocked is not None
        assert blocked.get("action") == "block"

        # Just below threshold
        allowed = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/doc.md", "content": "E" * 11999},
        )
        assert allowed is None

    def test_block_large_html_diagram(self):
        """write_file with 25KB .html → blocked (universal threshold)."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/diagram.html", "content": "X" * 25000},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "execute_code" in result.get("message", "")

    def test_block_message_contains_doc_create(self):
        """Block message must mention file_doc_create."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/report.md", "content": "F" * 16000},
        )
        assert result is not None
        msg = result.get("message", "")
        assert "file_doc_create" in msg
        assert "file_doc_write" in msg
        assert "file_doc_finalize" in msg

    def test_empty_content_not_blocked(self):
        """write_file with empty content → allowed."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/doc.md", "content": ""},
        )
        assert result is None

    def test_other_tools_not_affected(self):
        """read_file, patch, search_files must not be blocked."""
        for tool in ["read_file", "patch", "search_files"]:
            result = _on_pre_tool_call(
                tool_name=tool,
                args={"path": "/tmp/doc.md"},
            )
            assert result is None, f"{tool} should not be blocked"

    def test_no_args_not_blocked(self):
        """Calling hook with no args should never crash."""
        result = _on_pre_tool_call()
        assert result is None

    def test_block_txt_extension(self):
        """write_file with 20K .txt → blocked (doc extension)."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/notes.txt", "content": "G" * 20000},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_block_rst_extension(self):
        """write_file with 20K .rst → blocked."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/doc.rst", "content": "H" * 20000},
        )
        assert result is not None
        assert result.get("action") == "block"

    def test_very_large_non_doc_still_blocked(self):
        """write_file with 60K .py → blocked (universal 12K threshold)."""
        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/script.py", "content": "I" * 60000},
        )
        assert result is not None
        assert result.get("action") == "block"
        assert "execute_code" in result.get("message", "")

    def test_repeated_write_file_detected(self):
        """After resetting counter, first call not warned; second+ is warned."""
        from plugins.doc_session import _write_file_counts
        _write_file_counts.clear()

        # First call — should just return None (allow), but may add hint
        first = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/notes.md", "content": "J" * 6000},
            result="File written successfully",
        )
        # Second call — should hint about repeated writes
        second = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/notes.md", "content": "K" * 6000},
            result="File written successfully",
        )
        assert second is not None
        assert "write_file несколько раз" in second

    def test_not_doc_ext_not_counted(self):
        """Non-doc files should not increment the write counter."""
        from plugins.doc_session import _write_file_counts
        _write_file_counts.clear()

        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/script.py", "content": "L" * 6000},
            result="File written",
        )
        # Should be None since .py is not a doc extension
        assert result is None


# ── transform_tool_result hook tests (Level 3) ─────────────────────────────


class TestTransformToolResultAdvice:
    """Level 3: After write_file, add advice for medium-large content."""

    def test_advice_for_large_md(self):
        """write_file with 6K .md → advice added."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/report.md", "content": "M" * 6000},
            result="Success",
        )
        assert result is not None
        assert "doc_session" in result

    def test_no_advice_for_small_md(self):
        """write_file with 2K .md → no advice."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/small.md", "content": "N" * 2000},
            result="Success",
        )
        assert result is None  # No modification = None

    def test_no_advice_for_code(self):
        """write_file with 6K .py → no advice (code, not doc)."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/script.py", "content": "O" * 6000},
            result="Success",
        )
        assert result is None

    def test_advice_mentions_tools(self):
        """Advice must mention file_doc_create, file_doc_write, file_doc_finalize."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/report.md", "content": "P" * 6000},
            result="OK",
        )
        assert result is not None
        assert "file_doc_create" in result
        assert "file_doc_write" in result
        assert "file_doc_finalize" in result

    def test_error_result_unchanged(self):
        """Error results (dict with error key) should not be modified."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/report.md", "content": "Q" * 6000},
            result={"error": "Permission denied"},
        )
        assert result is None  # Error results are skipped

    def test_no_crash_on_missing_args(self):
        """Calling hook with missing args should not crash."""
        result = _on_transform_tool_result(tool_name="write_file")
        assert result is None

    def test_no_crash_on_none_result(self):
        """Calling hook with None result should not crash."""
        result = _on_transform_tool_result(
            tool_name="write_file",
            args={"path": "/tmp/doc.md", "content": "R" * 6000},
            result=None,
        )
        # Should not crash — either None or a string
        assert result is None or isinstance(result, str)
