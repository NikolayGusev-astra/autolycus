"""RTK-CK ContextEngine — replaces LLM-summarization with type-aware compression.

Implements the ContextEngine interface so it can be used as a drop-in
replacement for ContextCompressor in config.yaml:
  context.engine: rtk_ck
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from agent.context_engine import ContextEngine
from agent.model_metadata import get_model_context_length, MINIMUM_CONTEXT_LENGTH
from plugins.rtk_ck.compress import Compressor, DEFAULT_TOOL_HEAD_CHARS, DEFAULT_TOOL_TAIL_CHARS

logger = logging.getLogger(__name__)


class RTCKContextEngine(ContextEngine):
    """RTK-CK context engine — type-aware compression without LLM calls.

    Uses Compressor (head/tail, collapse, pointer) instead of LLM summarization.
    Preserves all user messages, compresses large tool results, collapses
    tool_call+result pairs to 1-line summaries.
    """

    @property
    def name(self) -> str:
        return "rtk_ck"

    def __init__(
        self,
        model: str = "",
        threshold_percent: float = 0.50,
        protect_first_n: int = 3,
        protect_last_n: int = 6,
        summary_target_ratio: float = 0.20,
        quiet_mode: bool = False,
        summary_model_override: str = None,
        base_url: str = "",
        api_key: str = "",
        config_context_length: int | None = None,
        provider: str = "",
        api_mode: str = "",
        **kwargs,
    ):
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.provider = provider
        self.api_mode = api_mode
        self.threshold_percent = threshold_percent
        self.protect_first_n = protect_first_n
        self.protect_last_n = protect_last_n
        self.summary_target_ratio = max(0.10, min(summary_target_ratio, 0.80))
        self.quiet_mode = quiet_mode
        self.context_length = get_model_context_length(
            model, base_url=base_url, api_key=api_key,
            config_context_length=config_context_length, provider=provider,
        )
        self.threshold_tokens = max(
            int(self.context_length * threshold_percent),
            MINIMUM_CONTEXT_LENGTH,
        )
        self.compression_count = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self._last_compression_savings_pct = 100.0
        self._ineffective_compression_count = 0

    def update_from_response(self, usage: Dict[str, Any]) -> None:
        """Update tracked token usage from an API response."""
        self.last_prompt_tokens = usage.get("prompt_tokens", 0)
        self.last_completion_tokens = usage.get("completion_tokens", 0)

    def should_compress(self, prompt_tokens: int = None) -> bool:
        """Return True if compaction should fire this turn."""
        tokens = prompt_tokens or self.last_prompt_tokens
        return tokens >= self.threshold_tokens

    def should_compress_preflight(self, messages: List[Dict[str, Any]]) -> bool:
        """Quick rough check before the API call."""
        if not messages:
            return False
        # Estimate: if we have more than protect_first_n + protect_last_n + 5 messages,
        # there's likely something to compress
        return len(messages) > self.protect_first_n + self.protect_last_n + 5

    def compress(
        self,
        messages: List[Dict[str, Any]],
        current_tokens: int = None,
        focus_topic: str = None,
    ) -> List[Dict[str, Any]]:
        """Compact the message list using type-aware compression.

        Unlike ContextCompressor, this does NOT call an LLM for summarization.
        Instead it uses Compressor rules:
        - user messages: always preserved
        - tool results >5K: head/tail compression
        - tool_call+result pairs: collapsed to 1-line summary
        - protect_first_n / protect_last_n: preserved verbatim
        """
        if not messages:
            return []

        self.compression_count += 1

        compressed = Compressor.compress(
            messages,
            config={
                "protect_first_n": self.protect_first_n,
                "protect_last_n": self.protect_last_n,
                "collapse_pairs": True,
                "tool_head_chars": DEFAULT_TOOL_HEAD_CHARS,
                "tool_tail_chars": DEFAULT_TOOL_TAIL_CHARS,
                "compression_enabled": True,
            },
        )

        # Track savings
        from plugins.rtk_ck.budget import BudgetScanner
        orig_tokens = BudgetScanner._estimate_tokens(messages)
        comp_tokens = BudgetScanner._estimate_tokens(compressed)
        if orig_tokens > 0:
            self._last_compression_savings_pct = round(
                (1 - comp_tokens / orig_tokens) * 100, 1
            )
            if self._last_compression_savings_pct < 5:
                self._ineffective_compression_count += 1
            else:
                self._ineffective_compression_count = 0

        self.last_prompt_tokens = comp_tokens

        if not self.quiet_mode:
            logger.info(
                "RTK-CK compress: %d → %d messages, %d tokens saved (%.0f%%)",
                len(messages), len(compressed),
                orig_tokens - comp_tokens,
                self._last_compression_savings_pct,
            )

        return compressed

    def on_session_reset(self) -> None:
        """Reset per-session state."""
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.compression_count = 0
        self._last_compression_savings_pct = 100.0
        self._ineffective_compression_count = 0

    def get_status(self) -> Dict[str, Any]:
        """Return status dict for display/logging."""
        return {
            "last_prompt_tokens": self.last_prompt_tokens,
            "threshold_tokens": self.threshold_tokens,
            "context_length": self.context_length,
            "usage_percent": (
                min(100, self.last_prompt_tokens / self.context_length * 100)
                if self.context_length else 0
            ),
            "compression_count": self.compression_count,
            "last_savings_pct": self._last_compression_savings_pct,
        }