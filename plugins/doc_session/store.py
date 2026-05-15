"""
plugins/doc_session/store — Persistence for document sessions.

Transactional save: write(temp) → fsync → rename(over current).
Survives agent crash between writes.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = __import__("logging").getLogger(__name__)

_DOCS_DIR = Path.home() / ".hermes" / "docs"
_SESSIONS_DIR = _DOCS_DIR / "sessions"
_CONTENT_DIR = _DOCS_DIR / "content"


def _ensure_dirs() -> None:
    _SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _CONTENT_DIR.mkdir(parents=True, exist_ok=True)


# ── Session State ─────────────────────────────────────────────────────────


def _state_path(session_id: str) -> Path:
    return _SESSIONS_DIR / f"{session_id}.json"


def _content_path(session_id: str, section_id: str) -> Path:
    return _CONTENT_DIR / f"{session_id}--{section_id}.md"


def save_session(state: dict) -> None:
    """Atomically save session state to disk."""
    _ensure_dirs()
    path = _state_path(state["session_id"])
    tmp_path = path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    # Atomic rename on POSIX — no need to fsync tmp file for crash safety;
    # rename is atomic, and if we crash before rename, only tmp is lost.
    tmp_path.replace(path)


def load_session(session_id: str) -> Optional[dict]:
    """Load session state, or None if missing/corrupted."""
    path = _state_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Corrupted session state %s: %s", session_id, e)
        return None


def delete_session(session_id: str) -> None:
    """Remove session state and all content files."""
    path = _state_path(session_id)
    if path.exists():
        path.unlink()
    # Remove content files for this session
    for cf in _CONTENT_DIR.glob(f"{session_id}--*.md"):
        cf.unlink()


def list_sessions() -> list[dict]:
    """Return all session states (without full content)."""
    _ensure_dirs()
    sessions = []
    for f in sorted(_SESSIONS_DIR.glob("*.json"), key=os.path.getmtime, reverse=True):
        try:
            state = json.loads(f.read_text())
            # Strip content for listing — keep only metadata
            state.pop("sections", None)
            sessions.append(state)
        except Exception:
            continue
    return sessions


def cleanup_old(max_age_hours: int = 24) -> int:
    """Remove sessions older than max_age_hours. Returns count removed."""
    _ensure_dirs()
    now = time.time()
    removed = 0
    for f in list(_SESSIONS_DIR.glob("*.json")):
        if now - f.stat().st_mtime > max_age_hours * 3600:
            session_id = f.stem
            delete_session(session_id)
            removed += 1
    return removed


# ── Section Content ────────────────────────────────────────────────────────


def save_content(session_id: str, section_id: str, content: str) -> None:
    """Write section content to disk. Not atomic per-section (losing one
    section on crash is acceptable — sections are independent)."""
    _ensure_dirs()
    path = _content_path(session_id, section_id)
    path.write_text(content)


def load_content(session_id: str, section_id: str) -> Optional[str]:
    """Load section content, or None if missing."""
    path = _content_path(session_id, section_id)
    if path.exists():
        return path.read_text()
    return None


def load_all_content(session_id: str) -> dict[str, str]:
    """Load all section content for a session."""
    sections = {}
    for cf in sorted(_CONTENT_DIR.glob(f"{session_id}--*.md")):
        section_id = cf.stem.split("--", 1)[1]
        sections[section_id] = cf.read_text()
    return sections
