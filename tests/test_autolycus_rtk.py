"""Tests for the ultra-governance RTK Filter (Reduced Token Kernel).

Covers all exported functions from ``plugins/ultra-governance/rtk.py``:

  * _load_rtk_config  — config with sensible defaults
  * _compact_repeats  — collapse repeated line sequences
  * _head_tail_truncate — head/tail truncation + hard cap
  * apply             — full pipeline (repeat compaction → truncation)
  * transform_tool_result — ``transform_tool_result`` hook wrapper

Test plan (from TEST_PLAN.md):
  - 3 test groups: head/tail truncation, repeat compaction, output cap
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers: import rtk module from the hyphenated plugin directory
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_rtk():
    """Import and return the rtk module using importlib (hyphenated dir)."""
    lib_path = _REPO_ROOT / "plugins" / "ultra-governance" / "rtk.py"
    spec = importlib.util.spec_from_file_location(
        "ultra_governance_rtk_under_test", lib_path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_rtk(**overrides):
    """Return (rtk_module, config_dict) with config values overridden.

    Patches ``_load_rtk_config`` on the module so calls to ``apply()``
    use the supplied config.
    """
    defaults = {
        "enabled": True,
        "head_chars": 2000,
        "tail_chars": 1000,
        "min_repeat_lines": 5,
        "max_output_chars": 10000,
    }
    defaults.update(overrides)
    rtk = _load_rtk()
    rtk._load_rtk_config = lambda: dict(defaults)
    return rtk, defaults


# ===========================================================================
# _load_rtk_config
# ===========================================================================


class TestLoadRtkConfig:
    def test_returns_defaults_when_config_unavailable(self):
        """When cfg_get can't find the section, defaults are returned."""
        rtk = _load_rtk()
        cfg = rtk._load_rtk_config()
        assert cfg == {
            "enabled": True,
            "head_chars": 2000,
            "tail_chars": 1000,
            "min_repeat_lines": 5,
            "max_output_chars": 10000,
        }

    def test_gracefully_handles_import_error(self):
        """If hermes_cli.config is not importable, fall back to defaults."""
        rtk = _load_rtk()
        # cfg_get is imported with `from ... import cfg_get`, so it's not
        # an attribute of the module. Instead, we patch the _load_rtk_config
        # try-block's cfg_get by patching at hermes_cli.config level.
        import hermes_cli.config
        with patch.object(hermes_cli.config, "cfg_get", side_effect=ImportError("no config")):
            cfg = rtk._load_rtk_config()
        assert cfg == {
            "enabled": True,
            "head_chars": 2000,
            "tail_chars": 1000,
            "min_repeat_lines": 5,
            "max_output_chars": 10000,
        }
        # The function already returned defaults above — the real proof is
        # that calling _load_rtk_config doesn't crash even when the config
        # module is absent.  Since we patched nothing, this already passes.
        cfg = rtk._load_rtk_config()
        assert cfg["enabled"] is True


# ===========================================================================
# _compact_repeats
# ===========================================================================


class TestCompactRepeats:
    """Collapse repeated line sequences of *min_repeat_lines* or more."""

    def test_no_repeats_passthrough(self):
        rtk = _load_rtk()
        text = "line1\nline2\nline3\nline4\n"
        assert rtk._compact_repeats(text) == text

    def test_below_threshold_unchanged(self):
        """Fewer repeated lines than threshold → passthrough."""
        rtk = _load_rtk()
        text = "a\nb\nb\nb\nb\nc\n"  # 4 repeats (below default 5)
        result = rtk._compact_repeats(text)
        assert result == text

    def test_exact_threshold_compacted(self):
        """Exactly min_repeat_lines repeats → compacted."""
        rtk = _load_rtk()
        text = "x\n" + "repeat\n" * 5 + "y\n"
        result = rtk._compact_repeats(text)
        assert "repeated 5 times" in result
        assert result.count("repeat\n") == 1  # single copy kept, rest compacted
        assert "x\n" in result
        assert "y\n" in result

    def test_above_threshold_compacted(self):
        """More lines than threshold → compacted with correct count."""
        rtk = _load_rtk()
        text = "start\n" + "same\n" * 10 + "end\n"
        result = rtk._compact_repeats(text)
        assert "repeated 10 times" in result
        assert result.count("same") == 1  # only one copy kept

    def test_custom_min_repeat_lines(self):
        """Custom threshold passed as argument."""
        rtk = _load_rtk()
        text = "a\n" + "dup\n" * 3 + "b\n"
        # Default threshold is 5, so 3 repeats pass through
        assert rtk._compact_repeats(text) == text
        # With custom threshold 3, they get compacted
        result = rtk._compact_repeats(text, min_repeat_lines=3)
        assert "repeated 3 times" in result

    def test_multiple_repeat_blocks(self):
        """Multiple distinct repeat blocks are each compacted."""
        rtk = _load_rtk()
        lines = []
        lines.extend(["A\n"] * 7)
        lines.append("separator\n")
        lines.extend(["B\n"] * 6)
        lines.append("end\n")
        text = "".join(lines)
        result = rtk._compact_repeats(text)
        assert "repeated 7 times" in result
        assert "separator" in result
        assert "repeated 6 times" in result

    def test_short_text_no_split(self):
        """Text with fewer lines than threshold is unchanged."""
        rtk = _load_rtk()
        text = "a\nb\nc\n"  # only 3 lines
        assert rtk._compact_repeats(text) == text

    def test_trailing_newline_handling(self):
        """Preserves trailing newline behavior."""
        rtk = _load_rtk()
        text = "ok\n" + "dup\n" * 6
        result = rtk._compact_repeats(text)
        assert "repeated 6 times" in result
        # Should not double the newline
        assert "\n\n\n" not in result

    def test_empty_text(self):
        """Empty string returns empty string."""
        rtk = _load_rtk()
        assert rtk._compact_repeats("") == ""


# ===========================================================================
# _head_tail_truncate
# ===========================================================================


class TestHeadTailTruncate:
    """Keep first N + last M chars, with truncation note."""

    def test_short_text_passthrough(self):
        """Text under both head+tail and max_total → unchanged."""
        rtk = _load_rtk()
        text = "Hello, world!"
        assert rtk._head_tail_truncate(text) == text

    def test_at_head_tail_boundary(self):
        """Text length == head_chars + tail_chars → unchanged."""
        rtk = _load_rtk()
        text = "A" * 2999 + "B"  # 3000 chars, head+tail = 3000
        # Defaults: head=2000, tail=1000 = 3000
        # But max_total=10000, so it's under cap and under head+tail
        assert rtk._head_tail_truncate(text) == text

    def test_above_head_tail_below_cap(self):
        """Text longer than head+tail but under max_total → truncated with note."""
        rtk = _load_rtk()
        head = "A" * 2000
        middle = "M" * 500
        tail = "B" * 1000
        text = head + middle + tail  # 3500 chars
        result = rtk._head_tail_truncate(text)
        assert result.startswith("A" * 2000)
        assert result.endswith("B" * 1000)
        assert "truncated 500 chars" in result
        assert len(result) < len(text)

    def test_above_max_cap(self):
        """Text exceeding max_total → aggressive truncation with cap warning."""
        rtk = _load_rtk()
        head = "A" * 2000
        middle = "M" * 20000
        tail = "B" * 1000
        text = head + middle + tail  # 23000 chars
        result = rtk._head_tail_truncate(text, head_chars=2000, tail_chars=1000, max_total=10000)
        assert result.startswith("A" * 2000)
        assert result.endswith("B" * 1000)
        assert "WARNING: output capped at 10000" in result
        assert len(result) < len(text)

    def test_exactly_max_cap(self):
        """Text exactly at max_total but above head+tail → truncated."""
        rtk = _load_rtk()
        text = "X" * 10000
        result = rtk._head_tail_truncate(text, head_chars=2000, tail_chars=1000, max_total=10000)
        assert result.startswith("X" * 2000)
        assert result.endswith("X" * 1000)
        assert "truncated" in result
        assert len(result) < len(text)

    def test_custom_head_tail_values(self):
        """Custom head/tail limits are respected."""
        rtk = _load_rtk()
        text = "HEAD" + "MIDDLE" * 100 + "TAIL"
        result = rtk._head_tail_truncate(text, head_chars=4, tail_chars=4, max_total=1000)
        assert result.startswith("HEAD")
        assert result.endswith("TAIL")
        assert "truncated" in result

    def test_head_tail_overlap(self):
        """If head+tail > len(text) (negative middle), return text unchanged."""
        rtk = _load_rtk()
        text = "A" * 500  # well under head+tail=3000
        result = rtk._head_tail_truncate(text, head_chars=300, tail_chars=300, max_total=10000)
        assert result == text

    def test_empty_text(self):
        """Empty string returns empty string."""
        rtk = _load_rtk()
        assert rtk._head_tail_truncate("") == ""


# ===========================================================================
# apply — full pipeline
# ===========================================================================


class TestApply:
    """Full RTK filter pipeline: compact repeats → head/tail truncation."""

    def test_disabled_config_returns_text_unchanged(self):
        """When rtk.enabled is False, apply returns text as-is."""
        rtk, _cfg = _make_rtk(enabled=False)
        text = "X" * 100000
        assert rtk.apply(text) is text

    def test_non_string_returns_as_is(self):
        """Non-string input passes through unchanged."""
        rtk, _cfg = _make_rtk()
        assert rtk.apply(42) == 42
        assert rtk.apply(None) is None
        assert rtk.apply([]) == []

    def test_empty_string_returns_as_is(self):
        """Empty string passes through unchanged."""
        rtk, _cfg = _make_rtk()
        assert rtk.apply("") == ""

    def test_short_text_passthrough(self):
        """Short text under all thresholds passes through."""
        rtk, _cfg = _make_rtk()
        text = "Hello, world!\n"
        assert rtk.apply(text) == text

    def test_repeat_compaction_applied(self):
        """Repeated lines are compacted by the pipeline."""
        rtk, cfg = _make_rtk(min_repeat_lines=3)
        text = "start\n" + "same\n" * 10 + "end\n"
        result = rtk.apply(text)
        assert "repeated 10 times" in result

    def test_head_tail_truncation_applied(self):
        """Long text gets head/tail truncation."""
        rtk, cfg = _make_rtk(head_chars=100, tail_chars=50, max_output_chars=5000)
        head = "A" * 100
        middle = "M" * 300
        tail = "B" * 50
        text = head + middle + tail
        result = rtk.apply(text)
        assert result.startswith("A" * 100)
        assert result.endswith("B" * 50)
        assert "truncated" in result

    def test_both_steps_combined(self):
        """Both repeat compaction and head/tail run on the same text."""
        rtk, cfg = _make_rtk(
            min_repeat_lines=3,
            head_chars=50,
            tail_chars=50,
            max_output_chars=5000,
        )
        lines = ["header\n"]
        lines.extend(["dup\n"] * 20)
        lines.append("footer\n")
        lines.append("A" * 200 + "\n")
        text = "".join(lines)
        result = rtk.apply(text)
        assert "repeated 20 times" in result
        # Should be shorter after truncation
        assert len(result) < len(text)

    def test_custom_config_from_call(self):
        """Config changes via mock are reflected."""
        rtk, cfg = _make_rtk(head_chars=50, tail_chars=30, max_output_chars=5000)
        text = "A" * 50 + "B" * 200 + "C" * 30  # 280 chars, middle = 200
        result = rtk.apply(text)
        assert len(result) < len(text)
        assert result.startswith("A" * 50)
        assert result.endswith("C" * 30)
        assert "truncated 200 chars" in result

    def test_raw_bypass(self):
        """raw=True returns text unchanged even if it exceeds thresholds."""
        rtk, _cfg = _make_rtk()
        text = "X" * 100000
        assert rtk.apply(text, raw=True) is text


# ===========================================================================
# transform_tool_result — hook wrapper
# ===========================================================================


class TestTransformToolResult:
    """``transform_tool_result`` hook: decides when to apply RTK filtering."""

    def test_skips_non_string_result(self):
        """Non-string results return None (no change)."""
        rtk, _cfg = _make_rtk()
        assert rtk.transform_tool_result(result=42) is None
        assert rtk.transform_tool_result(result=None) is None
        assert rtk.transform_tool_result(result=[1, 2, 3]) is None
        assert rtk.transform_tool_result(result=True) is None

    def test_skips_small_strings(self):
        """Strings under 500 chars return None."""
        rtk, _cfg = _make_rtk()
        assert rtk.transform_tool_result(result="x" * 499) is None

    def test_processes_large_strings(self):
        """Strings >= 500 chars get processed; if under thresholds, returned as-is."""
        rtk, _cfg = _make_rtk()
        text = "A" * 1000
        result = rtk.transform_tool_result(result=text)
        # With default config, 1000 < head+tail (3000), so text passes through unchanged
        assert result == text

    def test_large_string_gets_truncated(self):
        """Large strings exceeding thresholds get truncated."""
        rtk, cfg = _make_rtk(head_chars=100, tail_chars=50, max_output_chars=5000)
        head = "A" * 100
        middle = "M" * 2000
        tail = "B" * 50
        text = head + middle + tail
        result = rtk.transform_tool_result(tool_name="test_tool", result=text)
        assert result is not None
        assert result.startswith("A" * 100)
        assert "truncated" in result

    def test_logs_when_filter_applies(self, caplog):
        """Logs a debug message when RTK saves characters."""
        rtk, cfg = _make_rtk(head_chars=50, tail_chars=50, max_output_chars=5000)
        import logging
        caplog.set_level(logging.DEBUG)
        text = "A" * 50 + "M" * 500 + "B" * 50
        rtk.transform_tool_result(tool_name="my_tool", result=text)
        assert "RTK: my_tool" in caplog.text
        assert "saved" in caplog.text

    def test_accepts_all_kwargs(self):
        """Hook-compatible signature accepts all expected kwargs."""
        rtk, _cfg = _make_rtk()
        # This should not raise
        result = rtk.transform_tool_result(
            tool_name="ls",
            args={"path": "/tmp"},
            result="hello",
            task_id="t1",
            session_id="s1",
            extra_arg="ignored",
        )
        assert result is None  # "hello" is < 500 chars

    def test_empty_string_skipped(self):
        """Empty string result returns None."""
        rtk, _cfg = _make_rtk()
        assert rtk.transform_tool_result(result="") is None

    def test_raw_bypass_via_args(self):
        """When args contain rtk_raw=True, filtering is skipped."""
        rtk, cfg = _make_rtk(head_chars=10, tail_chars=10, max_output_chars=100)
        text = "A" * 50 + "M" * 500 + "B" * 50  # 600 chars
        # Without raw
        result = rtk.transform_tool_result(
            tool_name="big_tool", args={}, result=text,
        )
        assert result is not None
        assert len(result) < len(text)  # truncated
        # With rtk_raw=True
        result_raw = rtk.transform_tool_result(
            tool_name="big_tool", args={"rtk_raw": True}, result=text,
        )
        assert result_raw == text  # unchanged

    def test_raw_bypass_false_does_not_skip(self):
        """When rtk_raw is explicitly False, filtering still applies."""
        rtk, cfg = _make_rtk(head_chars=10, tail_chars=10, max_output_chars=100)
        text = "A" * 50 + "M" * 500 + "B" * 50
        result = rtk.transform_tool_result(
            tool_name="big_tool", args={"rtk_raw": False}, result=text,
        )
        assert result is not None
        assert len(result) < len(text)  # truncated, raw=False doesn't bypass
