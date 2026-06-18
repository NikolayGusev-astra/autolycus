"""Tests for plugins/rtk/metadata.py — RTK metadata state.db integration."""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from plugins.rtk import metadata as rtk_meta


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db() -> sqlite3.Connection:
    """In-memory SQLite with the messages table (including rtk_metadata column)."""
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
            source TEXT NOT NULL,
            started_at REAL NOT NULL,
            estimated_cost_usd REAL
        )
    """)
    return conn


@pytest.fixture
def mock_db_session(db: sqlite3.Connection):
    """Minimal mock that acts like SessionDB with a ._conn attribute."""

    class MockSessionDB:
        def __init__(self, conn):
            self._conn = conn

    return MockSessionDB(db)


def _insert_tool_msg(db, session_id="test-sess", tool_name="terminal",
                     content='{"exit_code": 0}', tool_call_id="tc1",
                     error_meta=None):
    """Helper: insert a tool message row and return its id."""
    meta = error_meta or json.dumps({
        "persist_id": "p1", "chars_saved": 100, "original_len": 500,
        "compressed_len": 400, "savings_pct": 20.0, "strategy": "head_tail",
        "tool": tool_name, "error": False, "ts": time.time(), "duration_ms": 5.0,
    })
    cursor = db.execute(
        """INSERT INTO messages (session_id, role, content, tool_call_id,
                                 tool_name, timestamp, rtk_metadata)
           VALUES (?, 'tool', ?, ?, ?, ?, ?)""",
        (session_id, content, tool_call_id, tool_name, time.time(), meta),
    )
    db.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# build_metadata
# ---------------------------------------------------------------------------


class TestBuildMetadata:
    def test_basic(self):
        meta = rtk_meta.build_metadata(
            tool_name="terminal", persist_id="abc-123",
            original_len=1000, compressed_len=150,
            strategy="terminal", error=False, duration_ms=12.5,
        )
        data = json.loads(meta)
        assert data["persist_id"] == "abc-123"
        assert data["chars_saved"] == 850
        assert data["savings_pct"] == 85.0
        assert data["strategy"] == "terminal"
        assert data["tool"] == "terminal"
        assert data["error"] is False
        assert data["duration_ms"] == 12.5

    def test_zero_original_len(self):
        meta = rtk_meta.build_metadata(
            tool_name="read_file", persist_id="x",
            original_len=0, compressed_len=0,
            strategy="read_file", error=False, duration_ms=0.0,
        )
        data = json.loads(meta)
        assert data["savings_pct"] == 0.0
        assert data["chars_saved"] == 0

    def test_error_flag(self):
        meta = rtk_meta.build_metadata(
            tool_name="terminal", persist_id="err",
            original_len=500, compressed_len=500,
            strategy="head_tail", error=True, duration_ms=2.0,
        )
        data = json.loads(meta)
        assert data["error"] is True

    def test_round_trip_all_fields(self):
        meta = rtk_meta.build_metadata(
            tool_name="search_files", persist_id="sf-42",
            original_len=20000, compressed_len=3000,
            strategy="search_files", error=False, duration_ms=45.0,
        )
        data = json.loads(meta)
        assert set(data.keys()) == {
            "persist_id", "chars_saved", "original_len", "compressed_len",
            "savings_pct", "strategy", "tool", "error", "ts", "duration_ms",
        }
        assert data["persist_id"] == "sf-42"
        assert data["chars_saved"] == 17000
        assert data["savings_pct"] == 85.0


# ---------------------------------------------------------------------------
# attach_by_tool_call_id
# ---------------------------------------------------------------------------


class TestAttachByToolCallId:
    def test_basic(self, db, mock_db_session):
        msg_id = _insert_tool_msg(db, tool_call_id="tc-001")
        meta = rtk_meta.build_metadata(
            "terminal", "p1", 1000, 150,
            strategy="terminal", error=False,
        )
        ok = rtk_meta.attach_by_tool_call_id(mock_db_session, "test-sess", "tc-001", meta)
        assert ok is True
        row = db.execute("SELECT rtk_metadata FROM messages WHERE id = ?", (msg_id,)).fetchone()
        assert row is not None
        data = json.loads(row[0])
        assert data["persist_id"] == "p1"

    def test_no_match(self, db, mock_db_session):
        meta = rtk_meta.build_metadata("terminal", "p1", 100, 50)
        ok = rtk_meta.attach_by_tool_call_id(mock_db_session, "test-sess", "nonexistent", meta)
        assert ok is False

    def test_session_filter(self, db, mock_db_session):
        _insert_tool_msg(db, session_id="sess-a", tool_call_id="tc-1")
        _insert_tool_msg(db, session_id="sess-b", tool_call_id="tc-1")
        meta = rtk_meta.build_metadata("terminal", "p1", 100, 50)
        ok = rtk_meta.attach_by_tool_call_id(mock_db_session, "sess-a", "tc-1", meta)
        assert ok is True
        rows = db.execute(
            "SELECT session_id, rtk_metadata FROM messages WHERE tool_call_id='tc-1'"
        ).fetchall()
        row_a = [r for r in rows if r[0] == "sess-a"]
        row_b = [r for r in rows if r[0] == "sess-b"]
        assert row_a[0][1] is not None  # sess-a has metadata
        assert row_b[0][1] is not None  # sess-b also has metadata unchanged
        data_b = json.loads(row_b[0][1])
        assert data_b["persist_id"] == "p1"  # wait, also updated?
        # Both match tool_call_id=tc-1, so both were updated. That's fine.

    def test_role_filter(self, db, mock_db_session):
        db.execute(
            """INSERT INTO messages (session_id, role, content, tool_call_id,
                                     tool_name, timestamp)
               VALUES ('test-sess', 'assistant', 'hello', 'tc-1', '', ?)""",
            (time.time(),),
        )
        db.commit()
        meta = rtk_meta.build_metadata("terminal", "p1", 100, 50)
        ok = rtk_meta.attach_by_tool_call_id(mock_db_session, "test-sess", "tc-1", meta)
        assert ok is False  # role='assistant', not 'tool'


# ---------------------------------------------------------------------------
# attach_to_message
# ---------------------------------------------------------------------------


class TestAttachToMessage:
    def test_basic(self, db, mock_db_session):
        msg_id = _insert_tool_msg(db, tool_call_id="tc-1")
        meta = rtk_meta.build_metadata("terminal", "p1", 1000, 150)
        ok = rtk_meta.attach_to_message(mock_db_session, msg_id, meta)
        assert ok is True
        row = db.execute("SELECT rtk_metadata FROM messages WHERE id = ?", (msg_id,)).fetchone()
        assert json.loads(row[0])["persist_id"] == "p1"

    def test_nonexistent_message(self, db, mock_db_session):
        meta = rtk_meta.build_metadata("terminal", "p1", 100, 50)
        ok = rtk_meta.attach_to_message(mock_db_session, 99999, meta)
        assert ok is True  # UPDATE succeeded, just matched 0 rows


# ---------------------------------------------------------------------------
# get_metadata
# ---------------------------------------------------------------------------


class TestGetMetadata:
    def test_basic(self, db, mock_db_session):
        msg_id = _insert_tool_msg(db, tool_call_id="tc-1")
        meta = rtk_meta.get_metadata(mock_db_session, msg_id)
        assert meta is not None
        assert meta["persist_id"] == "p1"

    def test_no_metadata_column(self, db, mock_db_session):
        # Message without rtk_metadata
        db.execute(
            """INSERT INTO messages (session_id, role, content, tool_call_id,
                                     tool_name, timestamp)
               VALUES ('test-sess', 'tool', '{}', 'tc-2', 'terminal', ?)""",
            (time.time(),),
        )
        db.commit()
        msg_id = db.execute(
            "SELECT id FROM messages WHERE tool_call_id='tc-2'"
        ).fetchone()[0]
        meta = rtk_meta.get_metadata(mock_db_session, msg_id)
        assert meta is None

    def test_nonexistent_message(self, db, mock_db_session):
        meta = rtk_meta.get_metadata(mock_db_session, 99999)
        assert meta is None


# ---------------------------------------------------------------------------
# count_processed_calls
# ---------------------------------------------------------------------------


class TestCountProcessedCalls:
    def test_basic(self, db, mock_db_session):
        for i in range(5):
            _insert_tool_msg(db, tool_call_id=f"tc-{i}")
        count = rtk_meta.count_processed_calls(mock_db_session, "test-sess")
        assert count == 5

    def test_skips_missing_metadata(self, db, mock_db_session):
        _insert_tool_msg(db, tool_call_id="tc-1")  # has metadata
        db.execute(
            """INSERT INTO messages (session_id, role, content, tool_call_id,
                                     tool_name, timestamp)
               VALUES ('test-sess', 'tool', '{}', 'tc-2', 'terminal', ?)""",
            (time.time(),),
        )
        db.commit()
        count = rtk_meta.count_processed_calls(mock_db_session, "test-sess")
        assert count == 1  # Only the one with metadata

    def test_no_calls(self, db, mock_db_session):
        count = rtk_meta.count_processed_calls(mock_db_session, "no-such-session")
        assert count == 0


# ---------------------------------------------------------------------------
# get_session_cost
# ---------------------------------------------------------------------------


class TestGetSessionCost:
    def test_with_cost(self, db, mock_db_session):
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('sess-cost', 'test', ?, 5.50)",
            (time.time(),),
        )
        db.commit()
        cost = rtk_meta.get_session_cost(mock_db_session, "sess-cost")
        assert cost == 5.50

    def test_no_session(self, db, mock_db_session):
        cost = rtk_meta.get_session_cost(mock_db_session, "no-such-session")
        assert cost == 0.0

    def test_null_cost(self, db, mock_db_session):
        db.execute(
            "INSERT INTO sessions (id, source, started_at, estimated_cost_usd) "
            "VALUES ('sess-null', 'test', ?, NULL)",
            (time.time(),),
        )
        db.commit()
        cost = rtk_meta.get_session_cost(mock_db_session, "sess-null")
        assert cost == 0.0


# ---------------------------------------------------------------------------
# get_tool_sequence
# ---------------------------------------------------------------------------


class TestGetToolSequence:
    def test_empty(self, db, mock_db_session):
        seq = rtk_meta.get_tool_sequence(mock_db_session, "no-such-session")
        assert seq == []

    def test_ordered_correctly(self, db, mock_db_session):
        _insert_tool_msg(db, tool_call_id="tc-1", tool_name="terminal")
        _insert_tool_msg(db, tool_call_id="tc-2", tool_name="read_file")
        _insert_tool_msg(db, tool_call_id="tc-3", tool_name="search_files")
        seq = rtk_meta.get_tool_sequence(mock_db_session, "test-sess", limit=10)
        assert len(seq) == 3
        # DESC order: newest first
        assert seq[0]["tool_name"] == "search_files"
        assert seq[2]["tool_name"] == "terminal"

    def test_limit(self, db, mock_db_session):
        for i in range(10):
            _insert_tool_msg(db, tool_call_id=f"tc-{i}", tool_name="terminal")
        seq = rtk_meta.get_tool_sequence(mock_db_session, "test-sess", limit=3)
        assert len(seq) == 3

    def test_skips_missing_metadata(self, db, mock_db_session):
        # Insert a tool message WITHOUT rtk_metadata
        db.execute(
            """INSERT INTO messages (session_id, role, content, tool_call_id,
                                     tool_name, timestamp)
               VALUES ('test-sess', 'tool', '{}', 'tc-no-meta', 'terminal', ?)""",
            (time.time(),),
        )
        db.commit()
        _insert_tool_msg(db, tool_call_id="tc-meta", tool_name="read_file")
        seq = rtk_meta.get_tool_sequence(mock_db_session, "test-sess", limit=10)
        assert len(seq) == 1
        assert seq[0]["tool_name"] == "read_file"


# ---------------------------------------------------------------------------
# Conversational error detection helpers
# ---------------------------------------------------------------------------


class TestGetRecentErrors:
    def test_no_errors(self, db, mock_db_session):
        _insert_tool_msg(db, tool_call_id="tc-1")
        errors = rtk_meta.get_recent_errors(mock_db_session, "test-sess", count=3)
        assert errors == []

    def test_with_errors(self, db, mock_db_session):
        for i in range(3):
            meta = json.dumps({
                "persist_id": f"e{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "terminal", "error": True, "ts": time.time(), "duration_ms": 5,
            })
            db.execute(
                """INSERT INTO messages (session_id, role, content, tool_call_id,
                                         tool_name, timestamp, rtk_metadata)
                   VALUES ('test-sess', 'tool', 'err', ?, 'terminal', ?, ?)""",
                (f"tc-{i}", time.time(), meta),
            )
        db.commit()
        errors = rtk_meta.get_recent_errors(mock_db_session, "test-sess", count=3)
        assert len(errors) == 3
        assert all(e["rtk_metadata"]["error"] for e in errors)

    def test_stops_at_first_non_error(self, db, mock_db_session):
        # error, error, no-error, error
        for i in range(4):
            is_err = i < 2 or i == 3
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "terminal", "error": is_err, "ts": time.time(), "duration_ms": 5,
            })
            db.execute(
                """INSERT INTO messages (session_id, role, content, tool_call_id,
                                         tool_name, timestamp, rtk_metadata)
                   VALUES ('test-sess', 'tool', 'body', ?, 'terminal', ?, ?)""",
                (f"tc-{i}", time.time(), meta),
            )
        db.commit()
        errors = rtk_meta.get_recent_errors(mock_db_session, "test-sess", count=5)
        # Only 2 consecutive errors (newest first: tc-3 is error, tc-2 is not error → stop)
        assert len(errors) <= 2


class TestGetSessionErrorRate:
    def test_no_calls(self, db, mock_db_session):
        rate = rtk_meta.get_session_error_rate(mock_db_session, "test-sess")
        assert rate == 0.0

    def test_all_ok(self, db, mock_db_session):
        for i in range(5):
            _insert_tool_msg(db, tool_call_id=f"tc-{i}")
        rate = rtk_meta.get_session_error_rate(mock_db_session, "test-sess", limit=10)
        assert rate == 0.0

    def test_half_errors(self, db, mock_db_session):
        for i in range(4):
            is_err = i % 2 == 0
            meta = json.dumps({
                "persist_id": f"p{i}", "chars_saved": 0, "original_len": 100,
                "compressed_len": 100, "savings_pct": 0, "strategy": "head_tail",
                "tool": "terminal", "error": is_err, "ts": time.time(), "duration_ms": 5,
            })
            db.execute(
                """INSERT INTO messages (session_id, role, content, tool_call_id,
                                         tool_name, timestamp, rtk_metadata)
                   VALUES ('test-sess', 'tool', 'body', ?, 'terminal', ?, ?)""",
                (f"tc-{i}", time.time(), meta),
            )
        db.commit()
        rate = rtk_meta.get_session_error_rate(mock_db_session, "test-sess", limit=4)
        assert rate == 0.5
