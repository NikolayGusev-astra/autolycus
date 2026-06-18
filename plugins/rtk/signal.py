"""
plugins/rtk/signal.py — Pre-turn signal injector for RTK pattern detector.

Writes detected signals to the kvstore and provides the injection text
for the system prompt. The injection is consumed by run_agent.py's
pre-turn logic.

Flow:
  1. pattern.detect() → Signal
  2. signal.store(sid, signal) → kvstore.put(sid, "signal", {...})
  3. signal.inject(sid) → str (read from kvstore, format for system prompt)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from plugins.rtk import kvstore, pattern
from plugins.rtk.pattern import Signal

logger = logging.getLogger(__name__)

_SIGNAL_KEY = "rtk_signal"

# ---------------------------------------------------------------------------
# Store / Clear
# ---------------------------------------------------------------------------


def store(session_id: str, signal: Signal) -> bool:
    """Persist a signal to the kvstore for the given session.

    Each store() overwrites the previous signal for that session.
    """
    data = {
        "code": signal.code,
        "severity": signal.severity,
        "message": signal.message,
        "count": signal.count,
        "detail": signal.detail,
        "injection": signal.to_injection(),
        "should_halt": signal.should_halt,
    }
    return kvstore.put(session_id, _SIGNAL_KEY, data)


def clear(session_id: str) -> bool:
    """Remove any stored signal for a session (acknowledged/resolved)."""
    return kvstore.delete(session_id, _SIGNAL_KEY)


# ---------------------------------------------------------------------------
# Read / Inject
# ---------------------------------------------------------------------------


def read(session_id: str) -> Optional[Dict[str, Any]]:
    """Read the current stored signal data."""
    return kvstore.get(session_id, _SIGNAL_KEY)


def get_injection(session_id: str) -> str:
    """Return the system-prompt injection text (empty string if no signal).

    This is called at the START of the next turn. If a signal was stored
    by the pattern detector, its injection text is returned.

    The injection is auto-cleared after reading (one-shot).
    """
    data = read(session_id)
    if data is None:
        return ""

    injection = data.get("injection", "")
    if injection:
        clear(session_id)

    return injection


# ---------------------------------------------------------------------------
# Auto-detect and inject (convenience)
# ---------------------------------------------------------------------------


def detect_and_store(
    db_session: Any,
    session_id: str,
    budget_limit: float = 10.0,
    error_threshold: int = 3,
) -> Optional[Signal]:
    """Run pattern detection and store the best signal.

    Returns the Signal if one was found and stored, None otherwise.
    """
    sig = pattern.best_signal(
        db_session, session_id,
        budget_limit=budget_limit,
        error_threshold=error_threshold,
    )
    if sig:
        store(session_id, sig)
        logger.info(
            "RTK/signal: %s/%s — %s",
            session_id, sig.code, sig.message,
        )
    return sig


def pre_turn(
    db_session: Any,
    session_id: str,
    budget_limit: float = 10.0,
    error_threshold: int = 3,
) -> tuple[str, bool]:
    """Full pre-turn pipeline: detect → store → inject.

    Returns (injection_text, should_halt) tuple.
    Empty injection text = nothing to inject.
    should_halt=True → circuit breaker should stop the session.

    Call this at the start of each turn (before the LLM call).
    """
    # 1. Check for existing unread signal
    existing = get_injection(session_id)
    if existing:
        data = read(session_id)
        halt = data.get("should_halt", False) if data else False
        return existing, halt

    # 2. Run detectors
    sig = detect_and_store(db_session, session_id, budget_limit, error_threshold)

    # 3. Return new signal
    inj = get_injection(session_id)
    halt = sig.should_halt if sig else False
    return inj, halt
