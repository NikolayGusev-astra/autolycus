"""Clarity Packet — SHA-256 dependency graph + stale document detection.

Registers a pre_llm_call hook that:
1. Checks if .clarity-protocol/ exists in the project
2. Runs SHA-256 staleness detection on all protocol documents
3. If stale docs found — injects a STALENESS WARNING into context
4. If decision reconsideration needed — injects DECISION WARNING

Also registers:
- clarity_packet_report tool: on-demand full report
- clarity_packet_record tool: record current hash as "accepted"

Zero overhead when .clarity-protocol/ is absent (~1 file stat check per turn).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from plugins.clarity_packet.packet_status import (
    build_graph_from_config,
    check_decision_reconsideration,
    check_staleness,
    generate_report,
    load_config,
    record_document,
)

logger = logging.getLogger(__name__)

# ── Hook ─────────────────────────────────────────────────────────────────────


def clarity_pre_turn(
    session_id: str = "",
    user_message: str = "",
    conversation_history: Optional[list] = None,
    model: str = "",
    workdir: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """pre_llm_call hook: check .clarity-protocol/ staleness, inject warnings.

    Returns dict with 'context_injection' if stale docs found, None otherwise.
    Token cost: ~0 when no .clarity-protocol/. ~50 tokens for staleness report.
    """
    if not workdir:
        return None

    protocol_dir = Path(workdir) / ".clarity-protocol"
    if not protocol_dir.exists():
        return None  # Zero overhead when no protocol

    try:
        config = load_config(protocol_dir)
        graph = build_graph_from_config(config)
        doc_status, stale_docs = check_staleness(protocol_dir, config, graph)

        if not stale_docs:
            return None  # All current — no injection needed

        # Build staleness warning
        stale_names = ", ".join(d.split("/")[-1] for d in stale_docs[:5])
        if len(stale_docs) > 5:
            stale_names += f" (+{len(stale_docs) - 5} more)"

        injection = (
            f"\n⚠ CLARITY STALENESS: {len(stale_docs)} document(s) need review: "
            f"{stale_names}. "
            f"Upstream changes detected. Re-read before proceeding.\n"
        )

        # Check decision reconsideration
        decisions = check_decision_reconsideration(protocol_dir, config)
        if decisions:
            dec_names = ", ".join(d["decision_id"] for d in decisions[:3])
            injection += (
                f"⚠ DECISIONS NEED REVIEW: {dec_names}. "
                f"Related documents changed since decision was made.\n"
            )

        return {"context_injection": injection}

    except Exception as e:
        logger.warning("Clarity packet check failed (non-fatal): %s", e)
        return None  # Never break the agent loop


# ── Tool handlers ─────────────────────────────────────────────────────────────


def _handle_clarity_packet_report(
    workdir: str = "",
    **_: Any,
) -> str:
    """Handle clarity_packet_report tool call — generate full staleness report."""
    if not workdir:
        return "ERROR: no workdir specified"

    protocol_dir = Path(workdir) / ".clarity-protocol"
    if not protocol_dir.exists():
        return "No .clarity-protocol/ found in project."

    config = load_config(protocol_dir)
    graph = build_graph_from_config(config)
    doc_status, stale_docs = check_staleness(protocol_dir, config, graph)
    report = generate_report(protocol_dir, doc_status, stale_docs)

    decisions = check_decision_reconsideration(protocol_dir, config)
    if decisions:
        report += "\n\nDecisions needing reconsideration:"
        for d in decisions:
            report += f"\n  • {d['decision_id']}: {d['changed_docs']} changed"

    return report


def _handle_clarity_packet_record(
    doc_path: str = "",
    workdir: str = "",
    **_: Any,
) -> str:
    """Handle clarity_packet_record tool call — record document hash."""
    if not workdir:
        return "ERROR: no workdir specified"
    if not doc_path:
        return "ERROR: no doc_path specified"

    protocol_dir = Path(workdir) / ".clarity-protocol"
    if not protocol_dir.exists():
        return "ERROR: .clarity-protocol/ not found"

    config = load_config(protocol_dir)
    try:
        config = record_document(protocol_dir, config, doc_path)
        return f"✓ Recorded {doc_path}: hash updated in config.json"
    except FileNotFoundError as e:
        return f"ERROR: {e}"


# ── Plugin Registration ──────────────────────────────────────────────────────


def register(ctx: Any) -> None:
    """Register Clarity Packet hook and tools."""
    ctx.register_hook("pre_llm_call", clarity_pre_turn)

    ctx.register_tool(
        name="clarity_packet_report",
        toolset="default",
        schema={
            "type": "object",
            "properties": {
                "workdir": {
                    "type": "string",
                    "description": "Project directory (defaults to current workdir)",
                },
            },
            "required": [],
        },
        handler=_handle_clarity_packet_report,
        emoji="📋",
    )

    ctx.register_tool(
        name="clarity_packet_record",
        toolset="default",
        schema={
            "type": "object",
            "properties": {
                "doc_path": {
                    "type": "string",
                    "description": "Document path relative to .clarity-protocol/ (e.g. goal/problem.md)",
                },
                "workdir": {
                    "type": "string",
                    "description": "Project directory (defaults to current workdir)",
                },
            },
            "required": ["doc_path"],
        },
        handler=_handle_clarity_packet_record,
        emoji="✓",
    )

    logger.info("Clarity Packet plugin registered: pre_llm_call hook + 2 tools")
