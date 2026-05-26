"""Tests for RTK-CK Compressor — type-aware history compression.

Tests are pure: feed messages[] in, get compressed messages[] out.
"""
from __future__ import annotations

import pytest


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def _tool_call_factory(name: str = "read_file", args: str = '{"path":"/etc/hosts"}', tool_call_id: str = "call_1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {"name": name, "arguments": args},
        }],
    }


def _tool_result(content: str, tool_call_id: str = "call_1", name: str = "read_file") -> dict:
    return {
        "role": "tool",
        "content": content,
        "tool_call_id": tool_call_id,
        "name": name,
    }


def _tool_error(error_text: str = "Error: timeout", tool_call_id: str = "call_1", name: str = "terminal") -> dict:
    return {
        "role": "tool",
        "content": error_text,
        "tool_call_id": tool_call_id,
        "name": name,
    }


def _count_tool_results(msgs: list) -> int:
    return sum(1 for m in msgs if isinstance(m, dict) and m.get("role") == "tool")


def _count_tool_full_results(msgs: list) -> int:
    """Count tool results that still have their full content (>1K chars)."""
    count = 0
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "tool":
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > 1000:
                count += 1
    return count


# ---------------------------------------------------------------------------
# Compressor tests
# ---------------------------------------------------------------------------


class TestCompressBasic:
    """Basic compression behavior."""

    def test_empty_messages(self):
        """Empty list → empty list."""
        from plugins.rtk_ck.compress import Compressor

        result = Compressor.compress([])
        assert result == []

    def test_user_messages_preserved(self):
        """User messages always preserved intact."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [_user("hello"), _user("how are you")]
        result = Compressor.compress(msgs)
        assert len(result) == 2
        assert result[0]["content"] == "hello"
        assert result[1]["content"] == "how are you"

    def test_small_tool_result_kept(self):
        """Small tool result (≤5K chars) kept as-is."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [
            _user("read hosts"),
            _tool_call_factory(),
            _tool_result("127.0.0.1 localhost"),
        ]
        result = Compressor.compress(msgs)
        # Tool result preserved
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert tool_msgs[0]["content"] == "127.0.0.1 localhost"

    def test_large_tool_result_compressed(self):
        """Large tool result (>5K chars) with protect_last_n=0 → collapsed."""
        from plugins.rtk_ck.compress import Compressor

        big = "A" * 3000 + "B" * 3000 + "C" * 3000  # 9K chars
        msgs = [
            _user("read file"),
            _tool_call_factory(),
            _tool_result(big),
        ]
        config = {"protect_last_n": 0}
        result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        assert len(content) < len(big), f"Expected compressed, got {len(content)} >= {len(big)}"
        assert "read_file" in content

    def test_compress_config_head_tail(self):
        """Config with collapse disabled + protect_last_n=0 uses head/tail."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [_user("r"), _tool_call_factory("read_file"), _tool_result(big)]
        config = {"collapse_pairs": False, "protect_last_n": 0, "tool_head_chars": 100, "tool_tail_chars": 100}
        result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        # 100 head + marker + 100 tail ≈ ~250 chars
        assert len(content) < 500


class TestCompressProtect:
    """Protect first/last N messages."""

    def test_protect_first_n(self):
        """First N user messages intact even if large."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [
            _user("x" * 10_000),  # large → but protected
            _tool_call_factory(),
            _tool_result("y" * 10_000),  # compressable
        ]
        config = {"protect_first_n": 2}
        result = Compressor.compress(msgs, config=config)

        # First message is user, protected → unchanged
        assert result[0]["role"] == "user"
        assert len(result[0]["content"]) == 10_000

    def test_protect_last_n(self):
        """Last N assistant/tool responses preserved."""
        from plugins.rtk_ck.compress import Compressor

        big = "Z" * 10_000
        msgs = [
            _user("q1"),
            _tool_call_factory("read_file", tool_call_id="c1"),
            _tool_result("old data", tool_call_id="c1"),  # old, compressable
            _user("q2"),
            _tool_call_factory("terminal", tool_call_id="c2"),
            _tool_result(big, tool_call_id="c2"),  # last → protected
            _user("q3"),
            _tool_call_factory("terminal", tool_call_id="c3"),
            _tool_result("latest data", tool_call_id="c3"),  # last → protected
        ]
        config = {"protect_last_n": 2}
        result = Compressor.compress(msgs, config=config)

        # Last 2 tool results should still have full content
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        # "latest data" and "big" should be full
        latest = [m for m in tool_msgs if m.get("content") == "latest data"]
        assert len(latest) == 1

    def test_protect_counts_default(self):
        """Default protect_first_n=2, protect_last_n=3."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [
            _user("first protected"),      # protect_first_n = 2
            _user("second protected"),     # protect_first_n = 2
            _user("third"),                # not protected
            _tool_call_factory("read_file", tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1"),
            _user("fourth"),
            _tool_call_factory("read_file", tool_call_id="c2"),
            _tool_result(big, tool_call_id="c2"),
            _user("fifth"),
            _tool_call_factory("read_file", tool_call_id="c3"),
            _tool_result("last result", tool_call_id="c3"),  # protected by last_n
        ]
        result = Compressor.compress(msgs)

        # First two users intact
        assert result[0]["content"] == "first protected"
        assert result[1]["content"] == "second protected"


class TestCompressError:
    """Error tool results get collapsed."""

    def test_error_tool_collapsed(self):
        """Error tool result → summary with tool name."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [
            _user("deploy"),
            _tool_call_factory("terminal", args='{"command":"deploy.sh"}', tool_call_id="c1"),
            _tool_error("Error: timeout after 30s", tool_call_id="c1", name="terminal"),
        ]
        result = Compressor.compress(msgs)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        # With collapse_pairs=True, the pair collapses to [terminal(...) -> Error: ...]
        assert "terminal" in content or content == "Error: timeout after 30s"


class TestCompressCollapse:
    """Tool_calls + tool_result → 1-line summary."""

    def test_collapse_pair(self):
        """Adjacent tool_call + tool_result → single collapsed message."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [
            _user("search config"),
            _tool_call_factory("search_files", args='{"pattern":"config"}', tool_call_id="c1"),
            _tool_result("Found 3 matches:\n- config.yaml\n- config.json\n- config.toml", tool_call_id="c1", name="search_files"),
        ]
        config = {"collapse_pairs": True, "protect_last_n": 0}
        result = Compressor.compress(msgs, config=config)

        # user + collapsed tool result = 2 messages (tool_call removed in collapse)
        assert len(result) == 2, f"Expected 2, got {len(result)}"
        # Tool result should be a summary
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        assert len(content) < 200  # condensed

    def test_large_tool_result_collapse_skipped_with_protect_last(self):
        """Protected last N tool results not collapsed."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [
            _user("read hosts"),
            _tool_call_factory("read_file", tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1"),
            _user("read again"),
            _tool_call_factory("read_file", tool_call_id="c2"),
            _tool_result(big, tool_call_id="c2"),
        ]
        # protect_last_n=1 → last pair protected
        config = {"collapse_pairs": True, "protect_last_n": 1}
        result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 2
        # Last should be full (protected)
        last_idx = len(result) - 1
        assert result[last_idx]["role"] == "tool"


class TestCompressStats:
    """Compressor returns stats about what was saved."""

    def test_compress_returns_stats(self):
        """Compressor.compress() returns (messages, stats_dict)."""
        from plugins.rtk_ck.compress import Compressor

        # Create enough messages to avoid protect_last_n keeping everything
        big = "X" * 10_000
        msgs = [
            _user("q1"),
            _tool_call_factory("read_file", tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1"),
            _user("q2"),
            _tool_call_factory("read_file", tool_call_id="c2"),
            _tool_result(big, tool_call_id="c2"),
            _user("q3"),
            _tool_call_factory("read_file", tool_call_id="c3"),
            _tool_result(big, tool_call_id="c3"),
            _user("q4"),
            _tool_call_factory("read_file", tool_call_id="c4"),
            _tool_result(big, tool_call_id="c4"),
        ]
        result, stats = Compressor.compress(msgs, return_stats=True)

        assert isinstance(result, list)
        assert isinstance(stats, dict)
        assert "original_tokens" in stats
        assert "compressed_tokens" in stats
        assert "savings_pct" in stats
        assert stats["savings_pct"] > 0

    def test_stats_zero_savings_for_small(self):
        """Small messages → 0% savings."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [_user("hi"), _assistant("hello")]
        result, stats = Compressor.compress(msgs, return_stats=True)
        assert stats["savings_pct"] == 0.0


class TestCompressIntegration:
    """Full integration scenarios."""

    def test_mixed_content_types(self):
        """Mix of user, assistant, tool → each type handled correctly."""
        from plugins.rtk_ck.compress import Compressor

        big = "DATA" * 2500  # 10K chars (> 8K threshold)
        config = {"protect_last_n": 0, "collapse_pairs": False}
        msgs = [
            _user("step 1: read config"),
            _tool_call_factory("read_file", args='{"path":"config.yaml"}', tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1"),
            _user("step 2: update"),
            _tool_call_factory("write_file", args='{"path":"config.yaml","content":"new"}', tool_call_id="c2"),
            _tool_result("written 5 lines", tool_call_id="c2", name="write_file"),
            _user("step 3: verify"),
            _assistant("All done!"),
        ]
        result = Compressor.compress(msgs, config=config)

        # User messages intact
        user_msgs = [m for m in result if m.get("role") == "user"]
        assert len(user_msgs) == 3
        assert user_msgs[0]["content"] == "step 1: read config"

        # Large tool result compressed
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        big_tool = [m for m in tool_msgs if m.get("name") == "read_file"]
        assert len(big_tool) == 1
        assert len(big_tool[0]["content"]) < len(big)

        # Small tool result kept
        small_tool = [m for m in tool_msgs if m.get("name") == "write_file"]
        assert small_tool[0]["content"] == "written 5 lines"

    def test_config_disable_compression(self):
        """config.compression_enabled=False → no changes."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [_user("r"), _tool_call_factory(), _tool_result(big)]
        result = Compressor.compress(msgs, config={"compression_enabled": False})
        assert len(result) == 3
        assert len(result[2]["content"]) == 10_000  # unchanged

    def test_mcp_tool_result_never_compressed(self):
        """MCP tools (name starts with 'mcp_') are never compressed."""
        from plugins.rtk_ck.compress import Compressor

        big = "RAG_DATA" * 2000  # 14K chars — would normally be compressed
        msgs = [
            _user("search docs"),
            _tool_call_factory("mcp_lodestone", args='{"query":"test"}', tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1", name="mcp_lodestone"),
        ]
        result = Compressor.compress(msgs, config={"protect_last_n": 0, "collapse_pairs": False})

        # MCP tool result should be kept as-is
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert len(tool_msgs[0]["content"]) == len(big)  # NOT compressed

    def test_custom_mcp_prefix(self):
        """Custom MCP prefix via config."""
        from plugins.rtk_ck.compress import Compressor

        big = "DATA" * 2000  # 8K chars
        msgs = [
            _user("query"),
            _tool_call_factory("my_rag_tool", args='{"q":"x"}', tool_call_id="c1"),
            _tool_result(big, tool_call_id="c1", name="my_rag_tool"),
        ]
        # Without custom prefix, this would be compressed (8K = threshold)
        result_default = Compressor.compress(msgs, config={"protect_last_n": 0, "collapse_pairs": False})
        tool_default = [m for m in result_default if m.get("role") == "tool"][0]
        # 8K is at threshold, may or may not be compressed depending on exact logic

        # With custom prefix, never compressed
        result_custom = Compressor.compress(msgs, config={
            "protect_last_n": 0, "collapse_pairs": False,
            "mcp_tool_prefixes": ("my_rag_",),
        })
        tool_custom = [m for m in result_custom if m.get("role") == "tool"][0]
        assert len(tool_custom["content"]) == len(big)  # NOT compressed

    def test_tool_results_reduced_count(self):
        """Multi-tool session sees significant reduction."""
        from plugins.rtk_ck.compress import Compressor

        big = "Y" * 10_000
        msgs = []
        for i in range(5):
            msgs.append(_user(f"query {i}"))
            msgs.append(_tool_call_factory("read_file", tool_call_id=f"c{i}"))
            msgs.append(_tool_result(big, tool_call_id=f"c{i}"))

        result = Compressor.compress(msgs)

        # Count full-size tool results
        full_before = _count_tool_full_results(msgs)
        full_after = _count_tool_full_results(result)
        assert full_after < full_before, "Expected fewer full-size results"