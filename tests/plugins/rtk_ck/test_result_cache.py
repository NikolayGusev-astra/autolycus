"""Tests for RTK-CK ResultCache — LRU, TTL, invalidation, cache key generation."""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# ResultCache core tests
# ---------------------------------------------------------------------------


class TestCacheKey:
    """_make_key produces stable, collision-resistant keys."""

    def test_same_args_same_key(self):
        from plugins.rtk_ck.result_cache import _make_key
        a = _make_key("read_file", {"path": "/etc/hosts"})
        b = _make_key("read_file", {"path": "/etc/hosts"})
        assert a == b

    def test_different_args_different_key(self):
        from plugins.rtk_ck.result_cache import _make_key
        a = _make_key("read_file", {"path": "/etc/hosts"})
        b = _make_key("read_file", {"path": "/etc/resolv.conf"})
        assert a != b

    def test_different_tools_different_key(self):
        from plugins.rtk_ck.result_cache import _make_key
        a = _make_key("read_file", {"path": "/etc/hosts"})
        b = _make_key("search_files", {"path": "/etc/hosts"})
        assert a != b

    def test_key_order_invariant(self):
        """Dict key order should not affect hash."""
        from plugins.rtk_ck.result_cache import _make_key
        a = _make_key("read_file", {"path": "/etc/hosts", "offset": 1})
        b = _make_key("read_file", {"offset": 1, "path": "/etc/hosts"})
        assert a == b


class TestResultCacheHitMiss:
    """check() returns cached result on hit, None on miss."""

    def _fresh_cache(self):
        from plugins.rtk_ck.result_cache import ResultCache
        return ResultCache()

    def test_miss_returns_none(self):
        cache = self._fresh_cache()
        assert cache.check("read_file", {"path": "/etc/hosts"}) is None

    def test_hit_after_store(self):
        cache = self._fresh_cache()
        cache.store("read_file", {"path": "/etc/hosts"}, "file content here")
        result = cache.check("read_file", {"path": "/etc/hosts"})
        assert result == "file content here"

    def test_miss_for_different_path(self):
        cache = self._fresh_cache()
        cache.store("read_file", {"path": "/etc/hosts"}, "content")
        assert cache.check("read_file", {"path": "/etc/resolv.conf"}) is None

    def test_store_then_check_updates_stats(self):
        cache = self._fresh_cache()
        cache.store("read_file", {"path": "/x"}, "data")
        cache.check("read_file", {"path": "/x"})
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["blocks"] == 1

    def test_miss_count_increments(self):
        """Each cache miss should increment miss_count."""
        cache = self._fresh_cache()
        # 3 misses for different paths
        cache.check("read_file", {"path": "/a"})
        cache.check("read_file", {"path": "/b"})
        cache.check("read_file", {"path": "/c"})
        stats = cache.stats
        assert stats["misses"] == 3
        assert stats["hits"] == 0

    def test_miss_then_hit_counts_both(self):
        """Miss followed by hit should show both counters."""
        cache = self._fresh_cache()
        cache.check("read_file", {"path": "/x"})  # miss
        cache.store("read_file", {"path": "/x"}, "data")
        cache.check("read_file", {"path": "/x"})  # hit
        stats = cache.stats
        assert stats["misses"] == 1
        assert stats["hits"] == 1

    def test_non_cacheable_tool_always_miss(self):
        """terminal/execute_code are not in CACHEABLE_TOOLS → always miss."""
        cache = self._fresh_cache()
        cache.store("terminal", {"command": "ls"}, "output")
        assert cache.check("terminal", {"command": "ls"}) is None


class TestResultCacheTTL:
    """Entries expire after TTL turns."""

    def test_entry_expires_after_ttl(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(ttl_turns=2)
        cache.store("read_file", {"path": "/x"}, "data")
        cache.advance_turn()  # ttl=1
        cache.advance_turn()  # ttl=0 → expired
        assert cache.check("read_file", {"path": "/x"}) is None

    def test_entry_survives_within_ttl(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(ttl_turns=3)
        cache.store("read_file", {"path": "/x"}, "data")
        cache.advance_turn()
        cache.advance_turn()
        assert cache.check("read_file", {"path": "/x"}) == "data"

    def test_hit_decrements_ttl(self):
        """Each cache hit consumes one TTL turn."""
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(ttl_turns=2)
        cache.store("read_file", {"path": "/x"}, "data")
        cache.check("read_file", {"path": "/x"})  # hit, ttl→1
        cache.check("read_file", {"path": "/x"})  # hit, ttl→0
        # Next check → expired
        assert cache.check("read_file", {"path": "/x"}) is None

    def test_advance_turn_batch(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(ttl_turns=1)
        cache.store("read_file", {"path": "/x"}, "data")
        cache.advance_turn()
        assert cache.check("read_file", {"path": "/x"}) is None


class TestResultCacheLRU:
    """LRU eviction when cache reaches max_size."""

    def test_evicts_oldest_entry(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(max_size=3)
        cache.store("read_file", {"path": "/a"}, "aaa")
        cache.store("read_file", {"path": "/b"}, "bbb")
        cache.store("read_file", {"path": "/c"}, "ccc")
        # Access /a to make it recently used
        cache.check("read_file", {"path": "/a"})
        # Add /d → should evict /b (oldest unused)
        cache.store("read_file", {"path": "/d"}, "ddd")
        assert cache.check("read_file", {"path": "/a"}) == "aaa"  # still there
        assert cache.check("read_file", {"path": "/b"}) is None  # evicted

    def test_stats_reflect_eviction(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache(max_size=2)
        cache.store("read_file", {"path": "/a"}, "aaa")
        cache.store("read_file", {"path": "/b"}, "bbb")
        cache.store("read_file", {"path": "/c"}, "ccc")  # evicts /a
        assert cache.stats["size"] == 2


class TestResultCacheInvalidation:
    """invalidate() removes entries related to a path."""

    def test_invalidate_by_path(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        cache.store("read_file", {"path": "/etc/hosts"}, "127.0.0.1 localhost")
        cache.store("read_file", {"path": "/etc/resolv.conf"}, "nameserver 8.8.8.8")
        removed = cache.invalidate("/etc/hosts")
        assert removed >= 1
        assert cache.check("read_file", {"path": "/etc/hosts"}) is None
        assert cache.check("read_file", {"path": "/etc/resolv.conf"}) is not None

    def test_invalidate_empty_path(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        removed = cache.invalidate("")
        assert removed == 0

    def test_invalidate_nonexistent_path(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        cache.store("read_file", {"path": "/a"}, "data")
        removed = cache.invalidate("/nonexistent")
        assert removed == 0


class TestResultCacheEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_result_not_cached(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        cache.store("read_file", {"path": "/x"}, "")
        assert cache.check("read_file", {"path": "/x"}) is None

    def test_huge_result_not_cached(self):
        """Results >500K chars are not cached."""
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        huge = "x" * 600_000
        cache.store("read_file", {"path": "/x"}, huge)
        assert cache.check("read_file", {"path": "/x"}) is None

    def test_reset_clears_all(self):
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        cache.store("read_file", {"path": "/x"}, "data")
        cache.check("read_file", {"path": "/x"})  # register a hit
        cache.reset()
        assert cache.check("read_file", {"path": "/x"}) is None
        stats = cache.stats
        assert stats["hits"] == 0
        assert stats["blocks"] == 0
        assert stats["size"] == 0

    def test_search_files_cached(self):
        """search_files is also cacheable."""
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        result = "matches: 1\nline 5: hello"
        cache.store("search_files", {"pattern": "hello", "path": "/src"}, result)
        assert cache.check("search_files", {"pattern": "hello", "path": "/src"}) == result

    def test_stats_saved_tokens(self):
        """saved_tokens estimates token savings."""
        from plugins.rtk_ck.result_cache import ResultCache
        cache = ResultCache()
        content = "x" * 400  # ~100 tokens
        cache.store("read_file", {"path": "/x"}, content)
        cache.check("read_file", {"path": "/x"})  # hit
        cache.check("read_file", {"path": "/x"})  # hit again
        stats = cache.stats
        assert stats["saved_tokens"] >= 100  # at least first hit counted
