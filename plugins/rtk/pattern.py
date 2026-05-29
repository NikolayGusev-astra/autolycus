"""
plugins/rtk/pattern.py — Semantic pattern detection over RTK metadata.

Reads the tool call sequence from state.db (via metadata.py) and detects:

  1. CONSECUTIVE_ERRORS — N identical-failing tool calls (same tool + same output)
  2. TOOL_LOOP — alternating tool→error→same_tool→error→same_tool
  3. BUDGET_EXCEEDED — session cost exceeded configured limit
  4. NO_PROGRESS — read-only tools returning identical error text

Each detector returns a Signal (or None) with severity and message.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from plugins.rtk import metadata as rtk_meta

logger = logging.getLogger(__name__)


def _flush_rtk_metadata() -> None:
    """Flush pending RTK metadata from the hook buffer to state.db."""
    try:
        from plugins.rtk import flush_pending_metadata
        flush_pending_metadata()
    except Exception:
        pass


@dataclass(frozen=True)
class Signal:
    """A structured signal from the pattern detector."""

    code: str  # e.g. "CONSECUTIVE_ERRORS", "TOOL_LOOP", "BUDGET_EXCEEDED"
    severity: str  # "info" | "warn" | "critical"
    message: str  # Human-readable, fits in system prompt
    count: int = 0
    detail: Dict[str, Any] = field(default_factory=dict)
    should_halt: bool = False  # True → circuit breaker: stop the session

    def to_injection(self) -> str:
        """Return the 1-2 line text for system prompt injection.

        Returns empty string for 'info' severity (no injection needed).
        """
        if self.severity == "info":
            return ""
        icon = {"warn": "⚠", "critical": "🔴"}.get(self.severity, "ℹ")
        return f"{icon} RTK/{self.code}: {self.message}"

    def __bool__(self) -> bool:
        return self.severity in ("warn", "critical")


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------


def detect_consecutive_errors(
    db_session: Any,
    session_id: str,
    threshold: int = 3,
) -> Optional[Signal]:
    """Check if the same tool call (same tool + same output) repeats N times.

    Distinguishes loops from sequential problem-solving:
    - Loop: same tool_name + same content_preview N times → halt
    - Problem-solving: different tools or different output → not a loop

    Flushes pending RTK metadata to state.db before reading.

    Args:
        threshold: Number of consecutive identical errors to trigger (default: 3).

    Returns:
        Signal if threshold met, else None.
    """
    # Ensure pending metadata is written to state.db
    _flush_rtk_metadata()

    # Fetch extra rows to check for repeated patterns among errors.
    # get_tool_sequence returns newest-first (DESC), so we reverse to get chronological order.
    seq = rtk_meta.get_tool_sequence(db_session, session_id, limit=max(threshold * 3, 12))
    if not seq:
        return None

    # Reverse to chronological order (oldest first)
    seq_chronological = list(reversed(seq))

    # Walk through and count consecutive identical-failing calls.
    # A "loop key" = (tool_name, content_preview) — if both match,
    # it's the same call repeating. Different key = different approach, reset counter.
    max_repeats = 0
    current_repeats = 0
    prev_loop_key = None
    looped_tool = None
    total_errors = 0

    for s in seq_chronological:
        meta = s.get("rtk_metadata", {})
        is_error = meta.get("error", False)
        if not is_error:
            current_repeats = 0
            prev_loop_key = None
            continue

        total_errors += 1
        tool_name = s.get("tool_name", "?")
        preview = s.get("content_preview", "")[:200]
        loop_key = f"{tool_name}:{preview}"

        if loop_key == prev_loop_key:
            current_repeats += 1
        else:
            current_repeats = 1

        if current_repeats > max_repeats:
            max_repeats = current_repeats
            looped_tool = tool_name

        prev_loop_key = loop_key

    if max_repeats >= threshold:
        return Signal(
            code="CONSECUTIVE_ERRORS",
            severity="critical",
            message=f"{max_repeats} одинаковых ошибок подряд ({looped_tool}). Зацикливание — прерви стратегию.",
            count=max_repeats,
            detail={"tool": looped_tool, "total_errors": total_errors, "total_calls": len(seq)},
            should_halt=True,
        )

    return None


def detect_tool_loop(
    db_session: Any,
    session_id: str,
    window: int = 6,
) -> Optional[Signal]:
    """Detect tool→error→same_tool→error cycles.

    Looks for: 3+ occurrences of the same tool name in the last N calls,
    where at least 2 had errors.

    Args:
        window: How many recent calls to scan.

    Returns:
        Signal if loop detected, else None.
    """
    seq = rtk_meta.get_tool_sequence(db_session, session_id, limit=window)
    if len(seq) < 4:
        return None

    tool_counts: Dict[str, int] = {}
    error_counts: Dict[str, int] = {}

    for s in seq:
        tool = s.get("tool_name", "?")
        meta = s.get("rtk_metadata", {})
        tool_counts[tool] = tool_counts.get(tool, 0) + 1
        if meta.get("error", False):
            error_counts[tool] = error_counts.get(tool, 0) + 1

    # Find tool with 3+ appearances and 2+ errors
    for tool, count in tool_counts.items():
        err_count = error_counts.get(tool, 0)
        if count >= 3 and err_count >= 2:
            return Signal(
                code="TOOL_LOOP",
                severity="warn",
                message=f"Зацикливание: {tool} вызван {count} раз, {err_count} с ошибками.",
                count=count,
                detail={"tool": tool, "total_calls": count, "errors": err_count},
            )
    return None


def detect_budget_exceeded(
    db_session: Any,
    session_id: str,
    budget_limit: float = 10.0,
) -> Optional[Signal]:
    """Check if the session has exceeded its cost budget.

    Reads from state.db sessions.estimated_cost_usd.

    Args:
        budget_limit: Maximum cost in USD before warning (default: $10).

    Returns:
        Signal if exceeded, else None.
    """
    cost = rtk_meta.get_session_cost(db_session, session_id)
    if cost >= budget_limit:
        return Signal(
            code="BUDGET_EXCEEDED",
            severity="critical",
            message=f"Бюджет ${budget_limit:.1f} превышен (${cost:.2f}). Оптимизируй вызовы.",
            count=round(cost),
            detail={"spent": cost, "budget": budget_limit},
            should_halt=True,
        )
    if cost >= budget_limit * 0.8:
        return Signal(
            code="BUDGET_WARNING",
            severity="warn",
            message=f"80% бюджета израсходовано (${cost:.2f} из ${budget_limit:.1f}).",
            count=round(cost),
            detail={"spent": cost, "budget": budget_limit},
        )
    return None


def detect_no_progress(
    db_session: Any,
    session_id: str,
    threshold: int = 3,
    similarity_threshold: float = 0.85,
) -> Optional[Signal]:
    """Detect repeated similar content from read-only tools.

    Uses fuzzy string matching (difflib.SequenceMatcher) to catch
    semantically identical calls with slightly different text.

    Args:
        db_session: Active SessionDB instance.
        session_id: Session to scan.
        threshold: Number of consecutive similar calls to trigger (default: 3).
        similarity_threshold: Minimum ratio for "same content" (0.0-1.0, default: 0.85).

    Returns:
        Signal if no-progress detected, else None.
    """
    seq = rtk_meta.get_tool_sequence(db_session, session_id, limit=threshold + 2)
    if len(seq) < threshold:
        return None

    # Group by tool name, check for similar content previews (fuzzy match)
    tool_previews = [(s["tool_name"], s.get("content_preview", "")[:200]) for s in seq[:threshold]]

    if len(tool_previews) < threshold:
        return None

    first_tool, first_preview = tool_previews[0]
    if not first_preview:
        return None

    # Compare all pairs — use the minimum similarity ratio
    all_same_tool = all(t == first_tool for t, _ in tool_previews)
    if not all_same_tool:
        return None

    similarities = []
    for _, preview in tool_previews[1:]:
        ratio = difflib.SequenceMatcher(None, first_preview, preview).ratio()
        similarities.append(ratio)

    min_similarity = min(similarities) if similarities else 1.0
    if min_similarity >= similarity_threshold:
        return Signal(
            code="NO_PROGRESS",
            severity="warn",
            message=f"{threshold} однотипных вызовов {first_tool} ({min_similarity:.0%} схожести). Измени запрос.",
            count=threshold,
            detail={
                "tool": first_tool,
                "min_similarity": round(min_similarity, 4),
                "threshold": similarity_threshold,
                "preview": first_preview[:100],
            },
        )
    return None


# ---------------------------------------------------------------------------
# Composite detector — run all checks
# ---------------------------------------------------------------------------

# Ordered by severity (critical first)
_DETECTORS: List[Callable[..., Optional[Signal]]] = [
    detect_consecutive_errors,
    detect_budget_exceeded,
    detect_tool_loop,
    detect_no_progress,
]


def run_all(
    db_session: Any,
    session_id: str,
    budget_limit: float = 10.0,
    error_threshold: int = 3,
    similarity_threshold: float = 0.85,
) -> List[Signal]:
    """Run all pattern detectors. Returns list of non-None signals.

    Args:
        db_session: Active SessionDB instance.
        session_id: Session to scan.
        budget_limit: USD limit for budget detector.
        error_threshold: Error count for consecutive_errors detector.
        similarity_threshold: Similarity ratio for no_progress detector (0.0-1.0).

    Returns:
        List of Signal objects, sorted by severity (critical first).
        Empty list = nothing detected.
    """
    signals = []
    for detector in _DETECTORS:
        try:
            kwargs = {}
            if detector.__name__ == "detect_budget_exceeded":
                kwargs["budget_limit"] = budget_limit
            elif detector.__name__ in ("detect_consecutive_errors", "detect_no_progress"):
                kwargs["threshold"] = error_threshold
            if detector.__name__ == "detect_no_progress":
                kwargs["similarity_threshold"] = similarity_threshold
            sig = detector(db_session, session_id, **kwargs)
            if sig:
                signals.append(sig)
        except Exception as exc:
            logger.debug("RTK/pattern: %s failed: %s", detector.__name__, exc)
    return signals


def best_signal(
    db_session: Any,
    session_id: str,
    budget_limit: float = 10.0,
    error_threshold: int = 3,
    similarity_threshold: float = 0.85,
) -> Optional[Signal]:
    """Return the most severe signal, or None.

    'critical' > 'warn' > 'info'. If multiple at same severity,
    returns the first one found.
    """
    signals = run_all(db_session, session_id, budget_limit, error_threshold, similarity_threshold)
    for s in signals:
        if s.severity == "critical":
            return s
    for s in signals:
        if s.severity == "warn":
            return s
    return signals[0] if signals else None
