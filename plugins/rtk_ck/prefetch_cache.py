"""RTK-CK PrefetchCache — avoid re-querying identical prefetch results.

Cache key: (query, session_id)
Cache value: (result_text, turns_remaining)
TTL: N turns (default 3)

Also tracks stale detection: same result N times → PREFETCH_STALE signal.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

from plugins.rtk.pattern import Signal

logger = logging.getLogger(__name__)

DEFAULT_TTL_TURNS = 3
DEFAULT_STALE_THRESHOLD = 5


class PrefetchCache:
    """Session-scoped prefetch result cache with TTL and stale detection."""

    def __init__(
        self,
        ttl_turns: int = DEFAULT_TTL_TURNS,
        stale_threshold: int = DEFAULT_STALE_THRESHOLD,
    ):
        self._ttl_turns = ttl_turns
        self._stale_threshold = stale_threshold
        # key: (query, session_id) → (result, turns_remaining)
        self._cache: Dict[Tuple[str, str], Tuple[str, int]] = {}
        # key: (query, session_id) → (last_result, consecutive_count)
        self._stale_tracker: Dict[Tuple[str, str], Tuple[str, int]] = {}

    def get(self, query: str, session_id: str = "") -> Optional[str]:
        """Get cached prefetch result. Returns None if miss or expired."""
        key = (query, session_id)
        entry = self._cache.get(key)
        if entry is None:
            return None
        result, remaining = entry
        if remaining <= 0:
            del self._cache[key]
            return None
        return result

    def store(self, query: str, result: str, session_id: str = "") -> None:
        """Store prefetch result with TTL."""
        if not result or not result.strip():
            return  # Don't cache empty results
        key = (query, session_id)
        self._cache[key] = (result, self._ttl_turns)

    def advance_turn(self, session_id: str = "") -> None:
        """Decrement TTL for all entries matching session_id. Remove expired."""
        expired_keys = []
        for key, (result, remaining) in self._cache.items():
            _, entry_session = key
            if entry_session == session_id:
                new_remaining = remaining - 1
                if new_remaining <= 0:
                    expired_keys.append(key)
                else:
                    self._cache[key] = (result, new_remaining)
        for key in expired_keys:
            del self._cache[key]

    def record_result(self, query: str, result: str, session_id: str = "") -> None:
        """Record a prefetch result for stale detection."""
        if not result or not result.strip():
            return
        key = (query, session_id)
        tracker = self._stale_tracker.get(key)
        if tracker is None:
            self._stale_tracker[key] = (result, 1)
        else:
            last_result, count = tracker
            if result == last_result:
                self._stale_tracker[key] = (result, count + 1)
            else:
                # Different result → reset counter
                self._stale_tracker[key] = (result, 1)

    def check_stale(self, query: str, session_id: str = "") -> Optional[Signal]:
        """Check if prefetch result has been identical N times."""
        key = (query, session_id)
        tracker = self._stale_tracker.get(key)
        if tracker is None:
            return None
        result, count = tracker
        if count >= self._stale_threshold:
            return Signal(
                code="PREFETCH_STALE",
                severity="warn",
                message=(
                    f"Prefetch returned identical result {count} times for query '{query[:60]}...'. "
                    f"Consider caching or skipping prefetch."
                ),
                count=count,
            )
        return None

    def reset(self) -> None:
        """Clear all cache and stale tracker state."""
        self._cache.clear()
        self._stale_tracker.clear()

    def check_and_record(
        self, query: str, result: str, session_id: str = ""
    ) -> Optional[Signal]:
        """Record result and check if stale. Returns Signal if stale."""
        self.record_result(query, result, session_id)
        return self.check_stale(query, session_id)


# Module-level singleton (one cache per process, survives across hook calls)
_prefetch_cache = PrefetchCache()