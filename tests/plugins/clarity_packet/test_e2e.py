"""End-to-end integration test for clarity_packet plugin.

Full lifecycle: create protocol → record hashes → modify doc → detect stale →
record updated → verify clean → check decision reconsideration.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.clarity_packet import clarity_pre_turn
from plugins.clarity_packet.__init__ import _handle_clarity_packet_report, _handle_clarity_packet_record


# Aliases for test readability (matching old API)
def clarity_full_report(workdir="", **kwargs):
    return _handle_clarity_packet_report(workdir=workdir, **kwargs)

def clarity_record_doc(doc_path="", workdir="", **kwargs):
    return _handle_clarity_packet_record(doc_path=doc_path, workdir=workdir, **kwargs)

from plugins.clarity_packet.packet_status import (
    DEFAULT_DEPENDENCY_GRAPH,
    compute_sha256,
    save_config,
)


@pytest.fixture
def e2e_project(tmp_path):
    """Create a realistic .clarity-protocol/ project."""
    pd = tmp_path / ".clarity-protocol"
    pd.mkdir()
    (pd / "goal").mkdir()
    (pd / "solution").mkdir()
    (pd / "failures").mkdir()
    (pd / "decisions").mkdir()

    (pd / "goal" / "problem.md").write_text(
        "# Problem\n\nBuild a real-time collaboration feature for doc editor.\n"
    )
    (pd / "goal" / "stakeholders.md").write_text(
        "# Stakeholders\n\n- Doc editors (direct, aligned)\n- Security team (direct, aligned)\n"
    )
    (pd / "goal" / "requirements.md").write_text(
        "# Requirements\n\n- No data loss on concurrent edits\n- <200ms sync latency\n"
    )
    (pd / "solution" / "solution.md").write_text(
        "# Solution\n\nOperational transforms on CRDT backend.\n"
    )
    (pd / "solution" / "architecture.md").write_text(
        "# Architecture\n\n- WebSocket sync layer\n- CRDT document store\n"
    )
    (pd / "failures" / "failures.md").write_text(
        "# Failures\n\n1. Network partition causes data divergence\n2. CRDT merge conflict\n"
    )
    (pd / "summary.md").write_text("# Summary\n\nReal-time collaboration for docs.\n")

    config = {
        "documents": {},
        "decisionState": {
            "decision-01-crdt": {
                "status": "decided",
                "related_docs": ["goal/requirements.md", "goal/problem.md"],
                "triggers": ["if latency requirement changes"],
            }
        },
        "graph": [
            {"from": fr, "to": to}
            for fr, tos in DEFAULT_DEPENDENCY_GRAPH.items()
            for to in tos
        ],
    }

    for doc_key in [
        "goal/problem.md", "goal/stakeholders.md", "goal/requirements.md",
        "solution/solution.md", "solution/architecture.md",
        "failures/failures.md", "summary.md",
    ]:
        fpath = pd / doc_key
        if fpath.exists():
            config["documents"][doc_key] = {"sha256": compute_sha256(fpath)}

    save_config(pd, config)
    return tmp_path


class TestE2ELifecycle:
    """Full lifecycle test mimicking real-world usage."""

    def test_fresh_project_all_current(self, e2e_project):
        """After initial setup — all documents current."""
        result = clarity_pre_turn(workdir=str(e2e_project))
        assert result is None

    def test_change_upstream_cascades_to_downstream(self, e2e_project):
        """Problem change → stakeholders, requirements, solution, architecture, failures all stale."""
        pd = e2e_project / ".clarity-protocol"

        # User changes the problem statement
        (pd / "goal" / "problem.md").write_text(
            "# Problem\n\nBuild an ASYNC collaboration feature (not real-time).\n"
        )

        result = clarity_pre_turn(workdir=str(e2e_project))
        assert result is not None
        injection = result["context_injection"]
        assert "STALENESS" in injection

        # Should mention multiple stale docs
        report = clarity_full_report(workdir=str(e2e_project))
        assert "goal/stakeholders.md" in report
        assert "goal/requirements.md" in report
        assert "solution/solution.md" in report
        assert "failures/failures.md" in report

    def test_record_acknowledges_change(self, e2e_project):
        """After recording updated doc, the recorded doc is no longer 'changed'.
        But downstream docs still need updating — they just won't be flagged as
        'stale' because SHA-256 only detects changes, not content consistency.
        This is correct: --record means 'I acknowledge this change, I'll update downstream myself'.
        """
        pd = e2e_project / ".clarity-protocol"

        # Change problem
        (pd / "goal" / "problem.md").write_text("# Problem\n\nCHANGED.\n")

        # Verify stale detected before record
        result_before = clarity_pre_turn(workdir=str(e2e_project))
        assert result_before is not None  # stale before record

        # Record the change
        result = clarity_record_doc(doc_path="goal/problem.md", workdir=str(e2e_project))
        assert "Recorded" in result

        # After record: problem hash matches → not "changed" → no staleness cascade
        # This is correct: user acknowledged, system trusts user to update downstream
        result_after = clarity_pre_turn(workdir=str(e2e_project))
        assert result_after is None  # acknowledged → clean state

    def test_full_resolution(self, e2e_project):
        """Update all stale docs + record → clean state."""
        pd = e2e_project / ".clarity-protocol"

        # Change problem
        (pd / "goal" / "problem.md").write_text("# Problem\n\nCHANGED.\n")
        clarity_record_doc(doc_path="goal/problem.md", workdir=str(e2e_project))

        # Update and record all downstream
        (pd / "goal" / "stakeholders.md").write_text("# Stakeholders\n\nUpdated.\n")
        clarity_record_doc(doc_path="goal/stakeholders.md", workdir=str(e2e_project))

        (pd / "goal" / "requirements.md").write_text("# Requirements\n\nUpdated.\n")
        clarity_record_doc(doc_path="goal/requirements.md", workdir=str(e2e_project))

        (pd / "solution" / "solution.md").write_text("# Solution\n\nUpdated.\n")
        clarity_record_doc(doc_path="solution/solution.md", workdir=str(e2e_project))

        (pd / "solution" / "architecture.md").write_text("# Architecture\n\nUpdated.\n")
        clarity_record_doc(doc_path="solution/architecture.md", workdir=str(e2e_project))

        (pd / "failures" / "failures.md").write_text("# Failures\n\nUpdated.\n")
        clarity_record_doc(doc_path="failures/failures.md", workdir=str(e2e_project))

        # Now all current
        result = clarity_pre_turn(workdir=str(e2e_project))
        assert result is None

    def test_decision_reconsideration_e2e(self, e2e_project):
        """Decision gets flagged when related doc changes."""
        pd = e2e_project / ".clarity-protocol"

        # Change requirements (related to decision-01)
        (pd / "goal" / "requirements.md").write_text(
            "# Requirements\n\n- No data loss\n- <50ms latency (was 200ms)\n"
        )

        report = clarity_full_report(workdir=str(e2e_project))
        assert "decision-01-crdt" in report
        assert "requirements.md" in report

    def test_decision_not_flagged_when_unrelated_doc_changes(self, e2e_project):
        """Decision NOT flagged when unrelated doc changes."""
        pd = e2e_project / ".clarity-protocol"

        # Change summary (NOT in decision-01's related_docs)
        (pd / "summary.md").write_text("# Summary\n\nUpdated summary.\n")
        clarity_record_doc(doc_path="summary.md", workdir=str(e2e_project))

        report = clarity_full_report(workdir=str(e2e_project))
        # decision-01 should NOT be flagged (only requirements.md and problem.md are related)
        assert "decision-01-crdt" not in report

    def test_transitive_chain(self, e2e_project):
        """problem.md → requirements.md → solution.md → architecture.md — full chain."""
        pd = e2e_project / ".clarity-protocol"
        config_path = pd / "config.json"

        with open(config_path) as f:
            config = json.load(f)

        # Add architecture.md dependencies
        # architecture depends on solution which depends on requirements which depends on problem
        # DEFAULT_DEPENDENCY_GRAPH already has these edges

        # Change problem
        (pd / "goal" / "problem.md").write_text("# Problem\n\nTOTALLY DIFFERENT.\n")

        doc_status_raw = {}
        from plugins.clarity_packet.packet_status import check_staleness, load_config, build_graph_from_config
        cfg = load_config(pd)
        graph = build_graph_from_config(cfg)
        doc_status, stale = check_staleness(pd, cfg, graph)

        # Verify the full chain: problem → requirements → solution → architecture
        assert "goal/requirements.md" in stale
        assert "solution/solution.md" in stale
        assert "solution/architecture.md" in stale  # transitive: problem → requirements → solution → architecture

    def test_report_format(self, e2e_project):
        """Report is human-readable and contains key information."""
        pd = e2e_project / ".clarity-protocol"
        (pd / "goal" / "problem.md").write_text("# Problem\n\nChanged.\n")

        report = clarity_full_report(workdir=str(e2e_project))
        assert "Clarity Packet Status" in report
        assert "STALE" in report
        assert "Current:" in report  # count of current docs