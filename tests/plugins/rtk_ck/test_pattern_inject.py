"""Tests for RTK-CK Pattern injection in pre_llm_call hook.

PatternDetector now injects warnings (not just logs) when patterns are detected.
"""
from __future__ import annotations

import pytest


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _tool_call(name: str = "read_file", args: str = '{"path":"/etc/hosts"}', tid: str = "c1") -> dict:
    return {
        "role": "assistant", "content": "",
        "tool_calls": [{"id": tid, "type": "function", "function": {"name": name, "arguments": args}}],
    }


def _tool_result(content: str, tid: str = "c1", name: str = "read_file") -> dict:
    return {"role": "tool", "content": content, "tool_call_id": tid, "name": name}


def _tool_error(error: str, tid: str = "c1", name: str = "terminal") -> dict:
    return {"role": "tool", "content": error, "tool_call_id": tid, "name": name}


class TestPatternInjection:
    """PatternDetector signals injected into user message context."""

    def test_redundant_reads_injects_warning(self):
        """3+ reads of same file → inject REDUNDANT_READS warning."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [
            _user("read hosts"),
            _tool_call("read_file", '{"path":"/etc/hosts"}', "c1"),
            _tool_result("127.0.0.1 localhost", "c1"),
            _user("read hosts again"),
            _tool_call("read_file", '{"path":"/etc/hosts"}', "c2"),
            _tool_result("127.0.0.1 localhost", "c2"),
            _user("read hosts again"),
            _tool_call("read_file", '{"path":"/etc/hosts"}', "c3"),
            _tool_result("127.0.0.1 localhost", "c3"),
        ]
        result = rtk_ck_pre_turn(
            session_id="test-pattern-redun",
            user_message="continue",
            conversation_history=msgs,
            model="gpt-4o",
        )
        assert result is not None
        assert isinstance(result, dict)
        ctx = result.get("context", "")
        assert "RTK-CK" in ctx
        assert "REDUNDANT_READS" in ctx
        assert "/etc/hosts" in ctx

    def test_stalled_session_injects_critical(self):
        """3+ error cycles → inject STALLED_SESSION critical."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [
            _user("connect"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c1"),
            _tool_error("Error: timeout", "c1", "terminal"),
            _user("try again"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c2"),
            _tool_error("Error: timeout", "c2", "terminal"),
            _user("try again"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c3"),
            _tool_error("Error: timeout", "c3", "terminal"),
        ]
        result = rtk_ck_pre_turn(
            session_id="test-pattern-stall",
            user_message="continue",
            conversation_history=msgs,
            model="gpt-4o",
        )
        assert result is not None
        assert isinstance(result, dict)
        ctx = result.get("context", "")
        assert "RTK-CK" in ctx
        assert "STALLED_SESSION" in ctx

    def test_no_patterns_no_inject(self):
        """Clean session → no pattern inject."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [
            _user("hello"),
            _tool_call("read_file", '{"path":"/etc/hosts"}', "c1"),
            _tool_result("127.0.0.1 localhost", "c1"),
            _user("done"),
        ]
        result = rtk_ck_pre_turn(
            session_id="test-pattern-clean",
            user_message="bye",
            conversation_history=msgs,
            model="gpt-4o",
        )
        # No budget/growth/pattern/dedup signals → None
        assert result is None

    def test_pattern_inject_priority_over_dedup(self):
        """Pattern signals fire before dedup (budget → growth → pattern → dedup)."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        # Both stalled session AND dedup would fire
        msgs = [
            _user("connect"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c1"),
            _tool_error("Error: timeout", "c1", "terminal"),
            _user("try again"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c2"),
            _tool_error("Error: timeout", "c2", "terminal"),
            _user("try again"),
            _tool_call("terminal", '{"command":"deploy.sh"}', "c3"),
            _tool_error("Error: timeout", "c3", "terminal"),
        ]
        result = rtk_ck_pre_turn(
            session_id="test-pattern-priority",
            user_message="continue",
            conversation_history=msgs,
            model="gpt-4o",
            volatile_text="Some memory.",
            prefetch_text="Some memory. Unique info.",
        )
        assert result is not None
        ctx = result.get("context", "")
        # Pattern fires before dedup in the chain
        assert "STALLED_SESSION" in ctx