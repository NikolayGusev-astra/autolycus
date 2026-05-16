"""Tests for plugins/rtk/signal.py — pre-turn signal injector."""

from __future__ import annotations

import json
import tempfile
import time

import pytest

from plugins.rtk import kvstore, pattern, signal
from plugins.rtk.pattern import Signal


@pytest.fixture(autouse=True)
def isolated_kvstore(monkeypatch):
    """Use a temp directory for kvstore to avoid test interference."""
    with tempfile.TemporaryDirectory() as tmp:
        monkeypatch.setattr(kvstore, "_DEFAULT_BASE", tmp)
        yield


# ---------------------------------------------------------------------------
# Signal.to_injection
# ---------------------------------------------------------------------------


class TestSignalInjection:
    def test_info_no_injection(self):
        sig = Signal(code="INFO", severity="info", message="Nothing", count=0)
        assert sig.to_injection() == ""

    def test_warn_injection(self):
        sig = Signal(code="TOOL_LOOP", severity="warn",
                     message="loop", count=3)
        text = sig.to_injection()
        assert "⚠" in text
        assert "RTK/TOOL_LOOP" in text
        assert "loop" in text

    def test_critical_injection(self):
        sig = Signal(code="BUDGET_EXCEEDED", severity="critical",
                     message="$15 spent", count=15)
        text = sig.to_injection()
        assert "🔴" in text
        assert "RTK/BUDGET_EXCEEDED" in text

    def test_bool(self):
        sig_warn = Signal(code="X", severity="warn", message="x")
        assert sig_warn  # __bool__ returns True for warn
        sig_crit = Signal(code="X", severity="critical", message="x")
        assert sig_crit  # __bool__ returns True for critical
        sig_info = Signal(code="X", severity="info", message="x")
        assert not sig_info  # __bool__ returns False for info

    def test_should_halt_critical(self):
        sig = Signal(code="BUDGET_EXCEEDED", severity="critical",
                     message="budget", should_halt=True)
        assert sig.should_halt is True
        assert sig.to_injection() != ""

    def test_should_halt_default(self):
        sig = Signal(code="NORMAL", severity="warn", message="normal")
        assert sig.should_halt is False


# ---------------------------------------------------------------------------
# store / clear / read / get_injection
# ---------------------------------------------------------------------------


class TestSignalStore:
    def test_store_and_read(self):
        sig = Signal(code="CONSECUTIVE_ERRORS", severity="critical",
                     message="3 errors", count=3)
        ok = signal.store("sess-1", sig)
        assert ok is True
        data = signal.read("sess-1")
        assert data["code"] == "CONSECUTIVE_ERRORS"
        assert data["severity"] == "critical"
        assert "3 errors" in data["injection"]

    def test_clear(self):
        signal.store("sess-1", Signal(code="X", severity="warn", message="x"))
        signal.clear("sess-1")
        assert signal.read("sess-1") is None

    def test_get_injection_one_shot(self):
        signal.store("sess-1", Signal(code="WARN", severity="warn",
                                       message="warning", count=1))
        # First read returns the injection
        inj1 = signal.get_injection("sess-1")
        assert "WARN" in inj1
        # Second read returns empty (auto-cleared)
        inj2 = signal.get_injection("sess-1")
        assert inj2 == ""

    def test_get_injection_no_signal(self):
        inj = signal.get_injection("no-such-session")
        assert inj == ""

    def test_overwrite(self):
        signal.store("sess-1", Signal(code="FIRST", severity="warn", message="a"))
        signal.store("sess-1", Signal(code="SECOND", severity="critical", message="b"))
        data = signal.read("sess-1")
        assert data["code"] == "SECOND"


# ---------------------------------------------------------------------------
# detect_and_store
# ---------------------------------------------------------------------------


class TestDetectAndStore:
    def test_no_detection_returns_none(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, rtk_metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT,
                started_at REAL, estimated_cost_usd REAL
            )
        """)
        # Clear any signals
        signal.clear("no-sess-1")
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db._conn = conn
        sig = signal.detect_and_store(mock_db, "no-sess-1")
        assert sig is None
        conn.close()

    def test_detection_creates_signal(self):
        import sqlite3, time
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, rtk_metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT,
                started_at REAL, estimated_cost_usd REAL
            )
        """)
        # Insert a session with high cost
        conn.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('budget-sess', 'test', ?, 15.0)",
            (time.time(),),
        )
        conn.commit()
        # Insert 3 consecutive errors
        meta = '{"error": true, "tool": "terminal", "persist_id": "p1"}'
        for i in range(3):
            conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, "
                "tool_name, timestamp, rtk_metadata) "
                "VALUES ('budget-sess', 'tool', 'err', ?, 'terminal', ?, ?)",
                (f"tc-{i}", time.time(), meta),
            )
        conn.commit()
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db._conn = conn
        sig = signal.detect_and_store(mock_db, "budget-sess", budget_limit=10.0)
        assert sig is not None
        assert sig.code in ("BUDGET_EXCEEDED", "CONSECUTIVE_ERRORS")
        # Check it was stored
        stored = signal.read("budget-sess")
        assert stored is not None
        assert stored["code"] == sig.code
        signal.clear("budget-sess")
        conn.close()


# ---------------------------------------------------------------------------
# pre_turn
# ---------------------------------------------------------------------------


class TestPreTurn:
    def test_existing_signal_returned(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, rtk_metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT,
                started_at REAL, estimated_cost_usd REAL
            )
        """)
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db._conn = conn

        # Store a signal first
        sig = Signal(code="TOOL_LOOP", severity="warn", message="loop detected", count=3)
        signal.store("pre-sess", sig)

        # pre_turn should return the existing signal without running detectors
        inj, halt = signal.pre_turn(mock_db, "pre-sess")
        assert "TOOL_LOOP" in inj
        assert "loop" in inj
        assert halt is False  # warn severity, no halt

        # Second call returns empty (one-shot cleared)
        inj2, halt2 = signal.pre_turn(mock_db, "pre-sess")
        assert inj2 == ""
        assert halt2 is False
        conn.close()

    def test_no_signal_returns_empty(self):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, rtk_metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT,
                started_at REAL, estimated_cost_usd REAL
            )
        """)
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db._conn = conn
        # No errors, no budget data
        inj, halt = signal.pre_turn(mock_db, "clean-sess")
        assert inj == ""
        assert halt is False
        conn.close()

    def test_halt_on_critical_signal(self):
        import sqlite3, time
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT, tool_call_id TEXT, tool_name TEXT,
                timestamp REAL NOT NULL, rtk_metadata TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT,
                started_at REAL, estimated_cost_usd REAL
            )
        """)
        # Create a session with high cost
        conn.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('halt-sess', 'test', ?, 15.0)",
            (time.time(),),
        )
        conn.commit()
        from unittest.mock import MagicMock
        mock_db = MagicMock()
        mock_db._conn = conn
        inj, halt = signal.pre_turn(mock_db, "halt-sess", budget_limit=10.0)
        assert inj != ""
        assert halt is True  # BUDGET_EXCEEDED is critical → should_halt=True
