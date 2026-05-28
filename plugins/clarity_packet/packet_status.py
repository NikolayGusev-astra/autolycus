"""Clarity Packet Status — SHA-256 dependency graph + stale document detection.

Standalone module: no LLM calls, no token overhead.
Checks .clarity-protocol/ config.json, computes hashes, detects stale docs.

Usage:
    python -m plugins.clarity_packet.packet_status <project_dir> --report
    python -m plugins.clarity_packet.packet_status <project_dir> --record goal/problem.md
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class DocStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    EMPTY = "empty"
    MISSING = "missing"


@dataclass
class DocEntry:
    path: str  # relative to .clarity-protocol/
    sha256: str
    last_accepted: str  # ISO timestamp or ""
    status: DocStatus = DocStatus.CURRENT
    triggers: List[str] = field(default_factory=list)


# Data-driven dependency graph. Edit this when adding new document types.
# Format: "from_doc": ["to_doc1", "to_doc2", ...]
DEFAULT_DEPENDENCY_GRAPH: Dict[str, List[str]] = {
    "goal/problem.md": [
        "goal/stakeholders.md",
        "goal/requirements.md",
        "solution/solution.md",
        "decisions/decisions.md",
    ],
    "goal/stakeholders.md": [
        "goal/requirements.md",
        "solution/solution.md",
    ],
    "goal/requirements.md": [
        "solution/solution.md",
        "solution/architecture.md",
        "failures/failures.md",
        "decisions/decisions.md",
    ],
    "goal/open-questions.md": [
        "solution/solution.md",
    ],
    "solution/solution.md": [
        "solution/architecture.md",
        "solution/solution-summary.md",
        "failures/failures.md",
        "decisions/decisions.md",
    ],
    "solution/architecture.md": [
        "failures/failures.md",
        "solution/solution-summary.md",
    ],
    "failures/failures.md": [
        "solution/architecture.md",  # cross-link: failures inform architecture
    ],
}

DEFAULT_DOC_KEYS = [
    "goal/problem.md",
    "goal/stakeholders.md",
    "goal/requirements.md",
    "goal/open-questions.md",
    "solution/solution.md",
    "solution/architecture.md",
    "solution/solution-summary.md",
    "failures/failures.md",
    "decisions/decisions.md",
    "summary.md",
    "notes.md",
    "messaging/messaging.md",
]


def compute_sha256(filepath: Path) -> str:
    """Compute SHA-256 hash of a file's content."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def load_config(protocol_dir: Path) -> Dict[str, Any]:
    """Load config.json from .clarity-protocol/. Returns empty defaults if missing."""
    config_path = protocol_dir / "config.json"
    if not config_path.exists():
        return {"documents": {}, "decisionState": {}, "graph": []}
    with open(config_path) as f:
        return json.load(f)


def save_config(protocol_dir: Path, config: Dict[str, Any]) -> None:
    """Write config.json atomically."""
    config_path = protocol_dir / "config.json"
    tmp = config_path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(config_path)


def build_graph_from_config(config: Dict[str, Any]) -> Dict[str, List[str]]:
    """Build dependency graph from config.json, falling back to DEFAULT_DEPENDENCY_GRAPH."""
    # If config has explicit graph, use it
    if config.get("graph"):
        graph: Dict[str, List[str]] = {}
        for edge in config["graph"]:
            fr = edge["from"]
            to = edge["to"]
            graph.setdefault(fr, []).append(to)
        return graph
    return dict(DEFAULT_DEPENDENCY_GRAPH)


def check_staleness(
    protocol_dir: Path,
    config: Dict[str, Any],
    graph: Dict[str, List[str]],
) -> Tuple[Dict[str, DocStatus], List[str]]:
    """
    Check all documents for staleness.
    
    Returns:
        - doc_status: {doc_path: DocStatus}
        - stale_downstream: list of doc paths that are stale due to upstream changes
    
    Algorithm:
        1. For each doc, compute current SHA-256
        2. Compare with stored hash in config.json
        3. If hash changed → mark as changed
        4. For each changed doc, walk downstream via dependency graph
        5. Downstream docs with unchanged hash but stale upstream → mark STALE
    """
    doc_status: Dict[str, DocStatus] = {}
    changed_docs: List[str] = []
    stale_downstream: List[str] = []

    documents_config = config.get("documents", {})

    # Phase 1: Check each doc's hash
    for doc_key in DEFAULT_DOC_KEYS:
        doc_path = protocol_dir / doc_key

        if not doc_path.exists():
            doc_status[doc_key] = DocStatus.MISSING
            continue

        if doc_path.stat().st_size == 0:
            doc_status[doc_key] = DocStatus.EMPTY
            continue

        current_hash = compute_sha256(doc_path)
        stored = documents_config.get(doc_key, {})
        stored_hash = stored.get("sha256", "")

        if not stored_hash:
            # New document — not yet recorded
            doc_status[doc_key] = DocStatus.CURRENT
            changed_docs.append(doc_key)
        elif current_hash != stored_hash:
            doc_status[doc_key] = DocStatus.CURRENT
            changed_docs.append(doc_key)
        else:
            doc_status[doc_key] = DocStatus.CURRENT

    # Phase 2: Walk downstream from changed docs
    visited: set = set()
    queue = list(changed_docs)

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        downstream = graph.get(current, [])
        for dep in downstream:
            if dep not in changed_docs:
                # This downstream doc exists and hasn't changed,
                # but an upstream doc it depends on has changed
                if doc_status.get(dep) == DocStatus.CURRENT:
                    doc_status[dep] = DocStatus.STALE
                    stale_downstream.append(dep)
                queue.append(dep)

    return doc_status, stale_downstream


def record_document(
    protocol_dir: Path,
    config: Dict[str, Any],
    doc_path: str,
) -> Dict[str, Any]:
    """
    Record current SHA-256 hash of a document as 'accepted'.
    Updates config.json.
    """
    full_path = protocol_dir / doc_path

    if not full_path.exists():
        raise FileNotFoundError(f"Document not found: {full_path}")

    current_hash = compute_sha256(full_path)

    if "documents" not in config:
        config["documents"] = {}

    if doc_path not in config["documents"]:
        config["documents"][doc_path] = {}

    config["documents"][doc_path]["sha256"] = current_hash

    # Ensure dependency graph is stored in config
    if not config.get("graph"):
        config["graph"] = [
            {"from": fr, "to": to}
            for fr, tos in DEFAULT_DEPENDENCY_GRAPH.items()
            for to in tos
        ]

    save_config(protocol_dir, config)
    return config


def generate_report(
    protocol_dir: Path,
    doc_status: Dict[str, DocStatus],
    stale_downstream: List[str],
) -> str:
    """Generate human-readable staleness report."""
    lines = []

    stale_docs = [d for d, s in doc_status.items() if s == DocStatus.STALE]
    missing_docs = [d for d, s in doc_status.items() if s == DocStatus.MISSING]
    empty_docs = [d for d, s in doc_status.items() if s == DocStatus.EMPTY]
    current_docs = [d for d, s in doc_status.items() if s == DocStatus.CURRENT]

    lines.append("=== Clarity Packet Status Report ===")
    lines.append(f"Protocol dir: {protocol_dir}")
    lines.append("")

    if not stale_docs:
        lines.append("✓ All documents current.")
        if missing_docs or empty_docs:
            lines.append("(Some documents are not yet created — this is normal for early-stage projects.)")
        return "\n".join(lines)

    if stale_docs:
        lines.append("⚠ STALE documents (upstream changed):")
        for d in stale_docs:
            triggers = _find_upstream_triggers(d, protocol_dir)
            trigger_str = f" ← {', '.join(triggers)}" if triggers else ""
            lines.append(f"  • {d}{trigger_str}")
        lines.append("")

    if missing_docs:
        lines.append("○ MISSING documents:")
        for d in missing_docs:
            lines.append(f"  • {d}")
        lines.append("")

    if empty_docs:
        lines.append("○ EMPTY documents:")
        for d in empty_docs:
            lines.append(f"  • {d}")
        lines.append("")

    if current_docs:
        lines.append(f"✓ Current: {len(current_docs)} docs")

    return "\n".join(lines)


def _find_upstream_triggers(doc: str, protocol_dir: Path) -> List[str]:
    """Find which upstream documents changed that caused this doc to be stale."""
    config = load_config(protocol_dir)
    graph = build_graph_from_config(config)

    triggers = []
    for upstream, downstreams in graph.items():
        if doc in downstreams:
            upstream_path = protocol_dir / upstream
            if upstream_path.exists():
                current_hash = compute_sha256(upstream_path)
                stored = config.get("documents", {}).get(upstream, {})
                if current_hash != stored.get("sha256", ""):
                    triggers.append(upstream)

    return triggers


def check_decision_reconsideration(
    protocol_dir: Path,
    config: Dict[str, Any],
) -> List[Dict[str, str]]:
    """
    Check decisions for reconsideration triggers.
    Returns list of decisions that need review.
    """
    decisions = config.get("decisionState", {})
    needs_review: List[Dict[str, str]] = []

    for decision_id, state in decisions.items():
        status = state.get("status", "decided")
        if status in ("gathering", "needed"):
            continue  # Not decided yet

        related_docs = state.get("related_docs", [])
        triggers = state.get("triggers", [])

        # Check if any related doc changed
        doc_changes = []
        documents_config = config.get("documents", {})

        for doc_path in related_docs:
            full_path = protocol_dir / doc_path
            if not full_path.exists():
                continue
            current_hash = compute_sha256(full_path)
            stored = documents_config.get(doc_path, {})
            if current_hash != stored.get("sha256", ""):
                doc_changes.append(doc_path)

        if doc_changes:
            needs_review.append({
                "decision_id": decision_id,
                "status": status,
                "changed_docs": ",".join(doc_changes),
                "triggers": "; ".join(triggers) if triggers else "related docs changed",
            })

    return needs_review


# ── CLI entry point ──────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    """CLI: python -m plugins.clarity_packet.packet_status <project_dir> [--report] [--record <path>]"""
    if argv is None:
        argv = sys.argv[1:]

    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    project_dir = Path(argv[0]).resolve()
    protocol_dir = project_dir / ".clarity-protocol"

    if not protocol_dir.exists():
        print(f"ERROR: .clarity-protocol/ not found in {project_dir}")
        return 1

    config = load_config(protocol_dir)
    graph = build_graph_from_config(config)

    # --report (default action)
    if "--report" in argv or "--record" not in argv:
        doc_status, stale_downstream = check_staleness(protocol_dir, config, graph)
        report = generate_report(protocol_dir, doc_status, stale_downstream)
        print(report)

        # Check decision reconsideration
        decisions = check_decision_reconsideration(protocol_dir, config)
        if decisions:
            print("\n⚠ Decisions needing reconsideration:")
            for d in decisions:
                print(f"  • {d['decision_id']}: {d['changed_docs']} changed")

    # --record <path>
    if "--record" in argv:
        idx = argv.index("--record")
        if idx + 1 < len(argv):
            doc_path = argv[idx + 1]
            try:
                config = record_document(protocol_dir, config, doc_path)
                print(f"✓ Recorded {doc_path}: hash updated")
            except FileNotFoundError as e:
                print(f"ERROR: {e}")
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
