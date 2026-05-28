"""Tests for RTK-CK Compressor pointer compression with RTK store.
"""
from __future__ import annotations

import tempfile
from unittest.mock import patch

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


class TestPointerCompression:
    """Pointer compression replaces large tool results with RTK store pointers."""

    def test_pointer_enabled_saves_to_store(self):
        """rtk_store_enabled=True → large tool result saved to RTK store with pointer."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [
            _user("read file"),
            _tool_call("read_file", tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": False,
            "rtk_store_enabled": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            config["rtk_cache_dir"] = tmp
            result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        # Should be a pointer, not the full content
        assert "RTK-CK" in content
        assert "pointer" in content
        assert len(content) < 200  # short pointer

    def test_pointer_content_stored_on_disk(self):
        """RTK store contains the original full content."""
        from plugins.rtk_ck.compress import Compressor
        from plugins.rtk.store import load as rtk_load

        big = "ORIGINAL_CONTENT_" * 500
        msgs = [
            _user("read file"),
            _tool_call("read_file", tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": False,
            "rtk_store_enabled": True,
        }

        persist_id = None
        with tempfile.TemporaryDirectory() as tmp:
            config["rtk_cache_dir"] = tmp
            result = Compressor.compress(msgs, config=config)

            # Extract persist_id from pointer text
            tool_msgs = [m for m in result if m.get("role") == "tool"]
            pointer_text = tool_msgs[0]["content"]
            # Pointer format: "<RTK-CK: pointer — id={uuid}>"
            assert "id=" in pointer_text
            persist_id = pointer_text.split("id=")[1].rstrip(">")

            # Verify original content is stored
            loaded = rtk_load(persist_id, cache_dir=tmp)
            assert loaded == big

    def test_pointer_disabled_falls_back_to_head_tail(self):
        """rtk_store_enabled=False → normal head/tail compression."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [
            _user("read file"),
            _tool_call("read_file", tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": False,
            "rtk_store_enabled": False,
        }
        result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        content = tool_msgs[0]["content"]
        # Head/tail compression, no RTK-CK pointer marker
        assert "<RTK-CK: pointer" not in content
        assert "XXX" in content  # head preserved

    def test_pointer_skipped_for_small_results(self):
        """Small tool results (≤5K) not saved to store."""
        from plugins.rtk_ck.compress import Compressor

        msgs = [
            _user("ping"),
            _tool_call("terminal", args='{"cmd":"ls"}', tid="c1"),
            _tool_result("ok", tid="c1", name="terminal"),
        ]
        config = {
            "rtk_store_enabled": True,
            "collapse_pairs": False,
            "protect_last_n": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            config["rtk_cache_dir"] = tmp
            result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert tool_msgs[0]["content"] == "ok"  # unchanged

    def test_pointer_store_unavailable_graceful(self):
        """RTK store import fails → graceful fallback to head/tail."""
        from plugins.rtk_ck.compress import Compressor

        big = "X" * 10_000
        msgs = [
            _user("read file"),
            _tool_call("read_file", tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": False,
            "rtk_store_enabled": True,
        }
        # Mock store to be unavailable (wrong import path)
        with patch("plugins.rtk_ck.compress._rtk_store_save", return_value=None):
            result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        # Falls back to head/tail compression
        assert len(content) < len(big)


class TestPointerWithCollapse:
    """Pointer compression and collapse interaction."""

    def test_pointer_with_collapse_disabled(self):
        """rtk_store_enabled + collapse_pairs=False → uses pointer."""
        from plugins.rtk_ck.compress import Compressor

        big = "DATA" * 2500
        msgs = [
            _user("read config"),
            _tool_call("read_file", args='{"path":"config.yaml"}', tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": False,
            "rtk_store_enabled": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            config["rtk_cache_dir"] = tmp
            result = Compressor.compress(msgs, config=config)

        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        assert "RTK-CK" in tool_msgs[0]["content"]

    def test_pointer_in_collapse_summary(self):
        """When both collapse and pointer are enabled, collapse takes priority."""
        from plugins.rtk_ck.compress import Compressor

        big = "DATA" * 2500
        msgs = [
            _user("read config"),
            _tool_call("read_file", args='{"path":"config.yaml"}', tid="c1"),
            _tool_result(big, tid="c1"),
        ]
        config = {
            "protect_last_n": 0,
            "collapse_pairs": True,
            "rtk_store_enabled": True,
        }
        with tempfile.TemporaryDirectory() as tmp:
            config["rtk_cache_dir"] = tmp
            result = Compressor.compress(msgs, config=config)

        # With collapse_pairs=True, the pair collapses to a summary
        tool_msgs = [m for m in result if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        content = tool_msgs[0]["content"]
        # Collapse produces a 1-line summary, much shorter than pointer
        assert content.startswith("[read_file(")
        assert "OK" in content or "Error" in content