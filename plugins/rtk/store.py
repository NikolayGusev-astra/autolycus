"""
plugins/rtk/store.py — Persistence layer for RTK filter.

Saves full tool results to disk, returns a recoverable persist_id.
Full data is always preserved — compression is best-effort on top.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_DIR = "~/.autolycus/rtk-cache"


def _resolve_cache_dir(cache_dir: Optional[str] = None) -> Path:
    """Resolve cache directory, creating it if needed."""
    if cache_dir:
        p = Path(cache_dir)
    else:
        p = Path(_DEFAULT_CACHE_DIR).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p


def save(data: str, cache_dir: Optional[str] = None) -> str:
    """Save *data* to disk, return a unique persist_id.

    The file is written as ``{cache_dir}/{persist_id}.txt``.
    """
    persist_id = str(uuid.uuid4())
    cache = _resolve_cache_dir(cache_dir)
    path = cache / f"{persist_id}.txt"
    try:
        path.write_text(data, encoding="utf-8")
        logger.debug("RTK/store: saved %d bytes → %s", len(data), path)
    except OSError as exc:
        logger.warning("RTK/store: failed to save %s: %s", path, exc)
        raise
    return persist_id


def load(persist_id: str, cache_dir: Optional[str] = None) -> Optional[str]:
    """Load data by *persist_id*. Returns None if not found."""
    cache = _resolve_cache_dir(cache_dir)
    path = cache / f"{persist_id}.txt"
    if not path.exists():
        logger.debug("RTK/store: persist_id not found: %s", persist_id)
        return None
    try:
        data = path.read_text(encoding="utf-8")
        logger.debug("RTK/store: loaded %d bytes from %s", len(data), path)
        return data
    except OSError as exc:
        logger.warning("RTK/store: failed to load %s: %s", path, exc)
        return None


def resolve_path(persist_id: str, cache_dir: Optional[str] = None) -> Optional[str]:
    """Return the absolute file path for a persist_id (for recovery instructions)."""
    cache = _resolve_cache_dir(cache_dir)
    path = cache / f"{persist_id}.txt"
    return str(path) if path.exists() else None


def cleanup(max_age_days: int = 30, cache_dir: Optional[str] = None) -> int:
    """Remove cached files older than *max_age_days*. Returns count removed."""
    import time
    cache = _resolve_cache_dir(cache_dir)
    now = time.time()
    cutoff = now - (max_age_days * 86400)
    removed = 0
    for f in cache.glob("*.txt"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("RTK/store: cleaned %d old cache files", removed)
    return removed
