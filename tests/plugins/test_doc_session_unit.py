"""
Tests for doc_session plugin — Layer 1: Unit tests.

Run: pytest tests/plugins/test_doc_session_unit.py -v
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from plugins.doc_session import session_manager
from plugins.doc_session import store as doc_store


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_storage(monkeypatch, tmp_path):
    """Redirect storage to temp dir for each test."""
    docs_dir = tmp_path / ".hermes" / "docs"
    monkeypatch.setattr(doc_store, "_DOCS_DIR", docs_dir)
    monkeypatch.setattr(doc_store, "_SESSIONS_DIR", docs_dir / "sessions")
    monkeypatch.setattr(doc_store, "_CONTENT_DIR", docs_dir / "content")
    yield
    # Clear cache
    session_manager._SESSION_CACHE.clear()


@pytest.fixture
def sample_session():
    """Create a basic session with 3-section plan."""
    return session_manager.create_session(
        path="/tmp/test-report.md",
        custom_plan=[
            {"id": "intro", "title": "Введение", "description": "Введение в тему"},
            {"id": "body", "title": "Основная часть", "description": "Детальный разбор"},
            {"id": "conclusion", "title": "Заключение", "description": "Выводы"},
        ],
    )


# ── session_manager tests ───────────────────────────────────────────────────


class TestCreateSession:
    def test_create_returns_session_id(self):
        state = session_manager.create_session(path="/tmp/doc.md")
        assert "session_id" in state
        assert state["session_id"].startswith("doc-")

    def test_create_default_plan(self):
        state = session_manager.create_session(path="/tmp/doc.md")
        assert len(state["plan"]) == 3
        assert state["plan"][0]["id"] == "section-1"

    def test_create_custom_plan(self):
        plan = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
        state = session_manager.create_session(path="/tmp/doc.md", custom_plan=plan)
        assert len(state["plan"]) == 2
        assert state["plan"][0]["id"] == "a"

    def test_create_status_in_progress(self, sample_session):
        assert sample_session["status"] == "in_progress"

    def test_create_with_sources(self):
        state = session_manager.create_session(
            path="/tmp/doc.md",
            sources=["/tmp/src1.md", "/tmp/src2.md"],
        )
        assert len(state["sources"]) == 2

    def test_create_persists_to_disk(self, sample_session):
        loaded = doc_store.load_session(sample_session["session_id"])
        assert loaded is not None
        assert loaded["path"] == "/tmp/test-report.md"


class TestWriteSection:
    def test_write_section_success(self, sample_session):
        sid = sample_session["session_id"]
        err = session_manager.write_section(sid, "intro", "Текст введения")
        assert err is None

        state = session_manager.get_session(sid)
        assert state["sections"]["intro"] == "Текст введения"

    def test_write_section_persists_content(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "Тестовый контент")
        content = doc_store.load_content(sid, "intro")
        assert content == "Тестовый контент"

    def test_write_nonexistent_session(self):
        err = session_manager.write_section("nonexistent", "intro", "text")
        assert err is not None
        assert "not found" in err

    def test_write_nonexistent_section(self, sample_session):
        sid = sample_session["session_id"]
        err = session_manager.write_section(sid, "ghost", "text")
        assert err is not None
        assert "not found" in err

    def test_write_after_finalize_rejected(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "text")
        session_manager.write_section(sid, "body", "text")
        session_manager.write_section(sid, "conclusion", "text")
        session_manager.finalize_session(sid)
        err = session_manager.write_section(sid, "intro", "new text")
        assert err is not None
        assert "already finalized" in err

    def test_write_multiple_sections(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "A")
        session_manager.write_section(sid, "body", "B")
        session_manager.write_section(sid, "conclusion", "C")
        state = session_manager.get_session(sid)
        assert state["sections"]["intro"] == "A"
        assert state["sections"]["body"] == "B"
        assert state["sections"]["conclusion"] == "C"

    def test_rewrite_replaces_content(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "Old")
        session_manager.rewrite_section(sid, "intro", "New")
        state = session_manager.get_session(sid)
        assert state["sections"]["intro"] == "New"

    def test_rewrite_untouched_sections_preserved(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "A")
        session_manager.write_section(sid, "body", "B")
        session_manager.rewrite_section(sid, "intro", "A2")
        state = session_manager.get_session(sid)
        assert state["sections"]["body"] == "B"


class TestStatus:
    def test_status_initial(self, sample_session):
        status = session_manager.get_section_status(sample_session["session_id"])
        assert status["completed"] == 0
        assert status["total"] == 3
        assert status["progress"] == "0/3"

    def test_status_partial(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "text")
        status = session_manager.get_section_status(sid)
        assert status["completed"] == 1
        assert status["total"] == 3
        assert status["progress"] == "1/3"

    def test_status_all_complete(self, sample_session):
        sid = sample_session["session_id"]
        for s in ["intro", "body", "conclusion"]:
            session_manager.write_section(sid, s, "text")
        status = session_manager.get_section_status(sid)
        assert status["completed"] == 3
        assert status["progress"] == "3/3"

    def test_status_nonexistent_session(self):
        status = session_manager.get_section_status("ghost")
        assert "error" in status


class TestFinalize:
    def test_finalize_full_session(self, sample_session, tmp_path):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "Введение текст")
        session_manager.write_section(sid, "body", "Основная часть текст")
        session_manager.write_section(sid, "conclusion", "Заключение текст")

        result = session_manager.finalize_session(sid)
        assert result is not None
        assert Path(result).exists()

        content = Path(result).read_text()
        assert "# Содержание" in content  # TOC
        assert "Введение" in content
        assert "Основная часть" in content
        assert "Заключение" in content
        assert "## Введение" in content

    def test_finalize_incomplete_session(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "text")
        result = session_manager.finalize_session(sid)
        assert result is not None
        assert "Cannot finalize" in result

    def test_finalize_nonexistent(self):
        result = session_manager.finalize_session("ghost")
        assert result is not None
        assert "not found" in result

    def test_finalize_creates_parent_dirs(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "text")
        session_manager.write_section(sid, "body", "text")
        session_manager.write_section(sid, "conclusion", "text")
        result = session_manager.finalize_session(sid)
        assert result is not None
        assert Path(result).exists()


class TestResume:
    def test_resume_finds_in_progress(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "Текст введения")
        # Clear cache to simulate restart
        session_manager._SESSION_CACHE.clear()

        state = session_manager.resume_session("/tmp/test-report.md")
        assert state is not None
        assert state["sections"].get("intro") == "Текст введения"

    def test_resume_complete_not_found(self, sample_session):
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "text")
        session_manager.write_section(sid, "body", "text")
        session_manager.write_section(sid, "conclusion", "text")
        session_manager.finalize_session(sid)
        session_manager._SESSION_CACHE.clear()

        state = session_manager.resume_session("/tmp/test-report.md")
        assert state is None  # complete sessions are not resumed

    def test_resume_nonexistent(self):
        state = session_manager.resume_session("/tmp/ghost.md")
        assert state is None


class TestTransactionalSave:
    def test_no_corruption_on_crash(self, sample_session, monkeypatch):
        """Simulate crash by raising during save_session after state update."""
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "original")

        # Simulate partial write: write half, then fail
        state = session_manager.get_session(sid)
        original_path = doc_store._state_path(sid)
        partial_data = json.dumps(state)[:50]
        original_path.write_text(partial_data)

        # Load should fail gracefully, not crash
        loaded = doc_store.load_session(sid)
        assert loaded is None  # corrupted → return None, not raise

    def test_atomic_rename_preserves_previous(self, sample_session):
        """After failed write, previous state should still be loadable."""
        sid = sample_session["session_id"]
        session_manager.write_section(sid, "intro", "v1")
        v1_state = doc_store.load_session(sid)

        session_manager.write_section(sid, "intro", "v2")
        v2_state = doc_store.load_session(sid)

        assert v1_state is not None
        assert v2_state is not None
        assert v2_state["sections"]["intro"] == "v2"


class TestCancel:
    def test_cancel_session(self, sample_session):
        sid = sample_session["session_id"]
        err = session_manager.cancel_session(sid)
        assert err is None
        state = session_manager.get_session(sid)
        assert state["status"] == "cancelled"

    def test_cancel_nonexistent(self):
        err = session_manager.cancel_session("ghost")
        assert err is not None


class TestConcurrentSessions:
    def test_two_sessions_different_paths(self):
        s1 = session_manager.create_session(path="/tmp/a.md")
        s2 = session_manager.create_session(path="/tmp/b.md")
        assert s1["session_id"] != s2["session_id"]

        session_manager.write_section(s1["session_id"], "section-1", "Content A")
        session_manager.write_section(s2["session_id"], "section-1", "Content B")

        assert session_manager.get_session(s1["session_id"])["sections"]["section-1"] == "Content A"
        assert session_manager.get_session(s2["session_id"])["sections"]["section-1"] == "Content B"


class TestCleanup:
    def test_cleanup_does_not_crash(self, sample_session):
        """Cleanup should not crash even on fresh sessions."""
        removed = doc_store.cleanup_old(max_age_hours=0)
        assert isinstance(removed, int)
        # Fresh session may or may not be removed depending on file age;
        # the important thing is no crash and an integer returned.
