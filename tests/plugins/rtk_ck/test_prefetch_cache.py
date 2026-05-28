"""Tests for RTK-CK PrefetchCache — avoid re-querying identical prefetch results.

Cache key: (query, session_id)
Cache value: prefetch result text
TTL: N turns (default 3)
"""
from __future__ import annotations

import pytest


class TestPrefetchCacheBasic:
    """Basic cache hit/miss behavior."""

    def test_first_call_miss(self):
        """First call with a query → miss, returns None (not cached)."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        result = cache.get("what is the project about?", session_id="sess-1")
        assert result is None

    def test_store_then_hit(self):
        """Store a result, then get → hit."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("what is the project about?", "Project uses pytest.", session_id="sess-1")
        result = cache.get("what is the project about?", session_id="sess-1")
        assert result == "Project uses pytest."

    def test_different_query_miss(self):
        """Different query → miss."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("query A", "Result A", session_id="sess-1")
        result = cache.get("query B", session_id="sess-1")
        assert result is None

    def test_different_session_miss(self):
        """Same query, different session → miss."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("query", "Result", session_id="sess-1")
        result = cache.get("query", session_id="sess-2")
        assert result is None


class TestPrefetchCacheTTL:
    """TTL-based expiration."""

    def test_ttl_1_expires_after_1_turn(self):
        """TTL=1 → expires after 1 turn advance."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=1)
        cache.store("query", "Result", session_id="sess-1")

        # Turn 0: hit
        assert cache.get("query", session_id="sess-1") == "Result"

        # Advance 1 turn → expires
        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") is None

    def test_ttl_3_survives_2_turns(self):
        """TTL=3 → survives 2 turn advances."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("query", "Result", session_id="sess-1")

        cache.advance_turn("sess-1")
        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") == "Result"

    def test_ttl_3_expires_after_3_turns(self):
        """TTL=3 → expires after 3 turn advances."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("query", "Result", session_id="sess-1")

        cache.advance_turn("sess-1")
        cache.advance_turn("sess-1")
        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") is None

    def test_default_ttl_is_3(self):
        """Default TTL is 3 turns."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache()
        cache.store("query", "Result", session_id="sess-1")

        cache.advance_turn("sess-1")
        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") == "Result"

        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") is None


class TestPrefetchCacheStalePattern:
    """Detect stale prefetch (same result N times → signal)."""

    def test_stale_after_5_identical_results(self):
        """Same result 5 times → PREFETCH_STALE signal."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(stale_threshold=5)
        for _ in range(5):
            cache.record_result("query", "Same result", session_id="sess-1")

        signal = cache.check_stale("query", session_id="sess-1")
        assert signal is not None
        assert signal.code == "PREFETCH_STALE"
        assert signal.severity == "warn"
        assert "5" in signal.message

    def test_not_stale_after_3_identical_results(self):
        """3 identical results (threshold=5) → no signal."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(stale_threshold=5)
        for _ in range(3):
            cache.record_result("query", "Same result", session_id="sess-1")

        signal = cache.check_stale("query", session_id="sess-1")
        assert signal is None

    def test_different_results_reset_stale_counter(self):
        """Different result resets stale counter."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(stale_threshold=3)
        cache.record_result("query", "Result A", session_id="sess-1")
        cache.record_result("query", "Result A", session_id="sess-1")
        cache.record_result("query", "Result B", session_id="sess-1")  # different → reset

        signal = cache.check_stale("query", session_id="sess-1")
        assert signal is None

    def test_custom_stale_threshold(self):
        """Custom stale_threshold=2 → signal after 2 identical."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(stale_threshold=2)
        cache.record_result("query", "Same", session_id="sess-1")
        cache.record_result("query", "Same", session_id="sess-1")

        signal = cache.check_stale("query", session_id="sess-1")
        assert signal is not None
        assert signal.code == "PREFETCH_STALE"


class TestPrefetchCacheIntegration:
    """Integration scenarios."""

    def test_multiple_sessions_independent(self):
        """Different sessions have independent caches."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=2)
        cache.store("query", "Result A", session_id="sess-1")
        cache.store("query", "Result B", session_id="sess-2")

        assert cache.get("query", session_id="sess-1") == "Result A"
        assert cache.get("query", session_id="sess-2") == "Result B"

        # Expire sess-1 only
        cache.advance_turn("sess-1")
        cache.advance_turn("sess-1")
        assert cache.get("query", session_id="sess-1") is None
        assert cache.get("query", session_id="sess-2") == "Result B"

    def test_overwrite_updates_ttl(self):
        """Storing new result for same query resets TTL."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=2)
        cache.store("query", "Old", session_id="sess-1")
        cache.advance_turn("sess-1")

        # Overwrite with new result
        cache.store("query", "New", session_id="sess-1")
        cache.advance_turn("sess-1")  # would expire old TTL

        # New result still alive (TTL reset)
        assert cache.get("query", session_id="sess-1") == "New"

    def test_empty_result_not_cached(self):
        """Empty string result → not cached."""
        from plugins.rtk_ck.prefetch_cache import PrefetchCache

        cache = PrefetchCache(ttl_turns=3)
        cache.store("query", "", session_id="sess-1")
        # Empty result should not be cached
        assert cache.get("query", session_id="sess-1") is None