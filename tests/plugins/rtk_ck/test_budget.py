"""Tests for RTK-CK BudgetScanner — token budget estimation vs context window.

Tests are pure functions: create messages[], call scan(), assert Signal or None.
"""
from __future__ import annotations

import pytest

from plugins.rtk.pattern import Signal


# ---------------------------------------------------------------------------
# Helpers to build message lists at specific token counts
# ---------------------------------------------------------------------------


def _make_text_msg(role: str, text: str) -> dict:
    """Create a message dict with text content."""
    return {"role": role, "content": text}


def _make_tool_result(name: str, content: str) -> dict:
    """Create a tool result message."""
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": "call_1",
        "name": name,
    }


def _build_messages(token_count: int, seed: int = 0) -> list:
    """Build a messages list with approximately ``token_count`` rough tokens.

    Uses estimate_messages_tokens_rough's formula: chars/4 ≈ tokens.
    So token_count * 4 chars ≈ token_count tokens.
    """
    chars_needed = token_count * 4
    # Split into 5 alternating user/assistant messages
    per_msg = max(10, chars_needed // 5)
    msgs = []
    for i in range(5):
        role = "user" if i % 2 == 0 else "assistant"
        text = f"Message {seed}_{i}_{'x' * per_msg}"
        msgs.append(_make_text_msg(role, text))
    return msgs


# ---------------------------------------------------------------------------
# BudgetScanner tests
# ---------------------------------------------------------------------------


class TestBudgetScan:
    """BudgetScanner.scan() returns Signal or None based on token usage."""

    def test_empty_messages_returns_none(self):
        """Completely empty message list → no signal."""
        from plugins.rtk_ck.budget import BudgetScanner

        result = BudgetScanner.scan([], context_length=128_000)
        assert result is None

    def test_low_usage_returns_none(self):
        """~20% of context → no signal."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=25_000)
        # 25K / 128K ≈ 20%
        result = BudgetScanner.scan(msgs, context_length=128_000)
        assert result is None

    def test_warn_at_82_percent(self):
        """>80% context → BUDGET_WARN signal."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=105_000)
        # 105K / 128K ≈ 82%
        result = BudgetScanner.scan(msgs, context_length=128_000)

        assert result is not None
        assert result.code == "BUDGET_WARN"
        assert result.severity == "warn"
        assert result.should_halt is False
        assert "82" in result.message or "80" in result.message or "105" in result.message

    def test_critical_at_96_percent(self):
        """>95% context → BUDGET_CRITICAL signal."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=123_000)
        # 123K / 128K ≈ 96%
        result = BudgetScanner.scan(msgs, context_length=128_000)

        assert result is not None
        assert result.code == "BUDGET_CRITICAL"
        assert result.severity == "critical"
        assert result.should_halt is False
        assert "96" in result.message or "95" in result.message or "123" in result.message

    def test_halt_at_100_percent(self):
        """>=100% context → BUDGET_HALT with should_halt=True."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=130_000)
        # 130K / 128K ≈ 101% → halt
        result = BudgetScanner.scan(msgs, context_length=128_000)

        assert result is not None
        assert result.code == "BUDGET_HALT"
        assert result.severity == "critical"
        assert result.should_halt is True

    def test_exact_80_percent_boundary(self):
        """Just under 80% → no warn (warn starts at 80%)."""
        from plugins.rtk_ck.budget import BudgetScanner

        # Use 79% of context to stay safely under the 80% threshold
        msgs = _build_messages(token_count=101_000)
        # 101K / 128K ≈ 78.9% → under 80%
        result = BudgetScanner.scan(msgs, context_length=128_000)

        assert result is None

    def test_warn_just_above_80(self):
        """80.1% → warn fires."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=102_500)
        # 102500 / 128000 ≈ 80.08%
        result = BudgetScanner.scan(msgs, context_length=128_000)

        assert result is not None
        assert result.code == "BUDGET_WARN"

    def test_custom_thresholds(self):
        """Config overrides default warn/critical/halt thresholds."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=60_000)
        # 60K / 128K = 47%
        config = {"warn_pct": 40, "critical_pct": 50, "halt_pct": 60}
        result = BudgetScanner.scan(msgs, context_length=128_000, config=config)

        # 47% > 40% → warn (but < 50%, not critical)
        assert result is not None
        assert result.code == "BUDGET_WARN"
        assert result.severity == "warn"

    def test_custom_thresholds_critical(self):
        """Config with warn=40, critical=50, halt=60 → 55% is critical."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=70_000)
        # 70K / 128K ≈ 55%
        config = {"warn_pct": 40, "critical_pct": 50, "halt_pct": 60}
        result = BudgetScanner.scan(msgs, context_length=128_000, config=config)

        assert result is not None
        assert result.code == "BUDGET_CRITICAL"
        assert result.severity == "critical"

    def test_halt_with_custom_threshold(self):
        """Config halt at 60% → 65% usage should_halt=True."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = _build_messages(token_count=83_000)
        # 83K / 128K ≈ 65%
        config = {"warn_pct": 40, "critical_pct": 50, "halt_pct": 60}
        result = BudgetScanner.scan(msgs, context_length=128_000, config=config)

        assert result is not None
        assert result.code == "BUDGET_HALT"
        assert result.should_halt is True

    def test_tool_results_included_in_estimate(self):
        """Large tool results contribute to budget scan."""
        from plugins.rtk_ck.budget import BudgetScanner

        msgs = [
            {"role": "user", "content": "small query"},
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"/etc/hosts"}'}
            }]},
            {"role": "tool", "content": "x" * (340_000),  # ~85K tokens
             "tool_call_id": "call_1", "name": "read_file"},
        ]
        # 85K + headers → over 80% but under 100% of 100K context
        result = BudgetScanner.scan(msgs, context_length=100_000)
        assert result is not None
        assert result.code in ("BUDGET_WARN", "BUDGET_CRITICAL")