"""Tests for plugins/rtk/pattern.py — semantic pattern detection."""

from __future__ import annotations

import json
import time

import pytest

from plugins.rtk import pattern


def _make_meta(error: bool = False, tool: str = "terminal") -> str:
    return json.dumps({
        "persist_id": "p", "chars_saved": 0, "original_len": 100,
        "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
        "tool": tool, "error": error, "ts": time.time(), "duration_ms": 5.0,
    })


def _insert_tool(db, session_id="test-sess", tool_name="terminal",
                  content='{"exit_code": 0}', tool_call_id="tc",
                  error=False):
    meta = _make_meta(error=error, tool=tool_name)
    db.execute(
        """INSERT INTO messages (session_id, role, content, tool_call_id,
                                 tool_name, timestamp, rtk_metadata)
           VALUES (?, 'tool', ?, ?, ?, ?, ?)""",
        (session_id, content, tool_call_id, tool_name, time.time(), meta),
    )
    db.commit()


# ---------------------------------------------------------------------------
# Fixtures: in-memory SQLite
# ---------------------------------------------------------------------------


@pytest.fixture
def db(request):
    conn = pytest.mark.skip("need_db_conn")(lambda: None)
    # We'll use a real SQLite connection via monkeypatching
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_name TEXT,
            timestamp REAL NOT NULL,
            rtk_metadata TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            started_at REAL,
            estimated_cost_usd REAL
        )
    """)
    yield conn
    conn.close()


@pytest.fixture
def sess(db):
    """Returns a minimal SessionDB-like object and the connection."""

    class MockDB:
        def __init__(self, conn):
            self._conn = conn

    return MockDB(db)


# ===========================================================================
# detect_consecutive_errors
# ===========================================================================


class TestConsecutiveErrors:
    def test_no_errors(self, db, sess):
        for i in range(3):
            _insert_tool(db, tool_call_id=f"tc-{i}")
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is None

    def test_3_consecutive_errors(self, db, sess):
        for i in range(3):
            _insert_tool(db, tool_call_id=f"tc-{i}", error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is not None
        assert sig.code == "CONSECUTIVE_ERRORS"
        assert sig.severity == "critical"
        assert sig.count == 3

    def test_2_errors_not_enough(self, db, sess):
        for i in range(2):
            _insert_tool(db, tool_call_id=f"tc-{i}", error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is None

    def test_error_then_success_then_error(self, db, sess):
        _insert_tool(db, tool_call_id="tc-1", error=True)
        _insert_tool(db, tool_call_id="tc-2", error=False)
        _insert_tool(db, tool_call_id="tc-3", error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is None  # not consecutive

    def test_different_tools(self, db, sess):
        """Different tools with errors = problem-solving, NOT a loop. No signal."""
        tools = ["terminal", "terminal", "read_file"]
        for i, t in enumerate(tools):
            _insert_tool(db, tool_call_id=f"tc-{i}", tool_name=t, error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        # Different tools with same error output = sequential problem-solving, not loop
        assert sig is None

    def test_same_tool_same_content_is_loop(self, db, sess):
        """Same tool + same error output 3 times = loop → should trigger."""
        for i in range(3):
            _insert_tool(db, tool_call_id=f"tc-{i}", tool_name="terminal",
                         content="permission denied", error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is not None
        assert sig.code == "CONSECUTIVE_ERRORS"
        assert sig.count == 3
        assert sig.detail["tool"] == "terminal"

    def test_same_tool_different_content_no_loop(self, db, sess):
        """Same tool but different error output = problem-solving, NOT loop."""
        contents = ["permission denied", "file not found", "timeout"]
        for i, c in enumerate(contents):
            _insert_tool(db, tool_call_id=f"tc-{i}", tool_name="terminal",
                         content=c, error=True)
        sig = pattern.detect_consecutive_errors(sess, "test-sess", threshold=3)
        assert sig is None


# ===========================================================================
# detect_tool_loop
# ===========================================================================


class TestToolLoop:
    def test_no_loop(self, db, sess):
        for i in range(6):
            _insert_tool(db, tool_call_id=f"tc-{i}", tool_name="terminal")
        sig = pattern.detect_tool_loop(sess, "test-sess", window=6)
        assert sig is None

    def test_cleat_loop(self, db, sess):
        # terminal: error, error, terminal: error, error, terminal: ok  
        for i in range(5):
            is_err = i < 4  # first 4 are errors
            _insert_tool(db, tool_call_id=f"tc-{i}", tool_name="terminal",
                         error=is_err)
        # 5 calls, 4 errors of the same tool
        sig = pattern.detect_tool_loop(sess, "test-sess", window=6)
        assert sig is not None
        assert sig.code == "TOOL_LOOP"

    def test_different_tools_no_loop(self, db, sess):
        for i in range(4):
            _insert_tool(db, tool_call_id=f"tc-{i}",
                         tool_name=["terminal", "read_file", "search_files", "terminal"][i],
                         error=True)
        sig = pattern.detect_tool_loop(sess, "test-sess", window=6)
        assert sig is None  # different tools, not a loop


# ===========================================================================
# detect_budget_exceeded
# ===========================================================================


class TestBudget:
    def test_under_budget(self, db, sess):
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('test-sess', 'test', ?, 5.0)",
            (time.time(),),
        )
        db.commit()
        sig = pattern.detect_budget_exceeded(sess, "test-sess", budget_limit=10.0)
        assert sig is None

    def test_over_budget(self, db, sess):
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('test-sess', 'test', ?, 15.0)",
            (time.time(),),
        )
        db.commit()
        sig = pattern.detect_budget_exceeded(sess, "test-sess", budget_limit=10.0)
        assert sig is not None
        assert sig.code == "BUDGET_EXCEEDED"
        assert sig.severity == "critical"

    def test_80_percent_warning(self, db, sess):
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('test-sess', 'test', ?, 8.5)",
            (time.time(),),
        )
        db.commit()
        sig = pattern.detect_budget_exceeded(sess, "test-sess", budget_limit=10.0)
        assert sig is not None
        assert sig.code == "BUDGET_WARNING"
        assert sig.severity == "warn"


# ===========================================================================
# run_all / best_signal
# ===========================================================================


class TestRunAll:
    def test_nothing_detected(self, db, sess):
        signals = pattern.run_all(sess, "no-such-session")
        assert signals == []

    def test_errors_and_budget(self, db, sess):
        # Save a session with high cost
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('test-sess', 'test', ?, 12.0)",
            (time.time(),),
        )
        db.commit()
        # 3 consecutive errors
        for i in range(3):
            _insert_tool(db, tool_call_id=f"tc-{i}", error=True)
        signals = pattern.run_all(sess, "test-sess", budget_limit=10.0)
        # Should have BUDGET_EXCEEDED (critical) and CONSECUTIVE_ERRORS (critical)
        codes = [s.code for s in signals]
        assert "BUDGET_EXCEEDED" in codes
        assert "CONSECUTIVE_ERRORS" in codes

    def test_detect_no_progress_inserts_null_metadata(self, db, sess):
        # Messages WITHOUT rtk_metadata should not crash detect_no_progress
        db.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, "
            "                     tool_name, timestamp, rtk_metadata) "
            "VALUES ('test-sess', 'tool', 'result', 'tc-1', 'read_file', ?, NULL)",
            (time.time(),),
        )
        db.commit()
        sig = pattern.detect_no_progress(sess, "test-sess", threshold=3)
        assert sig is None


# ===========================================================================
# detect_no_progress
# ===========================================================================


class TestNoProgress:
    def test_3_identical_calls_detected(self, db, sess):
        for i in range(3):
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "terminal", "error": False, "ts": time.time(), "duration_ms": 5.0,
            })
            db.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, "
                "                     tool_name, timestamp, rtk_metadata) "
                "VALUES ('test-sess', 'tool', 'permission denied', ?, 'read_file', ?, ?)",
                (f"tc-{i}", time.time(), meta),
            )
        db.commit()
        sig = pattern.detect_no_progress(sess, "test-sess", threshold=3)
        assert sig is not None
        assert sig.code == "NO_PROGRESS"
        assert sig.severity == "warn"

    def test_different_tools_no_signal(self, db, sess):
        tools = ["terminal", "read_file", "terminal"]
        for i, t in enumerate(tools):
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": t, "error": False, "ts": time.time(), "duration_ms": 5.0,
            })
            db.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, "
                "                     tool_name, timestamp, rtk_metadata) "
                "VALUES ('test-sess', 'tool', 'error text', ?, ?, ?, ?)",
                (f"tc-{i}", t, time.time(), meta),
            )
        db.commit()
        sig = pattern.detect_no_progress(sess, "test-sess", threshold=3)
        assert sig is None  # Different tools

    def test_different_text_no_signal(self, db, sess):
        texts = ["file not found", "file not found", "file loaded"]
        for i, txt in enumerate(texts):
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "read_file", "error": False, "ts": time.time(), "duration_ms": 5.0,
            })
            db.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, "
                "                     tool_name, timestamp, rtk_metadata) "
                "VALUES ('test-sess', 'tool', ?, ?, 'read_file', ?, ?)",
                (txt, f"tc-{i}", time.time(), meta),
            )
        db.commit()
        sig = pattern.detect_no_progress(sess, "test-sess", threshold=3)
        assert sig is None  # Third call shows different text

    def test_too_few_calls(self, db, sess):
        for i in range(2):
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "terminal", "error": False, "ts": time.time(), "duration_ms": 5.0,
            })
            db.execute(
                "INSERT INTO messages (session_id, role, content, tool_call_id, "
                "                     tool_name, timestamp, rtk_metadata) "
                "VALUES ('test-sess', 'tool', 'same', ?, 'terminal', ?, ?)",
                (f"tc-{i}", time.time(), meta),
            )
        db.commit()
        sig = pattern.detect_no_progress(sess, "test-sess", threshold=3)
        assert sig is None


    def test_est_signal_priority(self, db, sess):
        # Budget critical, errors warn
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('test-sess', 'test', ?, 15.0)",
            (time.time(),),
        )
        db.commit()
        for i in range(3):
            _insert_tool(db, tool_call_id=f"tc-{i}", error=True)
        best = pattern.best_signal(sess, "test-sess", budget_limit=10.0)
        assert best is not None
        assert best.severity == "critical"  # budget critical comes first
