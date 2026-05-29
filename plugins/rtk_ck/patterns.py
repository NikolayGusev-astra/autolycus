"""RTK-CK PatternDetector — sequence-level pattern detection.

Detects:
- REDUNDANT_READS: 3+ identical read_file calls in the conversation
- STALLED_SESSION: 3+ consecutive identical-failing tool calls (same tool+args)

Pure functions. No I/O.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from plugins.rtk.pattern import Signal

DEFAULT_REDUNDANT_READ_THRESHOLD = 3
DEFAULT_STALLED_THRESHOLD = 3

# Patterns that look like errors but are actually normal tool output
_FALSE_POSITIVE_PATTERNS = (
    r'"error"\s*:\s*(null|false|0)\b',
    r'"exit_code"\s*:\s*0\b',
    r'"exit_code"\s*:\s*"0"',
    r'stderr\s*:\s*["\']["\']',
    r'an error occurred.*resolved',
    r'no error',
    r'without error',
    r'error[-_]?free',
)

# Strong error indicators (high confidence)
_STRONG_ERRORS = ("traceback", "exception:", "failed with exit", "denied", "forbidden", "refused")

# Weak error indicators (only count if not a false positive)
_WEAK_ERRORS = ("error:", "failed:", "timeout")


def _is_tool_result_error(content: str) -> bool:
    """Heuristic: does this tool result look like a real tool error?

    Distinguishes actual failures (Traceback, command failed, denied)
    from normal output that happens to contain the word "error"
    (e.g. "error": null, exit_code: 0, stderr: "").
    """
    if not content:
        return False

    stripped = content.strip()
    lower = stripped.lower()

    # 1. Strong signals — always count as errors
    for indicator in _STRONG_ERRORS:
        if indicator in lower:
            return True

    # 2. Check for command failure patterns: "exit_code: N" where N != 0
    exit_match = re.search(r'"exit_code"\s*:\s*(\d+)', stripped)
    if exit_match:
        code = int(exit_match.group(1))
        if code != 0:
            return True

    # 3. Check for real "error" messages (not JSON null/false)
    for pattern in _FALSE_POSITIVE_PATTERNS:
        if re.search(pattern, lower):
            return False

    # 4. Weak signals — "error:", "failed:", "timeout" — only if not filtered above
    for indicator in _WEAK_ERRORS:
        if indicator in lower:
            return True

    return False


def _extract_read_path(tool_call: Optional[dict]) -> Optional[str]:
    """Extract the path + offset + limit from a read_file tool call.

    Includes offset/limit so that reading the same file in chunks
    (different offsets) is NOT flagged as REDUNDANT_READS.
    """
    if not isinstance(tool_call, dict):
        return None
    func = tool_call.get("function")
    if not isinstance(func, dict):
        return None
    if func.get("name") != "read_file":
        return None
    try:
        args = json.loads(func.get("arguments", "{}"))
        path = args.get("path")
        if not path:
            return None
        # Include offset/limit in the key so chunked reads are not duplicates
        offset = args.get("offset", 1)
        limit = args.get("limit", 500)
        return f"{path}:o={offset}:l={limit}"
    except (json.JSONDecodeError, TypeError):
        return None


def _build_tool_arg_map(messages: list) -> dict:
    """Build mapping tool_call_id → (tool_name, normalized_args_key) from assistant messages.

    Iterates through messages looking for assistant role messages with
    tool_calls, and extracts a normalized key from each tool call's
    function name + arguments (excluding noisy fields like session_id / task_id).

    Returns:
        Dict mapping tool_call_id → (tool_name, args_key)
    """
    tool_arg_map: dict[str, Tuple[str, str]] = {}
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            continue
        for tc in tool_calls if isinstance(tool_calls, list) else [tool_calls]:
            if not isinstance(tc, dict):
                continue
            tc_id = tc.get("id")
            if not tc_id:
                continue
            func = tc.get("function")
            if not isinstance(func, dict):
                continue
            tool_name = func.get("name", "?")
            raw_args = func.get("arguments", "{}")
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except (json.JSONDecodeError, TypeError):
                args = {}
            # Normalize: sort keys, exclude high-cardinality noise fields
            _NOISE_KEYS = {"session_id", "task_id", "effective_task_id"}
            norm = {k: v for k, v in sorted(args.items()) if k not in _NOISE_KEYS}
            args_key = json.dumps(norm, sort_keys=True, default=str)
            tool_arg_map[tc_id] = (tool_name, args_key)
    return tool_arg_map


def _find_errors_in_messages(messages: list) -> int:
    """Count consecutive *identical-failing* tool calls in the conversation.

    A "loop" is defined as the same tool called with the same arguments
    (normalized) producing errors repeatedly.  If the agent tries a different
    tool or different arguments, that counts as *sequential problem-solving*
    and the consecutive-error counter resets — it is NOT a loop.

    Algorithm:
        1. Build a map from tool_call_id → (tool_name, normalized_args)
           using assistant messages that emitted the tool_calls.
        2. Walk tool-result messages in order. For each error result,
           look up (tool_name, args_key) via tool_call_id.
        3. If the current (tool_name, args_key) matches the previous error,
           increment the consecutive counter.
        4. If the args key differs → reset (different approach = problem-solving).
        5. Non-error results also reset the counter.
    """
    tool_arg_map = _build_tool_arg_map(messages)

    consecutive_errors = 0
    max_errors = 0
    prev_key: str | None = None

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "tool":
            continue

        is_error = _is_tool_result_error(msg.get("content", ""))
        if not is_error:
            consecutive_errors = 0
            prev_key = None
            continue

        # Look up the tool_name + normalized args for this tool result
        tc_id = msg.get("tool_call_id", "")
        tool_name, args_key = tool_arg_map.get(tc_id, (msg.get("tool_name", msg.get("name", "?")), ""))
        current_key = f"{tool_name}:{args_key}"

        if current_key == prev_key:
            # Same tool + same args → this is a loop iteration
            consecutive_errors += 1
        else:
            # Different tool or different args → sequential problem-solving, not loop
            consecutive_errors = 1

        max_errors = max(max_errors, consecutive_errors)
        prev_key = current_key

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

        # 2. STALLED_SESSION: consecutive identical-failing tool calls
        error_count = _find_errors_in_messages(messages)
        if error_count >= stalled_threshold:
            signals.append(Signal(
                code="STALLED_SESSION",
                severity="critical",
                message=(
                    f"Detected {error_count} consecutive identical-failing tool calls. "
                    f"The session is in a loop — try a different approach."
                ),
                count=error_count,
                should_halt=True,
            ))

        return signals
