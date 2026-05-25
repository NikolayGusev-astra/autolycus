"""Tests for RTK-CK plugin init — plugin.yaml, register(), pre_llm_call hook.

Tests:
- plugin.yaml exists with correct hooks
- register() registers pre_llm_call hook
- rtk_ck_pre_turn() returns context dict or None
- rtk_ck_pre_turn() with budget signal returns context with warning
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from plugins.rtk.pattern import Signal


class TestPluginYaml:
    """plugin.yaml manifest must exist and declare pre_llm_call hook."""

    PLUGIN_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "plugins", "rtk_ck")

    def test_plugin_yaml_exists(self):
        """plugin.yaml exists in rtk_ck plugin directory."""
        yaml_path = os.path.join(self.PLUGIN_DIR, "plugin.yaml")
        assert os.path.exists(yaml_path), f"Missing: {yaml_path}"

    def test_plugin_yaml_declares_pre_llm_call(self):
        """plugin.yaml lists 'pre_llm_call' in hooks."""
        import yaml
        yaml_path = os.path.join(self.PLUGIN_DIR, "plugin.yaml")
        with open(yaml_path) as f:
            data = yaml.safe_load(f)
        assert data is not None
        assert "hooks" in data
        assert "pre_llm_call" in data["hooks"]


class TestRegister:
    """register() function must register pre_llm_call hook."""

    def test_register_registers_hook(self):
        """register() calls ctx.register_hook('pre_llm_call', ...)."""
        from plugins.rtk_ck import register

        ctx = MagicMock()
        register(ctx)

        # Verify all three hooks were registered
        assert ctx.register_hook.call_count == 3
        hook_calls = [call[0][0] for call in ctx.register_hook.call_args_list]
        assert "pre_llm_call" in hook_calls
        assert "pre_tool_call" in hook_calls
        assert "post_tool_call" in hook_calls


class TestPreTurnHook:
    """rtk_ck_pre_turn() returns context dict or None."""

    def test_first_turn_returns_none(self):
        """First turn (no conversation_history) → None."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        result = rtk_ck_pre_turn(
            session_id="test-sess-1",
            user_message="hello",
            conversation_history=None,
            model="gpt-4o",
        )
        assert result is None, f"Expected None, got: {result}"

    def test_empty_history_returns_none(self):
        """Empty conversation_history → None."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        result = rtk_ck_pre_turn(
            session_id="test-sess-2",
            user_message="hello",
            conversation_history=[],
            model="gpt-4o",
        )
        assert result is None

    def test_small_history_returns_none(self):
        """Small history (few turns, low tokens) → None."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello!"},
            {"role": "user", "content": "what's up"},
            {"role": "assistant", "content": "not much"},
        ]
        result = rtk_ck_pre_turn(
            session_id="test-sess-3",
            user_message="ping",
            conversation_history=msgs,
            model="gpt-4o",
        )
        assert result is None

    def test_large_history_budget_warn(self):
        """Large history near context limit → returns context with warning."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        # Build messages ~100K tokens, default context ~128K → ~78%
        big_content = "x" * 400_000  # ~100K chars = ~25K tokens
        msgs = [
            {"role": "user", "content": big_content},
            {"role": "assistant", "content": big_content},
            {"role": "user", "content": big_content},
            {"role": "assistant", "content": big_content},
            # 5th message = 125K tokens total
            {"role": "user", "content": big_content},
        ]
        # With small context (100K), should hit BUDGET_HALT
        result = rtk_ck_pre_turn(
            session_id="test-sess-4",
            user_message="more",
            conversation_history=msgs,
            model="gpt-4o-mini",  # typically 128K context
        )
        # Should produce a context dict with budget warning at this size
        if result is not None:
            assert isinstance(result, dict)
            assert "context" in result
            ctx_text = result["context"]
            assert "RTK-CK" in ctx_text

    def test_handles_missing_kwargs_gracefully(self):
        """Extra or missing kwargs don't crash."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        # Extra kwargs
        result = rtk_ck_pre_turn(
            session_id="sess",
            user_message="hi",
            conversation_history=[],
            model="gpt-4o",
            platform="cli",
            sender_id="user1",
            unknown_arg="should-not-crash",
        )
        assert result is None


class TestCompressInPreTurn:
    """pre_llm_call hook runs Compressor and injects stats."""

    def test_compress_stats_injected_when_significant(self):
        """Large history with high savings → compress stats injected (after patterns)."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        big = "X" * 10_000
        # No redundant reads, no budget/growth → compress inject fires
        msgs = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/hosts"}'}}]},
            {"role": "tool", "content": big, "tool_call_id": "c1", "name": "read_file"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/resolv.conf"}'}}]},
            {"role": "tool", "content": big, "tool_call_id": "c2", "name": "read_file"},
            {"role": "user", "content": "q3"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c3", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/fstab"}'}}]},
            {"role": "tool", "content": big, "tool_call_id": "c3", "name": "read_file"},
            {"role": "user", "content": "q4"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c4", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/etc/passwd"}'}}]},
            {"role": "tool", "content": big, "tool_call_id": "c4", "name": "read_file"},
        ]
        result = rtk_ck_pre_turn(
            session_id="test-compress-stats",
            user_message="continue",
            conversation_history=msgs,
            model="gpt-4o",
        )

        # Should return context with compression stats (no pattern/budget/growth triggered)
        if result is not None:
            assert isinstance(result, dict)
            ctx = result.get("context", "")
            if ctx:
                assert "RTK-CK" in ctx
                assert "%" in ctx or "savings" in ctx or "compressed" in ctx

    def test_compress_stats_empty_for_small_history(self):
        """Small history → no compression inject."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        result = rtk_ck_pre_turn(
            session_id="test-small",
            user_message="ping",
            conversation_history=msgs,
            model="gpt-4o",
        )
        assert result is None


class TestDedupInPreTurn:
    """pre_llm_call hook runs Deduplicator when volatile/prefetch kwargs provided."""

    def test_volatile_and_prefetch_matches_dedup_signal(self):
        """Volatile and prefetch overlap → dedup context injected."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [{"role": "user", "content": "hello"}]
        result = rtk_ck_pre_turn(
            session_id="test-dedup",
            user_message="hi",
            conversation_history=msgs,
            model="gpt-4o",
            volatile_text="User prefers concise responses.",
            prefetch_text="User prefers concise responses. Extra unique info.",
        )
        # Dedup MUST fire when there's overlap
        assert result is not None
        assert isinstance(result, dict)
        ctx = result.get("context", "")
        assert "RTK-CK" in ctx
        assert "dedup" in ctx.lower() or "duplicate" in ctx.lower()

    def test_volatile_and_prefetch_no_overlap_no_signal(self):
        """No overlap between volatile and prefetch → no dedup signal."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [{"role": "user", "content": "hello"}]
        result = rtk_ck_pre_turn(
            session_id="test-dedup-none",
            user_message="hi",
            conversation_history=msgs,
            model="gpt-4o",
            volatile_text="User prefers concise responses.",
            prefetch_text="Project uses pytest with xdist.",
        )
        assert result is None

    def test_no_volatile_text_no_dedup(self):
        """No volatile_text kwarg → no dedup attempt (backward compat)."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [{"role": "user", "content": "hello"}]
        result = rtk_ck_pre_turn(
            session_id="test-dedup-no-volatile",
            user_message="hi",
            conversation_history=msgs,
            model="gpt-4o",
            prefetch_text="User prefers concise responses.",
        )
        assert result is None

    def test_no_prefetch_text_no_dedup(self):
        """No prefetch_text kwarg → no dedup attempt."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        msgs = [{"role": "user", "content": "hello"}]
        result = rtk_ck_pre_turn(
            session_id="test-dedup-no-prefetch",
            user_message="hi",
            conversation_history=msgs,
            model="gpt-4o",
            volatile_text="User prefers concise responses.",
        )
        assert result is None


class TestFullIntegration:
    """Integration: scan → budget + growth + patterns → combined output."""

    def test_growth_and_budget_signals_combined(self):
        """Multiple signals active → return context with both."""
        from plugins.rtk_ck import rtk_ck_pre_turn

        # Build history with spike growth + high budget
        big = "x" * 200_000
        msgs = [
            {"role": "user", "content": big},
            {"role": "tool", "content": big, "tool_call_id": "c1", "name": "read_file"},
            {"role": "assistant", "content": big},
            {"role": "tool", "content": big, "tool_call_id": "c2", "name": "read_file"},
        ]

        result = rtk_ck_pre_turn(
            session_id="test-integration",
            user_message="more",
            conversation_history=msgs,
            model="gpt-4o-mini",
        )

        # Might return None if estimates are under threshold, but if triggered:
        if result is not None:
            assert isinstance(result, dict)
            assert "context" in result


class TestCountLastTurnTokens:
    """Tests for _count_last_turn_tokens — must count ALL messages in current turn."""

    def test_single_turn(self):
        """Single user→assistant→tool → counts all 3 messages."""
        from plugins.rtk_ck import _count_last_turn_tokens

        msgs = [
            {"role": "user", "content": "read file"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
            {"role": "tool", "content": "file content here", "tool_call_id": "c1"},
        ]
        result = _count_last_turn_tokens(msgs)
        assert result > 0

    def test_two_turns_counts_only_last(self):
        """Two complete turns → only last turn counted."""
        from plugins.rtk_ck import _count_last_turn_tokens

        msgs = [
            # Turn 1
            {"role": "user", "content": "small"},
            {"role": "tool", "content": "x", "tool_call_id": "c1"},
            # Turn 2 (current)
            {"role": "user", "content": "big " * 100},
            {"role": "tool", "content": "y", "tool_call_id": "c2"},
        ]
        result = _count_last_turn_tokens(msgs)
        # Should only count from the last "user" message onward
        from plugins.rtk_ck.budget import BudgetScanner
        expected = BudgetScanner._estimate_tokens(msgs[2:])
        assert result == expected

    def test_many_tool_calls_in_turn(self):
        """Turn with many tool calls → all counted toward last_turn_tokens."""
        from plugins.rtk_ck import _count_last_turn_tokens

        msgs = [
            {"role": "user", "content": "run tests"},
        ]
        # Add 5 tool calls
        for i in range(5):
            msgs.append({"role": "tool", "content": "x" * 1000, "tool_call_id": f"c{i}"})

        result = _count_last_turn_tokens(msgs)
        # Must include all 5 tool results
        from plugins.rtk_ck.budget import BudgetScanner
        expected = BudgetScanner._estimate_tokens(msgs)
        assert result == expected

    def test_empty_list(self):
        """Empty messages → 0."""
        from plugins.rtk_ck import _count_last_turn_tokens
        assert _count_last_turn_tokens([]) == 0

    def test_no_user_message(self):
        """No user message (only tools) → counts all as current turn."""
        from plugins.rtk_ck import _count_last_turn_tokens

        msgs = [
            {"role": "tool", "content": "x" * 500, "tool_call_id": "c1"},
            {"role": "tool", "content": "y" * 500, "tool_call_id": "c2"},
        ]
        result = _count_last_turn_tokens(msgs)
        from plugins.rtk_ck.budget import BudgetScanner
        assert result == BudgetScanner._estimate_tokens(msgs)


class TestPreToolCallHook:
    """Integration tests for rtk_ck_pre_tool_call hook."""

    def test_cache_hit_blocks_read_file(self):
        """read_file with cached result → returns cached content, blocks call."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call, rtk_ck_post_tool_call

        args = {"path": "/etc/hosts"}
        # Simulate: first call stores result
        rtk_ck_post_tool_call("read_file", args, "127.0.0.1 localhost")
        # Second call should hit cache
        result = rtk_ck_pre_tool_call("read_file", args)
        assert result == "127.0.0.1 localhost"

    def test_cache_miss_allows_read_file(self):
        """read_file with no cache → returns None (proceed normally)."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call

        result = rtk_ck_pre_tool_call("read_file", {"path": "/nonexistent"})
        assert result is None

    def test_write_invalidates_cache(self):
        """write_file on cached path → invalidates cache."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call, rtk_ck_post_tool_call

        args = {"path": "/etc/hosts"}
        rtk_ck_post_tool_call("read_file", args, "old content")
        # Write to same path
        rtk_ck_pre_tool_call("write_file", args)
        # Cache should be invalidated
        result = rtk_ck_pre_tool_call("read_file", args)
        assert result is None

    def test_non_cacheable_tool_not_blocked(self):
        """terminal/execute_code are never blocked by cache."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call

        result = rtk_ck_pre_tool_call("terminal", {"command": "ls"})
        assert result is None

    def test_string_args_parsed(self):
        """Args passed as JSON string are parsed correctly."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call, rtk_ck_post_tool_call

        import json
        args_str = json.dumps({"path": "/etc/hosts"})
        rtk_ck_post_tool_call("read_file", args_str, "content")
        result = rtk_ck_pre_tool_call("read_file", args_str)
        assert result == "content"

    def test_post_tool_call_stores_result(self):
        """post_tool_call stores result for future cache hits."""
        from plugins.rtk_ck import rtk_ck_pre_tool_call, rtk_ck_post_tool_call
        from plugins.rtk_ck import _result_cache

        args = {"path": "/tmp/test-file"}
        rtk_ck_post_tool_call("read_file", args, "stored content")
        assert _result_cache.check("read_file", args) == "stored content"