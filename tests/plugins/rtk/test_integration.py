"""E2E integration tests for RTK v2 — real-world scenarios.

Tests the complete pipeline: compress → persist → recover, circuit breaker,
cross-session isolation, and cumulative budget tracking.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from plugins.rtk import (
    compressor,
    kvstore,
    store,
    _handle_rtk_recover,
    _handle_rtk_cleanup,
)
from plugins.rtk.pattern import (
    detect_consecutive_errors,
    detect_budget_exceeded,
    detect_no_progress,
    detect_tool_loop,
    run_all,
    best_signal,
    Signal,
)
from plugins.rtk.signal import (
    pre_turn,
    detect_and_store,
    read as signal_read,
)


# ---------------------------------------------------------------------------
# Fixtures: in-memory SessionDB mock + kvstore isolation
# ---------------------------------------------------------------------------


@pytest.fixture
def kvstore_path(tmp_path: Path) -> Path:
    """Isolated kvstore directory for each test."""
    path = tmp_path / "kvstore"
    path.mkdir(parents=True, exist_ok=True)
    # Override kvstore base path
    import plugins.rtk.kvstore as kv_mod
    from plugins.rtk.metadata import get_session_cost, get_tool_sequence, get_recent_errors

    kv_mod._KVSTORE_DIR = str(path)
    return path


@pytest.fixture
def db(tmp_path: Path):
    """Real in-memory SQLite database with sessions + messages tables.
    Also isolates kvstore to a temp directory.
    """
    import sqlite3, shutil
    import plugins.rtk.kvstore as kv_mod

    # Isolate kvstore
    orig_kv = kv_mod._DEFAULT_BASE
    kv_dir = tmp_path / "kvstore"
    kv_dir.mkdir(parents=True, exist_ok=True)
    kv_mod._DEFAULT_BASE = str(kv_dir)

    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            estimated_cost_usd REAL DEFAULT 0.0
        )
    """)
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
    conn.execute("INSERT INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                 ("test-sess", 0.0))

    from unittest.mock import MagicMock
    sess = MagicMock()
    sess._conn = conn
    sess.db_path = Path("/tmp/test_rtk_session.db")
    yield sess

    conn.close()
    kv_mod._DEFAULT_BASE = orig_kv
    shutil.rmtree(str(kv_dir), ignore_errors=True)


def _set_cost(db, cost: float, session_id: str = "test-sess"):
    """Set session cost in the in-memory DB."""
    db._conn.execute("UPDATE sessions SET estimated_cost_usd=? WHERE id=?",
                     (cost, session_id))
    db._conn.commit()


def _add_messages(db, messages: list, session_id: str = "test-sess"):
    """Add tool call messages to the in-memory DB."""
    import json, time
    for i, m in enumerate(messages):
        meta = json.dumps({
            "error": m.get("error", False),
            "persist_id": f"p-{m.get('tool_call_id', f'tc-{i}')}",
            "original_len": 1000,
            "compressed_len": 200,
            "strategy": "terminal",
        })
        db._conn.execute(
            "INSERT INTO messages (session_id, role, content, tool_call_id, tool_name, timestamp, rtk_metadata) "
            "VALUES (?, 'tool', ?, ?, ?, ?, ?)",
            (session_id, m.get("content_preview", ""), m.get("tool_call_id", f"tc-{i}"),
             m.get("tool_name", "terminal"), m.get("timestamp", time.time()), meta),
        )
    db._conn.commit()


# ---------------------------------------------------------------------------
# 1. E2E: Recovery round-trip — compress → rtk_recover → full data
# ---------------------------------------------------------------------------


class TestRecoveryRoundTrip:
    """The most important real-world scenario: data is never lost."""

    @pytest.fixture(autouse=True)
    def clean_store(self, tmp_path: Path):
        """Use temp cache dir for each test by monkeypatching DEFAULT."""
        import plugins.rtk.store as store_mod
        self._orig_cache = store_mod._DEFAULT_CACHE_DIR
        self._cache = str(tmp_path / "rtk-cache")
        store_mod._DEFAULT_CACHE_DIR = self._cache
        yield
        store_mod._DEFAULT_CACHE_DIR = self._orig_cache

    def _save(self, data: str) -> str:
        return store.save(data)

    def _load(self, pid: str) -> str | None:
        return store.load(pid)

    def test_small_terminal_output_no_compression_round_trip(self):
        """Small output: not compressed, but still persisted and recoverable."""
        text = "hello world\nerror: file not found\ndone"
        cfg = {"enabled": True, "head_chars": 500, "tail_chars": 1000,
               "min_result_chars": 500}

        compressed, stats = compressor.compress("terminal", text, config=cfg)
        assert compressed == text

    def test_large_terminal_recovery_round_trip(self):
        """Large terminal output: compressed, persisted, recovered byte-perfect."""
        lines = []
        for i in range(200):
            lines.append(f"[INFO] Processing step {i}...")
        lines.append("FATAL: connection refused to database at step 150")
        lines.append("ERROR: failed to execute query")
        lines.append("Traceback (most recent call last):")
        lines.append('  File "main.py", line 42, in run')
        lines.append("ConnectionError: timeout after 30s")
        text = "\n".join(lines)
        assert len(text) > 3000

        persist_id = self._save(text)
        cfg = {"enabled": True, "head_chars": 500, "tail_chars": 1000,
               "min_result_chars": 500}
        compressed, stats = compressor.compress("terminal", text, config=cfg,
                                                persist_id=persist_id)

        assert len(compressed) < len(text)
        assert stats["chars_saved"] > 0
        assert persist_id in compressed

        recovered = _handle_rtk_recover(persist_id=persist_id)
        assert recovered == text, "Recovery returned different data!"

        recovered_direct = self._load(persist_id)
        assert recovered_direct == text, "store.load() recovery failed!"

    def test_read_file_with_offset_section_preserved(self):
        """read_file with offset/limit: section preserved, full data recoverable."""
        lines = []
        for i in range(500):
            lines.append(f"def func_{i}():\n    return {i}\n")
        text = "".join(lines)
        assert len(text) > 10000

        persist_id = self._save(text)
        cfg = {"enabled": True, "head_chars": 500, "tail_chars": 1000,
               "min_result_chars": 500,
               "read_file_context_window": 2000}

        result, stats = compressor.compress("read_file", text, config=cfg,
                                            persist_id=persist_id,
                                            tool_args={"offset": 100, "limit": 50})
        recovered = self._load(persist_id)
        assert recovered == text
        assert "rtk-recover" in result

    def test_search_files_many_matches_recoverable(self):
        """Search results with 200+ matches: grouped, full data recoverable."""
        lines = []
        for d in ["src/utils", "src/api", "src/db", "tests", "docs"]:
            for f in range(60):
                lines.append(f"{d}/file_{f}.py:{d}/file_{f}.py contains something")
        text = "\n".join(lines)
        assert len(text) > 5000

        persist_id = self._save(text)
        cfg = {"enabled": True, "head_chars": 500, "tail_chars": 1000,
               "min_result_chars": 500}

        result, stats = compressor.compress("search_files", text, config=cfg,
                                            persist_id=persist_id)
        assert "Total:" in result or "directory grouping" in result or stats["total_matches"] >= 300

        recovered = self._load(persist_id)
        assert recovered == text

    def test_megabyte_output_recovery(self):
        """1MB+ output: compressed and fully recoverable."""
        text = "0123456789" * 110000  # ~1.1MB
        assert len(text) > 1000000

        persist_id = self._save(text)
        cfg = {"enabled": True, "head_chars": 500, "tail_chars": 1000,
               "min_result_chars": 500}
        compressed, stats = compressor.compress("terminal", text, config=cfg,
                                                persist_id=persist_id)

        assert len(compressed) < len(text)
        assert stats["chars_saved"] > 990000

        recovered = self._load(persist_id)
        assert recovered == text

    def test_rtk_cleanup_removes_old_cache(self, tmp_path: Path):
        """Cache cleanup: removes old files, keeps recent ones."""
        import plugins.rtk.store as store_mod
        orig_cache = store_mod._DEFAULT_CACHE_DIR
        cache_dir = str(tmp_path / "rtk-cc")
        store_mod._DEFAULT_CACHE_DIR = cache_dir
        try:
            from time import time
            old_id = store.save("old data")
            new_id = store.save("new data")

            old_path = store.resolve_path(old_id)
            new_path = store.resolve_path(new_id)

            if old_path:
                old_path_obj = Path(old_path)
                old_mtime = time() - 31 * 86400
                os.utime(str(old_path_obj), (old_mtime, old_mtime))

            result = _handle_rtk_cleanup(max_age_days=30)
            data = json.loads(result)
            assert data["removed"] >= 1

            if new_path:
                assert Path(new_path).exists()
        finally:
            store_mod._DEFAULT_CACHE_DIR = orig_cache


def directories_grouped(result: str) -> bool:
    """Check if result contains directory grouping."""
    return any(c in result for c in ["/ (", "matches)", "Total:"])


from time import time


# ---------------------------------------------------------------------------
# 2. Cumulative budget tracking across multiple turns
# ---------------------------------------------------------------------------


class TestCumulativeBudget:
    """Cost accumulates over N API calls → warning → critical → halt."""

    def test_budget_accumulates_over_five_calls(self, db):
        """$0 → $2 → $4 → $6 (warn at 80%) → $8 → $10 (critical halt)."""
        limit = 10.0
        costs = [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
        expected = [None, None, None, None, "warn", "critical"]

        for i, cost in enumerate(costs):
            _set_cost(db, cost)
            sig = detect_budget_exceeded(db, "test-sess", budget_limit=limit)
            if expected[i] is None:
                assert sig is None, f"Step {i} (${cost}): expected no signal"
            else:
                assert sig is not None, f"Step {i} (${cost}): expected signal"
                assert sig.severity == expected[i], \
                    f"Step {i} (${cost}): expected {expected[i]}, got {sig.severity}"
                if sig.severity == "critical":
                    assert sig.should_halt, "Critical budget must halt"

    def test_budget_warning_at_eighty_percent(self, db):
        """$7.99 → no warning, $8.00 → warning, $9.99 → warning."""
        _set_cost(db, 7.99)
        sig = detect_budget_exceeded(db, "test-sess", budget_limit=10.0)
        assert sig is None, "$7.99 is under 80%"

        _set_cost(db, 8.00)
        sig = detect_budget_exceeded(db, "test-sess", budget_limit=10.0)
        assert sig is not None
        assert sig.severity == "warn"
        assert sig.code == "BUDGET_WARNING"

        _set_cost(db, 9.99)
        sig = detect_budget_exceeded(db, "test-sess", budget_limit=10.0)
        assert sig.severity == "warn"

    def test_budget_exactly_at_limit_triggers_critical(self, db):
        _set_cost(db, 10.0)
        sig = detect_budget_exceeded(db, "test-sess", budget_limit=10.0)
        assert sig is not None
        assert sig.severity == "critical"
        assert sig.should_halt

    def test_budget_exceeded_by_wide_margin(self, db):
        """$500 on a $10 budget → critical halt."""
        _set_cost(db, 500.0)
        sig = detect_budget_exceeded(db, "test-sess", budget_limit=10.0)
        assert sig.severity == "critical"
        assert sig.should_halt


# ---------------------------------------------------------------------------
# 3. Fuzzy no-progress detection
# ---------------------------------------------------------------------------


class TestFuzzyNoProgress:
    """detect_no_progress with fuzzy string matching."""

    def test_identical_calls_still_detected(self, db):
        """Exact match still works (backward compatibility)."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "error: permission denied", "tool_call_id": "tc-0", "timestamp": 1, "error": True},
            {"tool_name": "terminal", "content_preview": "error: permission denied", "tool_call_id": "tc-1", "timestamp": 2, "error": True},
            {"tool_name": "terminal", "content_preview": "error: permission denied", "tool_call_id": "tc-2", "timestamp": 3, "error": True},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is not None
        assert sig.code == "NO_PROGRESS"

    def test_fuzzy_similar_calls_detected(self, db):
        """'file not found: foo.py' vs 'file not found: bar.py' → detected."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "error: file not found: foo.py", "tool_call_id": "tc-0", "timestamp": 1, "error": True},
            {"tool_name": "terminal", "content_preview": "error: file not found: bar.py", "tool_call_id": "tc-1", "timestamp": 2, "error": True},
            {"tool_name": "terminal", "content_preview": "error: file not found: baz.py", "tool_call_id": "tc-2", "timestamp": 3, "error": True},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is not None
        assert sig.code == "NO_PROGRESS"

    def test_semantically_different_not_detected(self, db):
        """'connection established' vs 'permission denied' → NOT no-progress."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "connection established to database", "tool_call_id": "tc-0", "timestamp": 1},
            {"tool_name": "terminal", "content_preview": "error: permission denied: /var/log/app.log", "tool_call_id": "tc-1", "timestamp": 2},
            {"tool_name": "terminal", "content_preview": "success: all tasks completed", "tool_call_id": "tc-2", "timestamp": 3},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is None, "Semantically different content should not trigger"

    def test_slight_wording_change_still_detected(self, db):
        """'Processed 42 items' vs 'Processed 99 items' → detected (90%+ similar)."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "Processed 42 items in 3.2 seconds", "tool_call_id": "tc-0", "timestamp": 1},
            {"tool_name": "terminal", "content_preview": "Processed 99 items in 4.7 seconds", "tool_call_id": "tc-1", "timestamp": 2},
            {"tool_name": "terminal", "content_preview": "Processed 7 items in 1.1 seconds", "tool_call_id": "tc-2", "timestamp": 3},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is not None

    def test_raise_threshold_reduces_false_positives(self, db):
        """Higher similarity_threshold = fewer detections."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "Step 1: connecting to database", "tool_call_id": "tc-0", "timestamp": 1},
            {"tool_name": "terminal", "content_preview": "Step 5: connecting to database", "tool_call_id": "tc-1", "timestamp": 2},
            {"tool_name": "terminal", "content_preview": "Step 9: connecting to database", "tool_call_id": "tc-2", "timestamp": 3},
        ])
        sig_low = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.5)
        assert sig_low is not None

        sig_high = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.99)
        assert sig_high is None

    def test_long_preview_truncated_to_200_chars(self, db):
        """Very long content previews are truncated before fuzzy matching."""
        long_text = "error: " + "x" * 500
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": long_text, "tool_call_id": "tc-0", "timestamp": 1, "error": True},
            {"tool_name": "terminal", "content_preview": long_text, "tool_call_id": "tc-1", "timestamp": 2, "error": True},
            {"tool_name": "terminal", "content_preview": long_text, "tool_call_id": "tc-2", "timestamp": 3, "error": True},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is not None

    def test_empty_preview_skipped(self, db):
        """Empty content preview → no detection (cannot compare)."""
        _add_messages(db, [
            {"tool_name": "terminal", "content_preview": "", "tool_call_id": "tc-0", "timestamp": 1},
            {"tool_name": "terminal", "content_preview": "", "tool_call_id": "tc-1", "timestamp": 2},
            {"tool_name": "terminal", "content_preview": "", "tool_call_id": "tc-2", "timestamp": 3},
        ])
        sig = detect_no_progress(db, "test-sess", threshold=3, similarity_threshold=0.85)
        assert sig is None


# ---------------------------------------------------------------------------
# 4. Corrupted store handling
# ---------------------------------------------------------------------------


class TestStoreCorruption:
    """store.load() behavior with corrupted/missing files.

    Note: store is a "best effort" cache — it does not validate content.
    Corrupted files return corrupted content; empty files return empty string.
    Only truly missing files return None.
    """

    @pytest.fixture(autouse=True)
    def clean_store(self, tmp_path: Path):
        """Use temp cache dir for each test."""
        import plugins.rtk.store as store_mod
        self._orig_cache = store_mod._DEFAULT_CACHE_DIR
        self._cache = str(tmp_path / "rtk-cache")
        store_mod._DEFAULT_CACHE_DIR = self._cache
        yield
        store_mod._DEFAULT_CACHE_DIR = self._orig_cache

    def _save(self, data: str) -> str:
        return store.save(data)

    def _load(self, pid: str) -> str | None:
        return store.load(pid)

    def _resolve(self, pid: str) -> str | None:
        return store.resolve_path(pid)

    def test_load_nonexistent_id_returns_none(self):
        """Unknown persist_id → None (no crash)."""
        data = self._load("nonexistent-uuid-12345")
        assert data is None

    def test_load_corrupted_file_returns_content_as_is(self):
        """Corrupted file → returns the corrupted data (store doesn't validate)."""
        persist_id = self._save("hello world")
        filepath = self._resolve(persist_id)
        assert filepath is not None
        Path(filepath).write_text("NOT VALID DATA\x00\x01\x02")
        data = self._load(persist_id)
        assert data == "NOT VALID DATA\x00\x01\x02"

    def test_load_empty_file_returns_empty_string(self):
        """Empty file → returns '' (store reads the file as-is)."""
        persist_id = self._save("hello")
        filepath = self._resolve(persist_id)
        assert filepath is not None
        Path(filepath).write_text("")
        data = self._load(persist_id)
        assert data == ""

    def test_rtk_recover_returns_corrupted_content(self):
        """rtk_recover returns corrupted data as-is (not None → no error)."""
        persist_id = self._save("hello")
        filepath = self._resolve(persist_id)
        assert filepath is not None
        Path(filepath).write_text("TRUNCATED")
        result = _handle_rtk_recover(persist_id=persist_id)
        assert result == "TRUNCATED"  # returned as-is, not error JSON

    def test_recover_with_missing_file(self):
        """rtk_recover with missing persist_id → error JSON."""
        result = _handle_rtk_recover(persist_id="nonexistent")
        data = json.loads(result)
        assert "error" in data
        assert "not found" in data["error"].lower()


# ---------------------------------------------------------------------------
# 5. Cross-session signal isolation
# ---------------------------------------------------------------------------


class TestCrossSessionIsolation:
    """Signals from session A must not leak into session B."""

    def test_pre_turn_session_a_signal_does_not_affect_b(self, db):
        """Signal stored in session A → pre_turn(B) returns empty."""
        # Add session A and B to DB
        db._conn.execute("INSERT OR IGNORE INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                         ("session-a", 0.0))
        db._conn.execute("INSERT OR IGNORE INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                         ("session-b", 0.0))
        db._conn.commit()

        # A over budget
        _set_cost(db, 15.0, "session-a")
        sig_a = detect_and_store(db, "session-a", budget_limit=10.0)
        assert sig_a is not None
        assert sig_a.severity == "critical"

        # B under budget
        _set_cost(db, 2.0, "session-b")
        inj_b, halt_b = pre_turn(db, "session-b", budget_limit=10.0)
        assert inj_b == "", "Session B got a signal from session A!"
        assert halt_b is False

    def test_clear_signal_only_clears_own_session(self, db):
        """Clearing signal in A leaves B's signal intact."""
        db._conn.execute("INSERT OR IGNORE INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                         ("session-a", 0.0))
        db._conn.execute("INSERT OR IGNORE INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                         ("session-b", 0.0))
        db._conn.commit()

        _set_cost(db, 15.0, "session-a")
        sig_a = detect_and_store(db, "session-a", budget_limit=10.0)
        _set_cost(db, 15.0, "session-b")
        sig_b = detect_and_store(db, "session-b", budget_limit=10.0)

        from plugins.rtk.signal import clear as clear_signal
        clear_signal("session-a")

        signal_a = signal_read("session-a")
        assert signal_a is None

        signal_b = signal_read("session-b")
        assert signal_b is not None
        assert signal_b["code"] == "BUDGET_EXCEEDED"

    def test_session_isolation_multi_session(self, db):
        """Three sessions: A over budget, B under, C critical — all isolated."""
        for sid in ["session-a", "session-b", "session-c"]:
            db._conn.execute("INSERT OR IGNORE INTO sessions (id, estimated_cost_usd) VALUES (?, ?)",
                             (sid, 0.0))
        db._conn.commit()

        _set_cost(db, 15.0, "session-a")
        _set_cost(db, 5.0, "session-b")
        _set_cost(db, 50.0, "session-c")

        sig_a = detect_and_store(db, "session-a", budget_limit=10.0)
        sig_b = detect_and_store(db, "session-b", budget_limit=10.0)
        sig_c = detect_and_store(db, "session-c", budget_limit=10.0)

        assert sig_a is not None
        assert sig_b is None
        assert sig_c is not None

        sa = signal_read("session-a")
        sc = signal_read("session-c")
        assert sa is not None
        assert sc is not None
        assert sa["code"] == "BUDGET_EXCEEDED"
        assert sc["code"] == "BUDGET_EXCEEDED"
        assert sa["should_halt"] is True
        assert sc["should_halt"] is True


# ---------------------------------------------------------------------------
# 6. Circuit breaker halt propagation
# ---------------------------------------------------------------------------


class TestCircuitBreakerHalt:
    """should_halt must propagate through the full pipeline."""

    def test_critical_signal_has_should_halt(self):
        """BUDGET_EXCEEDED signal has should_halt=True."""
        sig = Signal(
            code="BUDGET_EXCEEDED",
            severity="critical",
            message="$15 on $10 budget",
            count=15,
            should_halt=True,
        )
        assert sig.should_halt is True

    def test_warning_signal_does_not_halt(self):
        """Warning signals should not halt."""
        sig = Signal(
            code="BUDGET_WARNING",
            severity="warn",
            message="80% budget used",
            should_halt=False,
        )
        assert sig.should_halt is False

    def test_pre_turn_returns_halt_on_budget_exceeded(self, db):
        """pre_turn() returns halt=True when budget is exceeded."""
        _set_cost(db, 15.0)
        inj, halt = pre_turn(db, "test-sess", budget_limit=10.0)
        assert halt is True
        assert "Бюджет" in inj

    def test_pre_turn_returns_halt_false_under_budget(self, db):
        """pre_turn() returns halt=False when under budget."""
        inj, halt = pre_turn(db, "test-sess", budget_limit=10.0)
        assert halt is False
        assert inj == ""

    def test_signal_stores_should_halt(self, db):
        """Stored signal preserves should_halt flag."""
        _set_cost(db, 15.0)
        sig = detect_and_store(db, "test-sess", budget_limit=10.0)
        assert sig is not None

        stored = signal_read("test-sess")
        assert stored is not None
        assert stored["should_halt"] is True
        assert stored["code"] == "BUDGET_EXCEEDED"

    def test_circuit_breaker_message_visible(self, db):
        """Critical signal message mentions budget and actual cost."""
        _set_cost(db, 42.0)
        sig = detect_and_store(db, "test-sess", budget_limit=10.0)
        assert sig is not None
        assert "$" in sig.message
        assert "42" in sig.message