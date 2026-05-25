"""RTK-CK BudgetScanner — token budget estimation vs context window.

Pure functions. No I/O. Returns Signal or None.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from plugins.rtk.pattern import Signal

logger = logging.getLogger(__name__)

# Default thresholds (% of context_length)
DEFAULT_WARN_PCT = 80
DEFAULT_CRITICAL_PCT = 95
DEFAULT_HALT_PCT = 100


class BudgetScanner:
    """Scans conversation messages and estimates token usage vs context budget."""

    @staticmethod
    def _estimate_tokens(messages: list) -> int:
        """Rough token estimate: chars/4, with image flat cost."""
        total_chars = 0
        for msg in messages:
            if not isinstance(msg, dict):
                total_chars += len(str(msg))
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") in ("image", "image_url", "input_image"):
                            total_chars += 1500 * 4  # image flat token cost * 4 chars/token
                        else:
                            total_chars += len(part.get("text", "") or "")
                    elif isinstance(part, str):
                        total_chars += len(part)
            else:
                total_chars += len(str(content))

            # Include tool_calls args
            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        args = tc.get("function", {}).get("arguments", "")
                        total_chars += len(str(args))

        return (total_chars + 3) // 4

    @staticmethod
    def scan(
        messages: list,
        context_length: int,
        config: Optional[Dict[str, Any]] = None,
    ) -> Optional[Signal]:
        """Scan messages vs context budget.

        Args:
            messages: Conversation message list.
            context_length: Model's context window in tokens.
            config: Override thresholds (warn_pct, critical_pct, halt_pct).

        Returns:
            Signal or None if under all thresholds.
        """
        if not messages:
            return None

        if not context_length or context_length <= 0:
            return None

        cfg = config or {}
        warn_pct = cfg.get("warn_pct", DEFAULT_WARN_PCT)
        critical_pct = cfg.get("critical_pct", DEFAULT_CRITICAL_PCT)
        halt_pct = cfg.get("halt_pct", DEFAULT_HALT_PCT)

        estimated = BudgetScanner._estimate_tokens(messages)
        usage_pct = (estimated / context_length) * 100

        if usage_pct >= halt_pct:
            return Signal(
                code="BUDGET_HALT",
                severity="critical",
                message=(
                    f"Context is {usage_pct:.0f}% full "
                    f"({estimated:,}/{context_length:,} tokens). "
                    f"Circuit breaker: session should halt."
                ),
                count=int(usage_pct),
                should_halt=True,
            )

        if usage_pct >= critical_pct:
            return Signal(
                code="BUDGET_CRITICAL",
                severity="critical",
                message=(
                    f"Context is {usage_pct:.0f}% full "
                    f"({estimated:,}/{context_length:,} tokens). "
                    f"Consider compressing or completing the task."
                ),
                count=int(usage_pct),
            )

        if usage_pct >= warn_pct:
            return Signal(
                code="BUDGET_WARN",
                severity="warn",
                message=(
                    f"Context is {usage_pct:.0f}% full "
                    f"({estimated:,}/{context_length:,} tokens). "
                    f"Approaching budget limit."
                ),
                count=int(usage_pct),
            )

        return None