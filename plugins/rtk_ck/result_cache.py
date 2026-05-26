"""RTK-CK ResultCache — prevent redundant tool calls by caching idempotent results.

Caches tool results keyed by (tool_name, hashed_args) with:
- Per-session isolation (cache doesn't leak between sessions)
- TTL in turns (entries expire after N turns)
- LRU eviction (max_size cap)
- Whitelist-only (only safe tools: read_file, search_files)
- Auto-invalidation on writes (write_file/patch/terminal with same path)

Used by the pre_tool_call hook to block redundant calls BEFORE they consume tokens.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Tools that are safe to cache (idempotent reads, no side effects)
CACHEABLE_TOOLS = frozenset({
    "read_file",
    "search_files",
})

# Tools that invalidate file caches (write operations)
INVALIDATING_TOOLS = frozenset({
    "write_file",
    "patch",
    "terminal",
})

DEFAULT_MAX_SIZE = 100
DEFAULT_TTL_TURNS = 20


def _make_key(tool_name: str, args: dict) -> str:
    """Create a cache key from tool name + normalized args."""
    # Normalize: sort keys, strip whitespace from string values
    normalized = json.dumps(args, sort_keys=True, default=str)
    raw = f"{tool_name}:{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ResultCache:
    """Per-session LRU cache for idempotent tool results.

    Designed to be instantiated once per agent session, shared across
    pre_tool_call hook invocations within that session.
    """

    def __init__(
        self,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_turns: int = DEFAULT_TTL_TURNS,
    ):
        self._max_size = max_size
        self._ttl_turns = ttl_turns
        # key → (result_text, callsite_info, turns_remaining)
        self._cache: OrderedDict[str, Tuple[str, str, int]] = OrderedDict()
        self._hit_count = 0
        self._miss_count = 0
        self._block_count = 0  # calls prevented by cache hit
        self._saved_tokens = 0  # estimated tokens saved

    def check(self, tool_name: str, args: dict, tool_result: str = "") -> Optional[str]:
        """Check if a tool call should be blocked (cache hit).

        Args:
            tool_name: Name of the tool being called.
            args: Tool arguments dict.
            tool_result: Actual result (only used on cache miss to store).

        Returns:
            Cached result string if hit (caller should use this instead of
            executing the tool), or None if miss (proceed normally).
        """
        if tool_name not in CACHEABLE_TOOLS:
            return None

        key = _make_key(tool_name, args)
        entry = self._cache.get(key)

        if entry is None:
            self._miss_count += 1
            return None

        cached_result, callsite, remaining = entry

        if remaining <= 0:
            # Expired — remove and treat as miss
            del self._cache[key]
            self._miss_count += 1
            return None

        # Hit! Move to end (LRU) and decrement TTL
        self._cache.move_to_end(key)
        self._cache[key] = (cached_result, callsite, remaining - 1)
        self._hit_count += 1
        self._block_count += 1
        self._saved_tokens += len(cached_result) // 4  # rough token estimate

        logger.debug(
            "ResultCache HIT: %s(%s) → cached result (%d chars, ~%d tokens saved)",
            tool_name, _summarize_args(args), len(cached_result), len(cached_result) // 4,
        )

        return cached_result

    def store(self, tool_name: str, args: dict, result: str) -> None:
        """Store a tool result in the cache.

        Called after tool execution (on cache miss) to cache the result
        for future calls.
        """
        if tool_name not in CACHEABLE_TOOLS:
            return

        if not result or len(result) > 500_000:
            # Don't cache empty or huge results
            return

        key = _make_key(tool_name, args)

        if key in self._cache:
            # Update existing entry
            self._cache.move_to_end(key)
            self._cache[key] = (result, _summarize_args(args), self._ttl_turns)
        else:
            # LRU eviction: remove oldest if at capacity
            while len(self._cache) >= self._max_size:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("ResultCache EVICT: %s", evicted_key)

            self._cache[key] = (result, _summarize_args(args), self._ttl_turns)
            logger.debug(
                "ResultCache STORE: %s(%s) → %d chars",
                tool_name, _summarize_args(args), len(result),
            )

    def invalidate(self, path: str) -> int:
        """Invalidate all cache entries related to a file path.

        Called when write_file/patch/terminal modifies a file.
        Returns number of entries invalidated.
        """
        keys_to_remove = []
        for key, (result, callsite, remaining) in self._cache.items():
            if path in callsite or path in result[:500]:
                keys_to_remove.append(key)

        for key in keys_to_remove:
            del self._cache[key]

        if keys_to_remove:
            logger.debug("ResultCache INVALIDATE: path=%s removed %d entries", path, len(keys_to_remove))

        return len(keys_to_remove)

    def advance_turn(self) -> None:
        """Advance TTL by one turn. Remove expired entries."""
        expired_keys = []
        for key, (result, callsite, remaining) in self._cache.items():
            new_remaining = remaining - 1
            if new_remaining <= 0:
                expired_keys.append(key)
            else:
                self._cache[key] = (result, callsite, new_remaining)

        for key in expired_keys:
            del self._cache[key]

        if expired_keys:
            logger.debug("ResultCache EXPIRED: %d entries", len(expired_keys))

    def reset(self) -> None:
        """Clear all cache state."""
        self._cache.clear()
        self._hit_count = 0
        self._miss_count = 0
        self._block_count = 0
        self._saved_tokens = 0

    @property
    def stats(self) -> Dict[str, Any]:
        """Return cache stats."""
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "hits": self._hit_count,
            "misses": self._miss_count,
            "blocks": self._block_count,
            "saved_tokens": self._saved_tokens,
        }


def _summarize_args(args: dict) -> str:
    """Create a short summary of args for logging."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        s = str(v)
        if len(s) > 40:
            s = s[:37] + "..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)
