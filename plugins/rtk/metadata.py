"""
plugins/rtk/metadata.py — RTK metadata integration with state.db.

Adds per-tool-call compression metadata to the existing messages table.
Uses the auto-migration system in SessionDB — adding the column to
SCHEMA_SQL is handled separately; this module reads and writes it.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Column name in the messages table
_RTK_META_COL = "rtk_metadata"

# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def build_metadata(
    tool_name: str,
    persist_id: str,
    original_len: int,
    compressed_len: int,
    strategy: str = "head_tail",
    error: bool = False,
    duration_ms: float = 0.0,
) -> str:
    """Build the JSON string for the rtk_metadata column.

    Args:
        tool_name: Name of the tool called.
        persist_id: UUID from store.save().
        original_len: Length of raw tool output in chars.
        compressed_len: Length after compression.
        strategy: Compression strategy used.
        error: Whether the tool call resulted in an error.
        duration_ms: Wall-clock duration of the compression step.

    Returns:
        JSON string to store in messages.rtk_metadata.
    """
    chars_saved = original_len - compressed_len
    savings_pct = 0.0
    if original_len > 0:
        savings_pct = round((chars_saved / original_len) * 100, 1)

    data = {
        "persist_id": persist_id,
        "chars_saved": chars_saved,
        "original_len": original_len,
        "compressed_len": compressed_len,
        "savings_pct": savings_pct,
        "strategy": strategy,
        "tool": tool_name,
        "error": error,
        "ts": time.time(),
        "duration_ms": round(duration_ms, 3),
    }
    return json.dumps(data, ensure_ascii=False)


def attach_to_message(
    db_session: Any,
    message_id: int,
    metadata_json: str,
) -> bool:
    """Attach RTK metadata to an existing message row by message id.

    Args:
        db_session: An active SessionDB instance (hermes_state.SessionDB).
        message_id: The messages.id to update.
        metadata_json: JSON string from build_metadata().

    Returns:
        True on success, False on failure.
    """
    try:
        db_session._conn.execute(
            f'UPDATE messages SET "{_RTK_META_COL}" = ? WHERE id = ?',
            (metadata_json, message_id),
        )
        db_session._conn.commit()
        return True
    except Exception as exc:
        logger.debug("RTK/metadata: attach failed for msg %s: %s", message_id, exc)
        return False


def attach_by_tool_call_id(
    db_session: Any,
    session_id: str,
    tool_call_id: str,
    metadata_json: str,
) -> bool:
    """Attach RTK metadata to a message row by tool_call_id.

    Finds the message with matching session_id and tool_call_id.
    This is the primary method used by transform_tool_result hook,
    which has access to session_id and tool_call_id but not message_id.
    """
    try:
        cursor = db_session._conn.execute(
            f"""UPDATE messages SET "{_RTK_META_COL}" = ?
                WHERE session_id = ? AND tool_call_id = ?
                AND role = 'tool'""",
            (metadata_json, session_id, tool_call_id),
        )
        db_session._conn.commit()
        affected = cursor.rowcount
        if affected == 0:
            logger.debug(
                "RTK/metadata: no message found for %s/%s",
                session_id, tool_call_id,
            )
            return False
        return True
    except Exception as exc:
        logger.debug(
            "RTK/metadata: attach_by_tool_call_id failed %s/%s: %s",
            session_id, tool_call_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def get_metadata(db_session: Any, message_id: int) -> Optional[Dict[str, Any]]:
    """Read RTK metadata for a specific message.

    Args:
        db_session: An active SessionDB instance.
        message_id: The messages.id.

    Returns:
        Parsed dict, or None if not found/not parseable.
    """
    try:
        row = db_session._conn.execute(
            f'SELECT "{_RTK_META_COL}" FROM messages WHERE id = ?',
            (message_id,),
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as exc:
        logger.debug("RTK/metadata: read failed for msg %s: %s", message_id, exc)
    return None


def get_tool_sequence(
    db_session: Any,
    session_id: str,
    limit: int = 20,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Return the most recent tool-call messages with their RTK metadata.

    Queries messages where role='tool', ordered by id DESC (newest first).
    Only returns rows that HAVE rtk_metadata (i.e. tool calls processed by RTK).

    Args:
        db_session: An active SessionDB instance.
        session_id: Session to query.
        limit: Max rows to return.
        offset: Skip first N rows (for pagination).

    Returns:
        List of dicts: {id, tool_name, timestamp, rtk_metadata, content_preview}
        where rtk_metadata is a parsed dict.
    """
    try:
        rows = db_session._conn.execute(
            f"""
            SELECT id, tool_name, timestamp, "{_RTK_META_COL}",
                   substr(content, 1, 200) AS content_preview
            FROM messages
            WHERE session_id = ? AND role = 'tool'
              AND "{_RTK_META_COL}" IS NOT NULL
              AND "{_RTK_META_COL}" != ''
            ORDER BY id DESC
            LIMIT ? OFFSET ?
            """,
            (session_id, limit, offset),
        ).fetchall()

        result = []
        for row in rows:
            meta = json.loads(row[3]) if row[3] else {}
            result.append({
                "id": row[0],
                "tool_name": row[1],
                "timestamp": row[2],
                "rtk_metadata": meta,
                "content_preview": (row[4] or "")[:200],
            })
        return result
    except Exception as exc:
        logger.debug("RTK/metadata: get_tool_sequence failed: %s", exc)
        return []


def count_processed_calls(db_session: Any, session_id: str) -> int:
    """Count tool messages with RTK metadata for a session."""
    try:
        row = db_session._conn.execute(
            f"""
            SELECT COUNT(*) FROM messages
            WHERE session_id = ? AND role = 'tool'
              AND "{_RTK_META_COL}" IS NOT NULL
              AND "{_RTK_META_COL}" != ''
            """,
            (session_id,),
        ).fetchone()
        return row[0] if row else 0
    except Exception:
        return 0


def get_session_error_rate(db_session: Any, session_id: str, limit: int = 20) -> float:
    """Return the error ratio (0.0-1.0) in the last N tool calls with RTK metadata.

    1.0 = all errors, 0.0 = none.
    """
    seq = get_tool_sequence(db_session, session_id, limit=limit)
    if not seq:
        return 0.0
    errors = sum(1 for s in seq if s.get("rtk_metadata", {}).get("error", False))
    return errors / len(seq)


def get_recent_errors(
    db_session: Any,
    session_id: str,
    count: int = 3,
) -> List[Dict[str, Any]]:
    """Return the last N consecutive error tool calls.

    Stops at the first non-error call.

    Returns:
        List of error metadata dicts, newest first.
    """
    seq = get_tool_sequence(db_session, session_id, limit=count * 3)
    errors = []
    for s in seq:
        meta = s.get("rtk_metadata", {})
        if meta.get("error", False):
            errors.append(s)
            if len(errors) >= count:
                break
        else:
            break  # stop at first non-error
    return errors


def get_session_cost(db_session: Any, session_id: str) -> float:
    """Return estimated_cost_usd from sessions table."""
    try:
        row = db_session._conn.execute(
            "SELECT estimated_cost_usd FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return row[0] if row and row[0] else 0.0
    except Exception:
        return 0.0
