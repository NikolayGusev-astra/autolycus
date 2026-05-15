"""
plugins/doc_session/session_manager — Document session lifecycle.

DocSessionManager manages the state of a document being written section-by-section.
Sections are independent; any order is allowed. Finalize collects them into one file.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from plugins.doc_session import store
from plugins.doc_session.template_loader import load_template, list_templates

logger = __import__("logging").getLogger(__name__)

_SESSION_CACHE: dict[str, dict] = {}


def create_session(
    path: str,
    template_id: Optional[str] = None,
    sources: Optional[list[str]] = None,
    custom_plan: Optional[list[dict]] = None,
) -> dict:
    """Create a new document session.

    Args:
        path: Target file path.
        template_id: Optional template name.
        sources: Optional list of source file paths.
        custom_plan: Optional manually specified plan sections.

    Returns:
        Session state dict with session_id and plan.
    """
    session_id = f"doc-{uuid.uuid4().hex[:12]}"
    now = datetime.now(timezone.utc).isoformat()

    # Build plan from template or custom or default
    plan = _build_plan(template_id, custom_plan)

    state: dict[str, Any] = {
        "session_id": session_id,
        "path": path,
        "template_id": template_id,
        "sources": sources or [],
        "plan": plan,
        "sections": {},
        "model": None,
        "created": now,
        "updated": now,
        "status": "in_progress",
    }
    store.save_session(state)
    _SESSION_CACHE[session_id] = state
    return state


def _build_plan(
    template_id: Optional[str],
    custom_plan: Optional[list[dict]],
) -> list[dict]:
    if custom_plan:
        return custom_plan
    if template_id:
        tpl = load_template(template_id)
        if tpl:
            return tpl.get("sections", [])
        logger.warning("Template '%s' not found, using default plan", template_id)
    return [
        {"id": "section-1", "title": "Раздел 1", "description": "Первый раздел"},
        {"id": "section-2", "title": "Раздел 2", "description": "Второй раздел"},
        {"id": "section-3", "title": "Раздел 3", "description": "Третий раздел"},
    ]


def get_session(session_id: str) -> Optional[dict]:
    """Get session state, from cache or disk."""
    if session_id in _SESSION_CACHE:
        return _SESSION_CACHE[session_id]
    state = store.load_session(session_id)
    if state:
        _SESSION_CACHE[session_id] = state
    return state


def write_section(session_id: str, section_id: str, content: str) -> Optional[str]:
    """Write content to a section. Returns error message or None on success."""
    state = get_session(session_id)
    if state is None:
        return f"Session {session_id} not found"
    if state["status"] == "complete":
        return f"Session {session_id} is already finalized"
    if state["status"] == "cancelled":
        return f"Session {session_id} is cancelled"

    # Verify section exists in plan
    section_ids = {s["id"] for s in state["plan"]}
    if section_id not in section_ids:
        return f"Section '{section_id}' not found in plan"

    # Save content to disk
    store.save_content(session_id, section_id, content)

    # Update state
    state["sections"][section_id] = content
    state["updated"] = datetime.now(timezone.utc).isoformat()
    store.save_session(state)
    return None


def rewrite_section(session_id: str, section_id: str, content: str) -> Optional[str]:
    """Replace content of an existing section. Same as write if fresh."""
    return write_section(session_id, section_id, content)


def get_section_status(session_id: str) -> dict:
    """Return plan with completion status and word counts."""
    state = get_session(session_id)
    if state is None:
        return {"error": f"Session {session_id} not found"}

    plan_progress = []
    for s in state["plan"]:
        content = state["sections"].get(s["id"])
        plan_progress.append({
            "id": s["id"],
            "title": s["title"],
            "status": "complete" if content else "pending",
            "words": len(content.split()) if content else 0,
        })

    completed = sum(1 for p in plan_progress if p["status"] == "complete")
    total = len(plan_progress)

    return {
        "session_id": session_id,
        "status": state["status"],
        "progress": f"{completed}/{total}",
        "completed": completed,
        "total": total,
        "plan": plan_progress,
        "path": state["path"],
    }


def finalize_session(session_id: str, format: str = "md") -> Optional[str]:
    """Finalize a document. Collect sections, generate TOC, write file.

    Args:
        session_id: Session to finalize.
        format: Output format ('md', 'pdf', 'docx').

    Returns:
        Path to the generated file, or error string on failure.
    """
    state = get_session(session_id)
    if state is None:
        return f"Session {session_id} not found"

    # Check all sections complete
    section_ids = {s["id"] for s in state["plan"]}
    written = set(state["sections"].keys())
    missing = section_ids - written
    if missing:
        return f"Cannot finalize: sections missing: {', '.join(sorted(missing))}"

    # Build document
    lines = []
    # TOC
    lines.append("# Содержание\n")
    for i, s in enumerate(state["plan"], 1):
        title = s.get("title", s["id"])
        lines.append(f"{i}. [{title}](#{s['id']})")
    lines.append("")
    lines.append("---\n")

    # Sections
    for s in state["plan"]:
        section_id = s["id"]
        title = s.get("title", section_id)
        content = state["sections"].get(section_id, "")
        lines.append(f"## {title}\n")
        lines.append(content.strip())
        lines.append("")

    document = "\n".join(lines)

    # Write to target path
    target = Path(state["path"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(document)

    # Update state
    state["status"] = "complete"
    state["updated"] = datetime.now(timezone.utc).isoformat()
    store.save_session(state)

    return str(target)


def resume_session(path: str) -> Optional[dict]:
    """Find and resume a session for the given file path."""
    sessions = store.list_sessions()
    for s in sessions:
        if s.get("path") == path and s.get("status") == "in_progress":
            sid = s["session_id"]
            # Load full state including content
            state = store.load_session(sid)
            if state:
                # Reload content from disk (in case cache was lost)
                disk_content = store.load_all_content(sid)
                state["sections"] = disk_content
                _SESSION_CACHE[sid] = state
                return state
    return None


def cancel_session(session_id: str) -> Optional[str]:
    """Cancel a session (mark as cancelled, keep on disk)."""
    state = get_session(session_id)
    if state is None:
        return f"Session {session_id} not found"
    state["status"] = "cancelled"
    state["updated"] = datetime.now(timezone.utc).isoformat()
    store.save_session(state)
    return None
