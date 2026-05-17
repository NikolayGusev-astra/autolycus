"""Tests for plugins/rtk/kvstore.py — session-scoped key-value store."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from plugins.rtk import kvstore


@pytest.fixture
def base_dir():
    """Temporary directory for kvstore files."""
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


# ---------------------------------------------------------------------------
# put / get
# ---------------------------------------------------------------------------


class TestPutGet:
    def test_put_and_get(self, base_dir):
        ok = kvstore.put("sess-1", "claims", {"test": True}, base_dir=base_dir)
        assert ok is True
        data = kvstore.get("sess-1", "claims", base_dir=base_dir)
        assert data == {"test": True}

    def test_get_nonexistent(self, base_dir):
        data = kvstore.get("no-sess", "claims", base_dir=base_dir)
        assert data is None

    def test_get_nonexistent_key(self, base_dir):
        kvstore.put("sess-1", "claims", "x", base_dir=base_dir)
        data = kvstore.get("sess-1", "nonexistent", base_dir=base_dir)
        assert data is None

    def test_overwrite(self, base_dir):
        kvstore.put("sess-1", "signal", "first", base_dir=base_dir)
        kvstore.put("sess-1", "signal", "second", base_dir=base_dir)
        data = kvstore.get("sess-1", "signal", base_dir=base_dir)
        assert data == "second"

    def test_complex_types(self, base_dir):
        data = {
            "codes": ["A", "B"],
            "count": 42,
            "nested": {"key": "val"},
        }
        kvstore.put("sess-1", "flags", data, base_dir=base_dir)
        loaded = kvstore.get("sess-1", "flags", base_dir=base_dir)
        assert loaded == data

    def test_session_isolation(self, base_dir):
        kvstore.put("sess-a", "key", "val-a", base_dir=base_dir)
        kvstore.put("sess-b", "key", "val-b", base_dir=base_dir)
        assert kvstore.get("sess-a", "key", base_dir=base_dir) == "val-a"
        assert kvstore.get("sess-b", "key", base_dir=base_dir) == "val-b"


# ---------------------------------------------------------------------------
# delete / delete_session
# ---------------------------------------------------------------------------


class TestDelete:
    def test_delete_key(self, base_dir):
        kvstore.put("sess-1", "claims", "data", base_dir=base_dir)
        ok = kvstore.delete("sess-1", "claims", base_dir=base_dir)
        assert ok is True
        assert kvstore.get("sess-1", "claims", base_dir=base_dir) is None

    def test_delete_nonexistent(self, base_dir):
        ok = kvstore.delete("sess-1", "nothing", base_dir=base_dir)
        assert ok is True  # no-op, not an error

    def test_delete_session(self, base_dir):
        kvstore.put("sess-1", "claims", "x", base_dir=base_dir)
        kvstore.put("sess-1", "flags", "y", base_dir=base_dir)
        ok = kvstore.delete_session("sess-1", base_dir=base_dir)
        assert ok is True
        assert kvstore.get("sess-1", "claims", base_dir=base_dir) is None
        assert kvstore.get("sess-1", "flags", base_dir=base_dir) is None


# ---------------------------------------------------------------------------
# list_keys / list_sessions
# ---------------------------------------------------------------------------


class TestList:
    def test_list_keys(self, base_dir):
        kvstore.put("sess-1", "claims", "x", base_dir=base_dir)
        kvstore.put("sess-1", "flags", "y", base_dir=base_dir)
        keys = kvstore.list_keys("sess-1", base_dir=base_dir)
        assert sorted(keys) == ["claims", "flags"]

    def test_list_keys_empty(self, base_dir):
        keys = kvstore.list_keys("no-sess", base_dir=base_dir)
        assert keys == []

    def test_list_sessions(self, base_dir):
        kvstore.put("sess-a", "k", "v", base_dir=base_dir)
        kvstore.put("sess-b", "k", "v", base_dir=base_dir)
        sessions = kvstore.list_sessions(base_dir=base_dir)
        assert sorted(sessions) == ["sess-a", "sess-b"]


# ---------------------------------------------------------------------------
# get_usage_budget
# ---------------------------------------------------------------------------


class TestUsageBudget:
    def test_no_usage(self, base_dir):
        budget = kvstore.get_usage_budget("sess-1", base_dir=base_dir)
        assert budget == {"spent": 0.0, "budget": 0.0, "remaining": 0.0}

    def test_with_usage(self, base_dir):
        kvstore.put("sess-1", "usage", {"budget": 10.0, "spent": 3.5}, base_dir=base_dir)
        budget = kvstore.get_usage_budget("sess-1", base_dir=base_dir)
        assert budget["spent"] == 3.5
        assert budget["budget"] == 10.0
        assert budget["remaining"] == 6.5

    def test_over_budget(self, base_dir):
        kvstore.put("sess-1", "usage", {"budget": 5.0, "spent": 12.0}, base_dir=base_dir)
        budget = kvstore.get_usage_budget("sess-1", base_dir=base_dir)
        assert budget["remaining"] == 0.0  # floor at 0
