"""Functional tests for clarity_packet plugin (hook + tools).

Tests the pre_llm_call hook injection, clarity_packet_record, and clarity_packet_report.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from plugins.clarity_packet import clarity_pre_turn
from plugins.clarity_packet.__init__ import _handle_clarity_packet_report, _handle_clarity_packet_record
from plugins.clarity_packet.packet_status import (
    DEFAULT_DEPENDENCY_GRAPH,
    compute_sha256,
    save_config,
)


# Aliases for test readability (matching old API)
def clarity_full_report(workdir="", **kwargs):
    return _handle_clarity_packet_report(workdir=workdir, **kwargs)

def clarity_record_doc(doc_path="", workdir="", **kwargs):
    return _handle_clarity_packet_record(doc_path=doc_path, workdir=workdir, **kwargs)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def protocol_dir(tmp_path):
    """Create .clarity-protocol/ with config and documents, all hashes recorded."""
    pd = tmp_path / ".clarity-protocol"
    pd.mkdir()
    (pd / "goal").mkdir()
    (pd / "solution").mkdir()
    (pd / "failures").mkdir()

    (pd / "goal" / "problem.md").write_text("# Problem\n\nSolve X.\n")
    (pd / "goal" / "stakeholders.md").write_text("# Stakeholders\n\n- User\n")
    (pd / "goal" / "requirements.md").write_text("# Requirements\n\n- Must Y\n")
    (pd / "solution" / "solution.md").write_text("# Solution\n\nBuild Z.\n")
    (pd / "failures" / "failures.md").write_text("# Failures\n\n1. F1\n")
    (pd / "summary.md").write_text("# Summary\n\nOverview.\n")

    config = {
        "documents": {},
        "decisionState": {},
        "graph": [
            {"from": fr, "to": to}
            for fr, tos in DEFAULT_DEPENDENCY_GRAPH.items()
            for to in tos
        ],
    }
    for doc_key in [
        "goal/problem.md", "goal/stakeholders.md", "goal/requirements.md",
        "solution/solution.md", "failures/failures.md", "summary.md",
    ]:
        fpath = pd / doc_key
        if fpath.exists():
            config["documents"][doc_key] = {"sha256": compute_sha256(fpath)}

    save_config(pd, config)
    return pd


@pytest.fixture
def workdir_with_protocol(tmp_path, protocol_dir):
    """Return the parent directory that contains .clarity-protocol/."""
    return protocol_dir.parent


# ── clarity_pre_turn hook ─────────────────────────────────────────────────────


class TestClarityPreTurn:
    def test_no_injection_when_all_current(self, workdir_with_protocol):
        result = clarity_pre_turn(workdir=str(workdir_with_protocol))
        assert result is None

    def test_no_injection_when_no_protocol_dir(self, tmp_path):
        result = clarity_pre_turn(workdir=str(tmp_path))
        assert result is None

    def test_no_injection_when_workdir_empty(self):
        result = clarity_pre_turn(workdir="")
        assert result is None

    def test_injects_staleness_warning(self, workdir_with_protocol, protocol_dir):
        """Changing upstream doc should trigger staleness injection."""
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nCHANGED.\n")

        result = clarity_pre_turn(workdir=str(workdir_with_protocol))

        assert result is not None
        assert "context_injection" in result
        injection = result["context_injection"]
        assert "STALENESS" in injection
        assert "stakeholders.md" in injection or "requirements.md" in injection

    def test_injection_includes_decision_warning(self, workdir_with_protocol, protocol_dir):
        """Changed related doc should trigger decision reconsideration."""
        # Add a decision that references requirements.md
        config_path = protocol_dir / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        config["decisionState"] = {
            "decision-01": {
                "status": "decided",
                "related_docs": ["goal/requirements.md"],
            }
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Change the related doc
        (protocol_dir / "goal" / "requirements.md").write_text("# Requirements\n\nCHANGED.\n")

        result = clarity_pre_turn(workdir=str(workdir_with_protocol))
        assert result is not None
        injection = result["context_injection"]
        assert "DECISIONS NEED REVIEW" in injection
        assert "decision-01" in injection

    def test_graceful_on_corrupt_config(self, workdir_with_protocol, protocol_dir):
        """Corrupt config.json should not crash the hook."""
        (protocol_dir / "config.json").write_text("not valid json{{{")

        result = clarity_pre_turn(workdir=str(workdir_with_protocol))
        # Should return None (error swallowed, no crash)
        assert result is None

    def test_graceful_on_missing_doc_references(self, workdir_with_protocol, protocol_dir):
        """Decision references a doc that doesn't exist — should not crash."""
        config_path = protocol_dir / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        config["decisionState"] = {
            "decision-01": {
                "status": "decided",
                "related_docs": ["nonexistent/doc.md"],
            }
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        result = clarity_pre_turn(workdir=str(workdir_with_protocol))
        assert result is None  # No error, no decisions to flag


# ── clarity_full_report tool ──────────────────────────────────────────────────


class TestClarityFullReport:
    def test_report_all_current(self, workdir_with_protocol):
        report = clarity_full_report(workdir=str(workdir_with_protocol))
        assert "All documents current" in report

    def test_report_with_stale(self, workdir_with_protocol, protocol_dir):
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")
        report = clarity_full_report(workdir=str(workdir_with_protocol))
        assert "STALE" in report

    def test_report_decisions(self, workdir_with_protocol, protocol_dir):
        config_path = protocol_dir / "config.json"
        with open(config_path) as f:
            config = json.load(f)
        config["decisionState"] = {
            "d1": {"status": "decided", "related_docs": ["goal/problem.md"]}
        }
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")

        report = clarity_full_report(workdir=str(workdir_with_protocol))
        assert "d1" in report

    def test_no_protocol_dir(self, tmp_path):
        report = clarity_full_report(workdir=str(tmp_path))
        assert "No .clarity-protocol" in report

    def test_empty_workdir(self):
        report = clarity_full_report(workdir="")
        assert "ERROR" in report


# ── clarity_record_doc tool ───────────────────────────────────────────────────


class TestClarityRecordDoc:
    def test_record_updates_hash(self, workdir_with_protocol, protocol_dir):
        # Modify doc
        problem_path = protocol_dir / "goal" / "problem.md"
        problem_path.write_text("# Problem\n\nUpdated!\n")

        result = clarity_record_doc(
            doc_path="goal/problem.md",
            workdir=str(workdir_with_protocol),
        )
        assert "Recorded" in result
        assert "goal/problem.md" in result

        # Verify hash was actually saved
        from plugins.clarity_packet.packet_status import load_config, compute_sha256
        config = load_config(protocol_dir)
        assert config["documents"]["goal/problem.md"]["sha256"] == compute_sha256(problem_path)

    def test_record_missing_doc(self, workdir_with_protocol):
        result = clarity_record_doc(
            doc_path="nonexistent.md",
            workdir=str(workdir_with_protocol),
        )
        assert "ERROR" in result

    def test_record_no_protocol_dir(self, tmp_path):
        result = clarity_record_doc(
            doc_path="goal/problem.md",
            workdir=str(tmp_path),
        )
        assert "ERROR" in result
