"""RTK-CK plugin — Conversation Kernel (RTK v3).

Registers a ``pre_llm_call`` hook that:
1. Scans conversation history token budget (BUDGET_WARN/CRITICAL/HALT)
2. Detects growth anomalies (GROWTH_SPIKE, GROWTH_ACCEL)
3. Detects sequence patterns (REDUNDANT_READS, STALLED_SESSION)
4. Injects warnings into the user message context

This is Phase 1: read-only monitoring. No message mutation.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from plugins.rtk_ck.budget import BudgetScanner
from plugins.rtk_ck.growth import GrowthDetector
from plugins.rtk_ck.patterns import PatternDetector
from plugins.rtk_ck.prefetch_cache import _prefetch_cache
from plugins.rtk_ck.result_cache import ResultCache, INVALIDATING_TOOLS, _summarize_args
from plugins.rtk_ck.metrics import get_metrics_collector

_metrics = get_metrics_collector()

logger = logging.getLogger(__name__)

_DEFAULT_CONTEXT_LENGTH = 128_000

# Module-level ResultCache instance (shared across all sessions in this process)
_result_cache = ResultCache()

def _resolve_context_length(model: str) -> int:
    """Get context length for a model using the agent's metadata resolver.

    Falls back to 128K only if the resolver returns nothing.
    """
    try:
        from agent.model_metadata import get_model_context_length
        ctx = get_model_context_length(model)
        if ctx and ctx > 0:
            return ctx
    except Exception:
        pass
    return _DEFAULT_CONTEXT_LENGTH


def _estimate_history_tokens(messages: list) -> int:
    """Rough token count for history analysis. Delegates to BudgetScanner."""
    return BudgetScanner._estimate_tokens(messages)


def _count_last_turn_tokens(messages: list) -> int:
    """Count tokens in the current turn (from the last user message onward).

    A "turn" in OpenAI format is: user → assistant → tool → assistant → ... → assistant.
    Everything after (and including) the most recent user message belongs to the current turn.
    """
    if not messages:
        return 0
    # Find the last user message
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            return BudgetScanner._estimate_tokens(messages[i:])
    # No user message found — treat entire history as the turn
    return BudgetScanner._estimate_tokens(messages)


def rtk_ck_pre_turn(
    session_id: str = "",
    user_message: str = "",
    conversation_history: Optional[list] = None,
    model: str = "",
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """pre_llm_call hook: budget → growth → patterns → inject.

    Args:
        session_id: Current session identifier.
        user_message: Original user message (pre-injection).
        conversation_history: Full message history.
        model: Model name for context length resolution.
        **kwargs: Additional hook kwargs (ignored).

    Returns:
        Dict with "context" key if signal should be injected, or None.
    """
    if not conversation_history:
        return None

    context_length = _resolve_context_length(model)

    # ── 1. Budget scan ────────────────────────────────────────────────
    budget_signal = BudgetScanner.scan(
        conversation_history,
        context_length=context_length,
    )
    if budget_signal:
        estimated = _estimate_history_tokens(conversation_history)
        pct = budget_signal.count or 0
        warning = (
            f"⚠️ RTK-CK {budget_signal.code}: "
            f"context is ~{pct}% full "
            f"({estimated:,}/{context_length:,} tokens). "
        )
        if budget_signal.code == "BUDGET_HALT":
            warning += "Circuit breaker — complete current task immediately."
        elif budget_signal.code == "BUDGET_CRITICAL":
            warning += "Consider compression or completing the task soon."
        else:
            warning += "Approaching budget limit."
        logger.info("RTK-CK/%s: session=%s pct=%d", budget_signal.code, session_id, pct)
        _metrics.record_signal(budget_signal.code)
        return {"context": warning}

    # ── 2. Growth scan ─────────────────────────────────────────────────
    history_tokens = _estimate_history_tokens(conversation_history)
    # Count tokens added in the current turn (from last user message onward)
    last_turn_tokens = _count_last_turn_tokens(conversation_history)

    growth_signal = GrowthDetector.detect(
        {
            "turn_count": len(conversation_history),
            "history_tokens": history_tokens,
            "last_turn_tokens": last_turn_tokens,
        },
        context_length=context_length,
    )
    if growth_signal:
        logger.info(
            "RTK-CK/%s: session=%s %s",
            growth_signal.code, session_id, growth_signal.message,
        )
        _metrics.record_signal(growth_signal.code)
        return {"context": f"⚠️ RTK-CK {growth_signal.code}: {growth_signal.message}"}

    # ── 3. Pattern scan ────────────────────────────────────────────────
    pattern_signals = PatternDetector.detect(conversation_history)
    for sig in pattern_signals:
        logger.info(
            "RTK-CK/%s: session=%s %s",
            sig.code, session_id, sig.message,
        )
        # Inject pattern warnings (highest priority after budget/growth)
        _metrics.record_signal(sig.code)
        return {
            "context": (
                f"⚠️ RTK-CK {sig.code}: {sig.message}"
                + (" — circuit breaker, halt session." if sig.should_halt else "")
            )
        }

    # ── 4. Compress stats ───────────────────────────────────────────────
    # Run compressor and inject stats if significant savings achieved
    try:
        from plugins.rtk_ck.compress import Compressor as _Compressor
        _, stats = _Compressor.compress(
            conversation_history,
            config={"protect_last_n": 3, "collapse_pairs": True},
            return_stats=True,
        )
        if stats.get("savings_pct", 0) > 20:
            logger.info(
                "RTK-CK/COMPRESS: session=%s savings=%s%%",
                session_id, stats["savings_pct"],
            )
            return {
                "context": (
                    f"📦 RTK-CK COMPRESS: history can be reduced {stats['savings_pct']:.0f}% "
                    f"({stats['original_tokens']:,} → {stats['compressed_tokens']:,} tokens). "
                    f"Consider /compress."
                )
            }
    except Exception:
        pass  # Compression is best-effort

    # ── 5. Prefetch cache ────────────────────────────────────────────────
    prefetch_text = kwargs.get("prefetch_text")
    prefetch_query = kwargs.get("prefetch_query", user_message)
    if prefetch_text is not None:
        stale_signal = _prefetch_cache.check_and_record(
            prefetch_query, prefetch_text, session_id=session_id
        )
        if stale_signal:
            logger.info("RTK-CK/PREFETCH_STALE: session=%s", session_id)
            _metrics.record_signal(stale_signal.code)
            return {"context": f"⚠️ RTK-CK {stale_signal.code}: {stale_signal.message}"}

    # ── 6. Dedup volatile vs prefetch ──────────────────────────────────
    volatile_text = kwargs.get("volatile_text")
    if volatile_text and prefetch_text:
        from plugins.rtk_ck.dedup import Deduplicator as _Dedup
        deduped = _Dedup.dedup(volatile_text, prefetch_text)
        if deduped != prefetch_text:
            saved_chars = len(prefetch_text) - len(deduped)
            logger.info(
                "RTK-CK/DEDUP: session=%s saved %d chars (dedup volatile vs prefetch)",
                session_id, saved_chars,
            )
            _metrics.record_dedup(saved_chars)
            return {"context": f"📦 RTK-CK DEDUP: removed ~{saved_chars} duplicate chars from prefetch"}

    return None


def rtk_ck_pre_tool_call(
    function_name: str = "",
    function_args: dict | str = None,
    session_id: str = "",
    **kwargs: Any,
) -> Optional[str]:
    """pre_tool_call hook: check ResultCache before tool execution.

    If a cacheable tool (read_file, search_files) was already called with the
    same arguments in this session, return the cached result to prevent the
    actual tool call from consuming tokens.

    For write tools (write_file, patch, terminal), invalidate any cached
    reads of the affected file path.

    Returns:
        Cached result string to use instead of executing the tool, or None
        to proceed with normal execution.
    """
    if function_args is None:
        function_args = {}

    # Parse args if they arrive as a JSON string
    if isinstance(function_args, str):
        try:
            import json as _json
            function_args = _json.loads(function_args) if function_args else {}
        except Exception:
            function_args = {}

    # Invalidate cache on write operations
    if function_name in INVALIDATING_TOOLS:
        path = function_args.get("path", "")
        if path:
            invalidated = _result_cache.invalidate(path)
            if invalidated:
                logger.info(
                    "RTK-CK/INVALIDATE: %s on %s removed %d cache entries",
                    function_name, path, invalidated,
                )
        return None

    # Check cache for read operations
    cached = _result_cache.check(function_name, function_args)
    if cached is not None:
        logger.info(
            "RTK-CK/CACHE_HIT: %s(%s) — blocking tool call, returning cached result (%d chars)",
            function_name, _summarize_args(function_args), len(cached),
        )
        _metrics.record_signal("CACHE_HIT")
        return cached

    return None


def rtk_ck_post_tool_call(
    function_name: str = "",
    function_args: dict | str = None,
    tool_result: str = "",
    session_id: str = "",
    **kwargs: Any,
) -> None:
    """post_tool_call hook: store successful read results in cache.

    Called after tool execution to cache idempotent results for future reuse.
    """
    if not tool_result:
        return

    if function_args is None:
        function_args = {}

    if isinstance(function_args, str):
        try:
            import json as _json
            function_args = _json.loads(function_args) if function_args else {}
        except Exception:
            function_args = {}

    _result_cache.store(function_name, function_args, tool_result)


def register(ctx: Any) -> None:
    """Register RTK-CK hooks and tools."""
    ctx.register_hook("pre_llm_call", rtk_ck_pre_turn)
    ctx.register_hook("pre_tool_call", rtk_ck_pre_tool_call)
    ctx.register_hook("post_tool_call", rtk_ck_post_tool_call)

    # Register rtk_ck_stat tool
    ctx.register_tool(
        name="rtk_ck_stat",
        toolset="default",
        schema={
            "type": "object",
            "properties": {
                "format": {
                    "type": "string",
                    "enum": ["text", "json"],
                    "default": "text",
                    "description": "Output format: text or json",
                },
                "reset": {
                    "type": "boolean",
                    "default": False,
                    "description": "Reset stats after reading",
                },
            },
        },
        handler=_handle_rtk_ck_stat,
        emoji="📊",
    )

    logger.info("RTK-CK plugin registered: pre_llm_call, pre_tool_call, post_tool_call hooks, rtk_ck_stat tool")


# ---------------------------------------------------------------------------
# rtk_ck_stat tool
# ---------------------------------------------------------------------------

def _handle_rtk_ck_stat(format: str = "text", reset: bool = False, **_: Any) -> str:
    """Handle rtk_ck_stat tool call — return RTK-CK metrics."""
    import json as _json
    from plugins.rtk_ck.metrics import _metrics, format_stat_line

    m = _metrics.get_metrics()

    if reset:
        _metrics.reset()

    if format == "json":
        return _json.dumps(m, indent=2, ensure_ascii=False)

    return format_stat_line(m)