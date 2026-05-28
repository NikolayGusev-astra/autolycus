"""Functional tests for clarity_packet.packet_status.

Tests real file I/O, hash computation, staleness detection, and decision reconsideration.
No mocks — uses temp directories with actual .clarity-protocol/ structures.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from plugins.clarity_packet.packet_status import (
    DEFAULT_DEPENDENCY_GRAPH,
    DocStatus,
    build_graph_from_config,
    check_decision_reconsideration,
    check_staleness,
    compute_sha256,
    generate_report,
    load_config,
    main,
    record_document,
    save_config,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def protocol_dir(tmp_path):
    """Create a minimal .clarity-protocol/ with config.json and template files."""
    pd = tmp_path / ".clarity-protocol"
    pd.mkdir()

    # Create goal/ and solution/ dirs
    (pd / "goal").mkdir()
    (pd / "solution").mkdir()
    (pd / "failures").mkdir()
    (pd / "decisions").mkdir()

    # Create minimal documents
    (pd / "goal" / "problem.md").write_text("# Problem\n\nWe need to solve X.\n")
    (pd / "goal" / "stakeholders.md").write_text("# Stakeholders\n\n- User\n")
    (pd / "goal" / "requirements.md").write_text("# Requirements\n\n- Must do Y\n")
    (pd / "solution" / "solution.md").write_text("# Solution\n\nBuild Z.\n")
    (pd / "failures" / "failures.md").write_text("# Failures\n\n1. F1\n")
    (pd / "summary.md").write_text("# Summary\n\nProject overview.\n")
    (pd / "notes.md").write_text("# Notes\n\nCross-phase observations.\n")

    # Create config.json with initial hashes
    config = {
        "documents": {},
        "decisionState": {},
        "graph": [
            {"from": fr, "to": to}
            for fr, tos in DEFAULT_DEPENDENCY_GRAPH.items()
            for to in tos
        ],
    }

    # Record initial hashes
    for doc_key in [
        "goal/problem.md",
        "goal/stakeholders.md",
        "goal/requirements.md",
        "solution/solution.md",
        "failures/failures.md",
        "summary.md",
        "notes.md",
    ]:
        fpath = pd / doc_key
        if fpath.exists():
            h = compute_sha256(fpath)
            config["documents"][doc_key] = {"sha256": h}

    save_config(pd, config)
    return pd


# ── compute_sha256 ────────────────────────────────────────────────────────────


class TestComputeSha256:
    def test_known_hash(self, tmp_path):
        """SHA-256 of known content matches expected value."""
        f = tmp_path / "test.txt"
        f.write_text("hello\n")
        h = compute_sha256(f)
        # Known SHA-256 of "hello\n"
        expected = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
        assert h == expected

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        h = compute_sha256(f)
        # SHA-256 of empty string
        assert h == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    def test_binary_content(self, tmp_path):
        f = tmp_path / "binary.bin"
        f.write_bytes(b"\x00\x01\x02\xff")
        h = compute_sha256(f)
        assert len(h) == 64  # SHA-256 hex is always 64 chars
        assert all(c in "0123456789abcdef" for c in h)

    def test_large_file(self, tmp_path):
        """Large file (>8KB chunk size) hashes correctly."""
        f = tmp_path / "large.txt"
        f.write_text("x" * 100_000)
        h = compute_sha256(f)
        assert len(h) == 64


# ── load_config / save_config ────────────────────────────────────────────────


class TestConfig:
    def test_load_missing_returns_defaults(self, tmp_path):
        pd = tmp_path / ".clarity-protocol"
        pd.mkdir()
        config = load_config(pd)
        assert config == {"documents": {}, "decisionState": {}, "graph": []}

    def test_save_and_load_roundtrip(self, tmp_path):
        pd = tmp_path / ".clarity-protool"
        pd.mkdir()
        config = {
            "documents": {"goal/problem.md": {"sha256": "abc123"}},
            "decisionState": {"d1": {"status": "decided"}},
            "graph": [{"from": "a", "to": "b"}],
        }
        save_config(pd, config)
        loaded = load_config(pd)
        assert loaded == config

    def test_save_is_atomic(self, tmp_path):
        """save_config writes to .tmp then renames — no partial writes."""
        pd = tmp_path / ".clarity-protocol"
        pd.mkdir()
        config = {"documents": {}, "decisionState": {}, "graph": []}
        save_config(pd, config)
        # No .tmp file should remain
        assert not (pd / "config.json.tmp").exists()
        assert (pd / "config.json").exists()


# ── build_graph_from_config ──────────────────────────────────────────────────


class TestBuildGraph:
    def test_uses_config_graph_when_present(self, tmp_path):
        pd = tmp_path / ".clarity-protocol"
        pd.mkdir()
        config = {
            "graph": [
                {"from": "a.md", "to": "b.md"},
                {"from": "a.md", "to": "c.md"},
            ]
        }
        graph = build_graph_from_config(config)
        assert graph == {"a.md": ["b.md", "c.md"]}

    def test_falls_back_to_defaults(self, tmp_path):
        pd = tmp_path / ".clarity-protocol"
        pd.mkdir()
        config = {"graph": []}
        graph = build_graph_from_config(config)
        assert "goal/problem.md" in graph
        assert "goal/stakeholders.md" in graph["goal/problem.md"]

    def test_empty_config_uses_defaults(self):
        graph = build_graph_from_config({})
        assert graph == DEFAULT_DEPENDENCY_GRAPH


# ── check_staleness ───────────────────────────────────────────────────────────


class TestCheckStaleness:
    def test_all_current_when_hashes_match(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        doc_status, stale = check_staleness(protocol_dir, config, graph)
        # No stale docs; MISSING/EMPTY are allowed (not every doc must exist)
        assert stale == []
        assert all(
            s in (DocStatus.CURRENT, DocStatus.MISSING, DocStatus.EMPTY)
            for s in doc_status.values()
        )

    def test_detects_stale_downstream(self, protocol_dir):
        """Changing problem.md should mark stakeholders, requirements, solution as stale."""
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Modify problem.md
        problem_path = protocol_dir / "goal" / "problem.md"
        problem_path.write_text("# Problem\n\nWe need to solve ENTIRELY DIFFERENT thing.\n")

        doc_status, stale = check_staleness(protocol_dir, config, graph)

        # Downstream docs should be stale
        assert "goal/stakeholders.md" in stale
        assert "goal/requirements.md" in stale
        assert "solution/solution.md" in stale

    def test_missing_doc_detected(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Remove a doc
        (protocol_dir / "goal" / "stakeholders.md").unlink()

        doc_status, stale = check_staleness(protocol_dir, config, graph)
        assert doc_status["goal/stakeholders.md"] == DocStatus.MISSING

    def test_empty_doc_detected(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Empty a doc
        (protocol_dir / "goal" / "requirements.md").write_text("")

        doc_status, stale = check_staleness(protocol_dir, config, graph)
        assert doc_status["goal/requirements.md"] == DocStatus.EMPTY

    def test_transitive_staleness(self, protocol_dir):
        """problem.md change → requirements.md stale → solution.md stale (transitive)."""
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Modify problem.md
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")

        doc_status, stale = check_staleness(protocol_dir, config, graph)

        # Transitive: problem → requirements → solution
        assert "goal/requirements.md" in stale
        assert "solution/solution.md" in stale

    def test_no_false_stale_when_downstream_also_changed(self, protocol_dir):
        """If both upstream and downstream changed, downstream is not stale (it's current)."""
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Modify both problem.md AND solution.md
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")
        (protocol_dir / "solution" / "solution.md").write_text("# Solution\n\nAlso changed.\n")

        doc_status, stale = check_staleness(protocol_dir, config, graph)

        # solution.md changed itself → it's CURRENT, not STALE
        assert doc_status["solution/solution.md"] == DocStatus.CURRENT
        assert "solution/solution.md" not in stale

    def test_stale_list_contains_correct_docs(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)

        # Change requirements.md — should mark solution and failures as stale
        (protocol_dir / "goal" / "requirements.md").write_text("# Requirements\n\nNew req.\n")

        doc_status, stale = check_staleness(protocol_dir, config, graph)

        assert "solution/solution.md" in stale
        assert "failures/failures.md" in stale
        # problem.md is upstream of requirements, not downstream — should not be stale
        assert "goal/problem.md" not in stale


# ── record_document ───────────────────────────────────────────────────────────


class TestRecordDocument:
    def test_records_hash(self, protocol_dir):
        config = load_config(protocol_dir)

        # Modify problem.md
        problem_path = protocol_dir / "goal" / "problem.md"
        problem_path.write_text("# Problem\n\nUpdated problem.\n")

        # Record it
        new_config = record_document(protocol_dir, config, "goal/problem.md")

        # Hash should be updated
        new_hash = compute_sha256(problem_path)
        assert new_config["documents"]["goal/problem.md"]["sha256"] == new_hash

    def test_creates_doc_entry_if_missing(self, protocol_dir):
        config = load_config(protocol_dir)

        # Add a new doc
        (protocol_dir / "goal" / "open-questions.md").write_text("# Open Questions\n\nQ1?\n")

        new_config = record_document(protocol_dir, config, "goal/open-questions.md")
        assert "goal/open-questions.md" in new_config["documents"]

    def test_raises_on_missing_file(self, protocol_dir):
        config = load_config(protocol_dir)
        with pytest.raises(FileNotFoundError):
            record_document(protocol_dir, config, "nonexistent.md")

    def test_persists_graph_in_config(self, protocol_dir):
        config = {"documents": {}, "decisionState": {}}  # no graph
        record_document(protocol_dir, config, "goal/problem.md")
        assert "graph" in config
        assert len(config["graph"]) > 0


# ── generate_report ───────────────────────────────────────────────────────────


class TestGenerateReport:
    def test_all_current_report(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        doc_status, stale = check_staleness(protocol_dir, config, graph)
        report = generate_report(protocol_dir, doc_status, stale)
        # No stale docs → report shows "All documents current"
        assert "All documents current" in report

    def test_stale_report_lists_docs(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")
        doc_status, stale = check_staleness(protocol_dir, config, graph)
        report = generate_report(protocol_dir, doc_status, stale)
        assert "STALE" in report
        assert "goal/stakeholders.md" in report

    def test_missing_docs_in_report(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        (protocol_dir / "goal" / "stakeholders.md").unlink()
        doc_status, stale = check_staleness(protocol_dir, config, graph)
        report = generate_report(protocol_dir, doc_status, stale)
        # Missing docs are noted even when no stale docs (as a "not yet created" note)
        assert "not yet created" in report or "MISSING" in report

    def test_report_includes_upstream_triggers(self, protocol_dir):
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")
        doc_status, stale = check_staleness(protocol_dir, config, graph)
        report = generate_report(protocol_dir, doc_status, stale)
        # Should mention that problem.md triggered the staleness
        assert "goal/problem.md" in report


# ── check_decision_reconsideration ────────────────────────────────────────────


class TestDecisionReconsideration:
    def test_no_decisions_returns_empty(self, protocol_dir):
        config = load_config(protocol_dir)
        result = check_decision_reconsideration(protocol_dir, config)
        assert result == []

    def test_decided_with_unchanged_docs(self, protocol_dir):
        config = load_config(protocol_dir)
        config["decisionState"] = {
            "decision-01-db": {
                "status": "decided",
                "related_docs": ["goal/requirements.md"],
                "triggers": ["if requirements change"],
            }
        }
        result = check_decision_reconsideration(protocol_dir, config)
        assert result == []

    def test_decided_with_changed_related_doc(self, protocol_dir):
        config = load_config(protocol_dir)
        config["decisionState"] = {
            "decision-01-db": {
                "status": "decided",
                "related_docs": ["goal/requirements.md"],
                "triggers": ["if requirements change"],
            }
        }

        # Change the related doc
        (protocol_dir / "goal" / "requirements.md").write_text("# Requirements\n\nChanged!\n")

        result = check_decision_reconsideration(protocol_dir, config)
        assert len(result) == 1
        assert result[0]["decision_id"] == "decision-01-db"
        assert "goal/requirements.md" in result[0]["changed_docs"]

    def test_gathering_status_skipped(self, protocol_dir):
        config = load_config(protocol_dir)
        config["decisionState"] = {
            "decision-02": {
                "status": "gathering",
                "related_docs": ["goal/problem.md"],
            }
        }
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")
        result = check_decision_reconsideration(protocol_dir, config)
        assert result == []  # gathering → not checked

    def test_multiple_decisions(self, protocol_dir):
        config = load_config(protocol_dir)
        config["decisionState"] = {
            "d1": {
                "status": "decided",
                "related_docs": ["goal/problem.md"],
            },
            "d2": {
                "status": "decided",
                "related_docs": ["goal/requirements.md"],
            },
        }

        # Change only problem.md
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")

        result = check_decision_reconsideration(protocol_dir, config)
        assert len(result) == 1
        assert result[0]["decision_id"] == "d1"


# ── CLI ───────────────────────────────────────────────────────────────────────


class TestCLI:
    def test_report_via_cli(self, protocol_dir, capsys):
        rc = main([str(protocol_dir.parent), "--report"])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Clarity Packet Status" in output

    def test_record_via_cli(self, protocol_dir, capsys):
        # Modify then record
        (protocol_dir / "goal" / "problem.md").write_text("# Problem\n\nUpdated.\n")
        rc = main([str(protocol_dir.parent), "--record", "goal/problem.md"])
        assert rc == 0
        output = capsys.readouterr().out
        assert "Recorded" in output

    def test_missing_protocol_dir(self, tmp_path, capsys):
        rc = main([str(tmp_path), "--report"])
        assert rc == 1
        output = capsys.readouterr().out
        assert "not found" in output

    def test_record_missing_file(self, protocol_dir, capsys):
        rc = main([str(protocol_dir.parent), "--record", "nonexistent.md"])
        assert rc == 1

    def test_help(self, capsys):
        rc = main(["--help"])
        assert rc == 0
        output = capsys.readouterr().out
        assert "SHA-256" in output or "Clarity" in output
