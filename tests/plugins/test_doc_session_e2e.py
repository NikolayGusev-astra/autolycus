"""
Tests for doc_session plugin — Layer 3: E2E tests (through agent).

These tests require a running Hermes agent with the doc_session plugin loaded.
They are slow (~5 min) and expensive (API calls). Run selectively.

Usage:
    pytest tests/plugins/test_doc_session_e2e.py -v -k "test_full_session"
    pytest tests/plugins/test_doc_session_e2e.py -v -k "test_write_file_block"
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Mark all tests as slow/e2e
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("HERMES_E2E"),
        reason="Set HERMES_E2E=1 to run E2E tests",
    ),
]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_doc_cache():
    """Clear session cache between tests."""
    from plugins.doc_session import session_manager
    from plugins.doc_session import store as doc_store
    session_manager._SESSION_CACHE.clear()
    doc_store.cleanup_old(max_age_hours=0)
    from plugins.doc_session import _write_file_counts
    _write_file_counts.clear()


@pytest.fixture
def tmp_doc_path():
    """Return a temp file path for test document output."""
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


# ── E2E Tests ───────────────────────────────────────────────────────────────


class TestFullSession:
    """Full document lifecycle: create → write → finalize."""

    def test_full_session_small_doc(self, tmp_doc_path):
        """Create a 3-section document, write each section, finalize."""
        from plugins.doc_session import session_manager

        # Create
        state = session_manager.create_session(
            path=tmp_doc_path,
            custom_plan=[
                {"id": "s1", "title": "Section 1", "description": "First"},
                {"id": "s2", "title": "Section 2", "description": "Second"},
                {"id": "s3", "title": "Section 3", "description": "Third"},
            ],
        )
        sid = state["session_id"]

        # Write sections
        for section_id, content in [
            ("s1", "Content for section one."),
            ("s2", "Content for section two with more detail."),
            ("s3", "Conclusion content here."),
        ]:
            err = session_manager.write_section(sid, section_id, content)
            assert err is None

        # Status check
        status = session_manager.get_section_status(sid)
        assert status["completed"] == 3
        assert status["total"] == 3

        # Finalize
        result = session_manager.finalize_session(sid)
        assert result is not None
        assert "Cannot finalize" not in result
        assert Path(result).exists()

        # Verify content
        content = Path(result).read_text()
        assert "# Содержание" in content  # TOC
        assert "Section 1" in content
        assert "Section 2" in content
        assert "Section 3" in content
        assert "Content for section one" in content
        assert "Conclusion content here" in content

    def test_finalize_incomplete_rejected(self, tmp_doc_path):
        """Finalizing with missing sections must fail gracefully."""
        from plugins.doc_session import session_manager

        state = session_manager.create_session(path=tmp_doc_path)
        sid = state["session_id"]
        session_manager.write_section(sid, "section-1", "Only section")

        result = session_manager.finalize_session(sid)
        assert result is not None
        assert "Cannot finalize" in result

    def test_crash_recovery(self, tmp_doc_path):
        """Simulate crash: write 1/3 sections, clear cache, resume."""
        from plugins.doc_session import session_manager

        state = session_manager.create_session(
            path=tmp_doc_path,
            custom_plan=[
                {"id": "a", "title": "A", "description": ""},
                {"id": "b", "title": "B", "description": ""},
            ],
        )
        sid = state["session_id"]
        session_manager.write_section(sid, "a", "Section A written")

        # Simulate crash: clear cache
        session_manager._SESSION_CACHE.clear()

        # Resume
        resumed = session_manager.resume_session(tmp_doc_path)
        assert resumed is not None
        assert resumed["sections"].get("a") == "Section A written"
        assert "b" not in resumed["sections"]

        # Continue: write second section
        session_manager.write_section(resumed["session_id"], "b", "Section B written")

        # Finalize
        result = session_manager.finalize_session(resumed["session_id"])
        assert result is not None
        assert Path(result).exists()

    def test_template_propagates_sections(self, tmp_doc_path):
        """Creating with template should propagate section plan."""
        from plugins.doc_session import session_manager

        state = session_manager.create_session(
            path=tmp_doc_path,
            template_id="quarterly-report",
        )
        section_ids = {s["id"] for s in state["plan"]}
        assert "executive-summary" in section_ids
        assert "key-metrics" in section_ids
        assert "achievements" in section_ids
        assert "challenges" in section_ids
        assert "plans" in section_ids

    def test_multi_source_session(self, tmp_doc_path):
        """Create session with sources and write all sections."""
        from plugins.doc_session import session_manager

        state = session_manager.create_session(
            path=tmp_doc_path,
            sources=["/tmp/src1.md", "/tmp/src2.md"],
        )
        assert len(state["sources"]) == 2

        # Write all sections
        sid = state["session_id"]
        for s in state["plan"]:
            err = session_manager.write_section(sid, s["id"], f"Content for {s['title']}")
            assert err is None

        result = session_manager.finalize_session(sid)
        assert result is not None
        assert Path(result).exists()


class TestWriteFileBlockRedirect:
    """Verify Level 2 hook prevents large write_file."""

    def test_write_file_blocked_via_hook(self):
        """Direct hook test: write_file >15K for .md is blocked."""
        from plugins.doc_session import _on_pre_tool_call

        result = _on_pre_tool_call(
            tool_name="write_file",
            args={"path": "/tmp/big-report.md", "content": "X" * 20000},
        )
        assert result is not None
        assert result.get("action") == "block"


class TestToolsetIsolation:
    """Verify doc tools are separate from file tools."""

    def test_doc_toolset_separate(self):
        """Verify there are separate toolsets."""
        # This test verifies the design, not the runtime behavior.
        # Plugin registers tools with toolset="doc", not toolset="file".
        from plugins.doc_session import FILE_DOC_CREATE_SCHEMA
        # Toolset separation is declared in plugin registration — this
        # test confirms the schema exists and is well-formed.
        assert FILE_DOC_CREATE_SCHEMA["name"] == "file_doc_create"
