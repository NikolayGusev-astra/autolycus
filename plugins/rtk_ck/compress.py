"""RTK-CK Compressor — type-aware conversation history compression.

Pure functions. No I/O. No LLM calls.

Rules:
  - user messages: always preserved (protect_first_n)
  - assistant (text): keep AS-IS (protect_last_n)
  - assistant (tool_calls): collapsed with paired tool_result
  - tool (result >8K chars): head(500) + tail(500) + marker
  - tool (result ≤8K): keep AS-IS
  - tool (error): collapse to summary
  - tool (RTK store): replace with pointer (when rtk_store_enabled)
  - tool (MCP/RAG): NEVER compressed (configurable via non_compressible_tools)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Defaults
DEFAULT_TOOL_HEAD_CHARS = 500
DEFAULT_TOOL_TAIL_CHARS = 500
DEFAULT_PROTECT_FIRST_N = 2
DEFAULT_PROTECT_LAST_N = 3
DEFAULT_SMALL_RESULT_MAX = 8_000  # chars — results ≤ this are kept as-is (was 5000)

COMPRESSED_MARKER = "   ... [compressed by RTK-CK] ..."
POINTER_MARKER = "RTK-CK: compressed — id={uuid}"

# Minimum content size for RTK store pointer (4K chars)
_POINTER_MIN_CHARS = 4_000

# Tools whose results should NEVER be compressed (MCP, RAG, etc.)
# Any tool with name starting with "mcp_" is considered MCP and not compressed.
# Configurable via config.yaml: compress.mcp_tool_prefixes (list of prefixes)
DEFAULT_MCP_TOOL_PREFIXES = ("mcp_",)


def _rtk_store_save(content: str, cache_dir: Optional[str] = None) -> Optional[str]:
    """Save content to RTK store. Returns persist_id or None on failure."""
    try:
        from plugins.rtk.store import save as _save
        return _save(content, cache_dir=cache_dir)
    except Exception:
        return None


def _pointer_compress_tool_result(
    content: str,
    rtk_cache_dir: Optional[str] = None,
) -> Optional[str]:
    """Save tool result to RTK store and return pointer text.

    Returns pointer string like "<RTK-CK: pointer — id={uuid}>"
    or None if save failed.
    """
    persist_id = _rtk_store_save(content, cache_dir=rtk_cache_dir)
    if persist_id:
        return f"<RTK-CK: pointer — id={persist_id}>"
    return None


# Error indicators
_ERROR_PREFIXES = ("error", "traceback", "exception", "failed:", "timeout", "denied", "forbidden")


def _is_tool_result_error(content: str) -> bool:
    """Check if a tool result text looks like an error."""
    if not content:
        return False
    lower = content.lower().strip()
    for prefix in _ERROR_PREFIXES:
        if lower.startswith(prefix):
            return True
    return False


def _estimate_tool_tokens(msg: dict) -> int:
    """Rough token count for a single message."""
    content = msg.get("content", "")
    if isinstance(content, str):
        return (len(content) + 3) // 4
    return 0


def _classify_message(msg: dict) -> str:
    """Classify a message by its role and content.

    Returns one of: 'user', 'assistant_text', 'assistant_tool_call',
    'tool_result', 'tool_error', 'tool_small', 'unknown'
    """
    if not isinstance(msg, dict):
        return "unknown"

    role = msg.get("role", "")

    if role == "user":
        return "user"

    if role == "assistant":
        content = msg.get("content", "")
        tool_calls = msg.get("tool_calls")
        if tool_calls:
            return "assistant_tool_call"
        if content and content.strip():
            return "assistant_text"
        return "assistant_tool_call"  # empty content + no tool_calls = artifact

    if role == "tool":
        content = msg.get("content", "")
        if _is_tool_result_error(content):
            return "tool_error"
        if isinstance(content, str) and len(content) > DEFAULT_SMALL_RESULT_MAX:
            return "tool_result"
        return "tool_small"

    return "unknown"


def _compress_tool_result(content: str, head_chars: int, tail_chars: int) -> str:
    """Head/tail compression for large tool results."""
    if not content or len(content) <= head_chars + tail_chars + len(COMPRESSED_MARKER):
        return content

    head = content[:head_chars]
    tail = content[-tail_chars:]
    return f"{head}{COMPRESSED_MARKER}\n{tail}"


def _collapse_pair(tool_call_msg: dict, tool_result_msg: dict) -> str:
    """Create a 1-line summary from a tool_call + tool_result pair."""
    tc = tool_call_msg.get("tool_calls", [{}])[0] if tool_call_msg.get("tool_calls") else {}
    func = tc.get("function", {}) if isinstance(tc, dict) else {}
    name = func.get("name", tool_result_msg.get("name", "?"))
    args = func.get("arguments", "{}")
    try:
        import json
        parsed = json.loads(args) if args else {}
        # Short preview of args
        preview = ", ".join(f"{k}={v}" for k, v in list(parsed.items())[:2])
    except (json.JSONDecodeError, TypeError):
        preview = args[:60] if args else ""

    result_content = tool_result_msg.get("content", "")
    if _is_tool_result_error(result_content):
        status = f"Error: {result_content[:60]}"
    else:
        lines = result_content.count("\n") + 1 if result_content.strip() else 0
        char_count = len(result_content)
        status = f"OK ({lines} lines, {char_count:,} chars)"

    return f"[{name}({preview}) -> {status}]"


def _compute_savings(original: list, compressed: list) -> Dict[str, Any]:
    """Compute compression stats."""
    orig_tokens = sum(_estimate_tool_tokens(m) for m in original)
    comp_tokens = sum(_estimate_tool_tokens(m) for m in compressed)

    if orig_tokens == 0:
        return {"original_tokens": 0, "compressed_tokens": 0, "savings_pct": 0.0}

    savings_pct = round((1 - comp_tokens / orig_tokens) * 100, 1)
    return {
        "original_tokens": orig_tokens,
        "compressed_tokens": comp_tokens,
        "savings_pct": max(0.0, savings_pct),
    }


class Compressor:
    """Type-aware conversation history compressor."""

    @staticmethod
    def compress(
        messages: List[Dict[str, Any]],
        config: Optional[Dict[str, Any]] = None,
        return_stats: bool = False,
    ) -> List[Dict[str, Any]] | Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Compress conversation messages.

        Args:
            messages: OpenAI-format message list.
            config: Override config dict.
            return_stats: If True, return (messages, stats_dict).

        Returns:
            Compressed messages (optionally with stats).
        """
        if not messages:
            return ([] if return_stats else messages) if not return_stats else ([], _compute_savings([], []))

        cfg = config or {}
        compression_enabled = cfg.get("compression_enabled", True)
        if not compression_enabled:
            stats = _compute_savings(messages, messages)
            return (messages, stats) if return_stats else messages

        head_chars = cfg.get("tool_head_chars", DEFAULT_TOOL_HEAD_CHARS)
        tail_chars = cfg.get("tool_tail_chars", DEFAULT_TOOL_TAIL_CHARS)
        protect_first_n = cfg.get("protect_first_n", DEFAULT_PROTECT_FIRST_N)
        protect_last_n = cfg.get("protect_last_n", DEFAULT_PROTECT_LAST_N)
        collapse_pairs = cfg.get("collapse_pairs", True)

        result: List[Dict[str, Any]] = []

        # Pass 1: classify and find boundaries
        classified = [_classify_message(m) for m in messages]

        # Find protected regions
        total = len(messages)
        protected_first_indices = set()
        user_count = 0
        for i in range(total):
            if classified[i] == "user":
                user_count += 1
                if user_count <= protect_first_n:
                    protected_first_indices.add(i)

        # Protected last: the last N tool_result/tool_error messages
        protected_last_indices = set()
        last_count = 0
        for i in range(total - 1, -1, -1):
            if classified[i] in ("tool_result", "tool_error", "tool_small"):
                last_count += 1
                if last_count <= protect_last_n:
                    protected_last_indices.add(i)

        # Track tool_call → tool_result pairs for collapse
        pending_pairs: List[int] = []  # indices of tool_call msgs waiting for their result

        for i, msg in enumerate(messages):
            msg_type = classified[i]

            # User messages: always preserved
            if msg_type == "user":
                result.append(dict(msg))
                pending_pairs.clear()  # user message breaks tool_call→tool_result chain
                continue

            # Assistant text: preserved
            if msg_type == "assistant_text":
                result.append(dict(msg))
                continue

            # Assistant tool_call: track for potential collapse
            if msg_type == "assistant_tool_call":
                if collapse_pairs and i not in protected_last_indices:
                    pending_pairs.append(i)
                    # Don't add yet — wait for the tool result
                    continue
                else:
                    result.append(dict(msg))
                    pending_pairs.clear()
                    continue

            # Tool result
            if msg_type in ("tool_result", "tool_error", "tool_small"):
                # Protected results keep full content — skip collapse
                if i in protected_last_indices:
                    result.append(dict(msg))
                    pending_pairs.clear()
                    continue

                # Check if we should collapse with a pending tool_call
                if pending_pairs and collapse_pairs:
                    tc_idx = pending_pairs.pop(0)
                    tc_msg = messages[tc_idx]
                    # Check if this tool_call is protected
                    if tc_idx in protected_last_indices:
                        # Protected — keep both as-is
                        result.append(dict(tc_msg))
                        result.append(dict(msg))
                        pending_pairs.clear()
                        continue

                    # Collapse: replace tool_call summary into tool result
                    summary = _collapse_pair(tc_msg, msg)
                    collapsed = dict(msg)
                    collapsed["content"] = summary
                    result.append(collapsed)
                    continue

                # Not collapsing — apply normal compression
                if i in protected_last_indices:
                    result.append(dict(msg))
                elif msg_type == "tool_error":
                    # Collapse error to summary
                    collapsed = dict(msg)
                    name = msg.get("name", "tool")
                    content = msg.get("content", "")
                    collapsed["content"] = f"[{name} -> Error: {content[:80]}]"
                    result.append(collapsed)
                elif msg_type == "tool_result":
                    content = msg.get("content", "")
                    tool_name = msg.get("name", "")
                    mcp_prefixes = cfg.get("mcp_tool_prefixes", DEFAULT_MCP_TOOL_PREFIXES)

                    # MCP/RAG tools: never compress (identified by name prefix)
                    if any(tool_name.startswith(p) for p in mcp_prefixes):
                        result.append(dict(msg))
                        continue

                    # Try pointer compression first
                    rtk_enabled = cfg.get("rtk_store_enabled", False)
                    rtk_cache = cfg.get("rtk_cache_dir")
                    pointer = None
                    if rtk_enabled and len(content) >= _POINTER_MIN_CHARS:
                        pointer = _pointer_compress_tool_result(content, rtk_cache_dir=rtk_cache)
                    if pointer:
                        collapsed = dict(msg)
                        collapsed["content"] = pointer
                        result.append(collapsed)
                    else:
                        # Fall back to head/tail
                        compressed_content = _compress_tool_result(content, head_chars, tail_chars)
                        collapsed = dict(msg)
                        collapsed["content"] = compressed_content
                        result.append(collapsed)
                elif msg_type == "tool_small":
                    # Keep as-is
                    result.append(dict(msg))
                else:
                    result.append(dict(msg))
                continue

            # Unknown: pass through
            result.append(dict(msg))

        stats = _compute_savings(messages, result)
        return (result, stats) if return_stats else result