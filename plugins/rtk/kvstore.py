"""
plugins/rtk/kvstore.py — Session-scoped key-value store for RTK metadata.

Structured as:  ~/.autolycus/rtk-cache/{session_id}/{key}.json

Thread-safe via threading.Lock. Intended for:
  - Verification claims / flags (verifier)
  - Pattern detector signals
  - Per-session configuration overrides
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_DEFAULT_BASE = "~/.autolycus/rtk-cache"


def _resolve_base(base_dir: Optional[str] = None) -> Path:
    p = Path(base_dir or _DEFAULT_BASE).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_dir(session_id: str, base_dir: Optional[str] = None) -> Path:
    base = _resolve_base(base_dir)
    sd = base / session_id
    sd.mkdir(parents=True, exist_ok=True)
    return sd


def put(
    session_id: str,
    key: str,
    data: Any,
    base_dir: Optional[str] = None,
) -> bool:
    """Store a value under ``key`` for the given session.

    Args:
        session_id: Session identifier.
        key: Namespace key (e.g. 'claims', 'flags', 'signal', 'usage').
        data: JSON-serialisable value.
        base_dir: Override cache root (default: ~/.autolycus/rtk-cache).

    Returns:
        True on success.
    """
    with _lock:
        try:
            sd = _session_dir(session_id, base_dir)
            path = sd / f"{key}.json"
            path.write_text(
                json.dumps(data, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            logger.debug("RTK/kvstore: wrote %s/%s (%d bytes)", session_id, key, len(str(data)))
            return True
        except Exception as exc:
            logger.warning("RTK/kvstore: put failed %s/%s: %s", session_id, key, exc)
            return False


def get(
    session_id: str,
    key: str,
    base_dir: Optional[str] = None,
) -> Optional[Any]:
    """Read a value previously stored with ``put``.

    Returns None if the key doesn't exist or can't be parsed.
    """
    with _lock:
        try:
            sd = _session_dir(session_id, base_dir)
            path = sd / f"{key}.json"
            if not path.exists():
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("RTK/kvstore: get failed %s/%s: %s", session_id, key, exc)
            return None


def delete(
    session_id: str,
    key: str,
    base_dir: Optional[str] = None,
) -> bool:
    """Remove a single key for a session."""
    with _lock:
        try:
            sd = _session_dir(session_id, base_dir)
            path = sd / f"{key}.json"
            if path.exists():
                path.unlink()
            return True
        except Exception as exc:
            logger.debug("RTK/kvstore: delete failed %s/%s: %s", session_id, key, exc)
            return False


def delete_session(session_id: str, base_dir: Optional[str] = None) -> bool:
    """Remove ALL data for a session (the entire directory)."""
    with _lock:
        try:
            base = _resolve_base(base_dir)
            sd = base / session_id
            if sd.exists() and sd.is_dir():
                import shutil
                shutil.rmtree(sd)
                logger.debug("RTK/kvstore: deleted session %s", session_id)
            return True
        except Exception as exc:
            logger.warning("RTK/kvstore: delete_session failed %s: %s", session_id, exc)
            return False


def list_keys(session_id: str, base_dir: Optional[str] = None) -> List[str]:
    """List all keys stored for a session."""
    with _lock:
        try:
            sd = _session_dir(session_id, base_dir)
            return sorted(
                f.stem for f in sd.iterdir() if f.suffix == ".json"
            )
        except Exception:
            return []


def list_sessions(base_dir: Optional[str] = None) -> List[str]:
    """List all session IDs that have stored data."""
    with _lock:
        try:
            base = _resolve_base(base_dir)
            return sorted(
                d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")
            )
        except Exception:
            return []


def get_usage_budget(
    session_id: str,
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience: read the 'usage' key with defaults."""
    usage = get(session_id, "usage", base_dir)
    if usage is None:
        return {"spent": 0.0, "budget": 0.0, "remaining": 0.0}
    budget = usage.get("budget", 10.0)
    spent = usage.get("spent", 0.0)
    return {
        "spent": spent,
        "budget": budget,
        "remaining": max(0.0, budget - spent),
    }
