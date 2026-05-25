"""RTK-CK PatternDetector — sequence-level pattern detection.

Detects:
- REDUNDANT_READS: 3+ identical read_file calls in the conversation
- STALLED_SESSION: 3+ consecutive tool→error cycles (any tools)

Pure functions. No I/O.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict, List, Optional

from plugins.rtk.pattern import Signal

DEFAULT_REDUNDANT_READ_THRESHOLD = 3
DEFAULT_STALLED_THRESHOLD = 3

# Tool results containing error indicators
_ERROR_INDICATORS = ("error", "traceback", "exception", "failed", "timeout", "denied", "forbidden")


def _is_tool_result_error(content: str) -> bool:
    """Heuristic: does this tool result look like an error?"""
    if not content:
        return False
    lower = content.lower()
    for indicator in _ERROR_INDICATORS:
        if indicator in lower:
            return True
    return False


def _extract_read_path(tool_call: Optional[dict]) -> Optional[str]:
    """Extract the path argument from a read_file tool call."""
    if not isinstance(tool_call, dict):
        return None
    func = tool_call.get("function")
    if not isinstance(func, dict):
        return None
    if func.get("name") != "read_file":
        return None
    try:
        args = json.loads(func.get("arguments", "{}"))
        return args.get("path")
    except (json.JSONDecodeError, TypeError):
        return None


def _find_errors_in_messages(messages: list) -> int:
    """Count consecutive error cycles (tool→error→tool→error pattern).

    Counts how many tool results look like errors. If a non-error
    tool result appears, the consecutive counter resets.
    """
    consecutive_errors = 0
    max_errors = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "tool":
            is_error = _is_tool_result_error(msg.get("content", ""))
            if is_error:
                consecutive_errors += 1
                max_errors = max(max_errors, consecutive_errors)
            else:
                consecutive_errors = 0
    return max_errors


class PatternDetector:
    """Detects sequence-level patterns in conversation messages."""

    @staticmethod
    def detect(
        messages: List[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
    ) -> List[Signal]:
        """Run all pattern detectors against messages.

        Args:
            messages: Full conversation history.
            config: Override thresholds.

        Returns:
            List of Signals (empty if none detected).
        """
        signals: List[Signal] = []
        if not messages:
            return signals

        cfg = config or {}
        redundant_threshold = cfg.get("redundant_read_threshold", DEFAULT_REDUNDANT_READ_THRESHOLD)
        stalled_threshold = cfg.get("stalled_threshold", DEFAULT_STALLED_THRESHOLD)

        # 1. REDUNDANT_READS: count read_file calls by path
        read_paths: Counter = Counter()
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                continue
            for tc in tool_calls if isinstance(tool_calls, list) else [tool_calls]:
                path = _extract_read_path(tc)
                if path:
                    read_paths[path] += 1

        for path, count in read_paths.items():
            if count >= redundant_threshold:
                signals.append(Signal(
                    code="REDUNDANT_READS",
                    severity="warn",
                    message=(
                        f"File '{path}' was read {count} times. "
                        f"The agent may be re-reading the same content."
                    ),
                    count=count,
                ))

        # 2. STALLED_SESSION: consecutive error cycles
        error_count = _find_errors_in_messages(messages)
        if error_count >= stalled_threshold:
            signals.append(Signal(
                code="STALLED_SESSION",
                severity="critical",
                message=(
                    f"Detected {error_count} consecutive error cycles. "
                    f"The session is stalled — consider a different approach."
                ),
                count=error_count,
                should_halt=True,
            ))

        return signals