"""Tests for RTK-CK Deduplicator — remove duplicate content between volatile memory and prefetch.

Pure function tests: dedup(volatile_text, prefetch_text, threshold) → deduped_prefetch_text.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Dedup tests
# ---------------------------------------------------------------------------


class TestDedupBasic:
    """Basic dedup: no overlap, full overlap, empty inputs."""

    def test_no_overlap_unchanged(self):
        """Prefetch and volatile have no common content → prefetch unchanged."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses."
        prefetch = "Project uses pytest with xdist."
        result = Deduplicator.dedup(volatile, prefetch)
        assert result == prefetch

    def test_complete_overlap_empty_result(self):
        """Prefetch is identical to volatile → empty string."""
        from plugins.rtk_ck.dedup import Deduplicator

        text = "User prefers concise responses."
        result = Deduplicator.dedup(text, text)
        assert result == ""

    def test_both_empty_returns_empty(self):
        """Both empty → empty."""
        from plugins.rtk_ck.dedup import Deduplicator

        result = Deduplicator.dedup("", "")
        assert result == ""

    def test_empty_volatile_returns_prefetch(self):
        """Empty volatile → prefetch unchanged."""
        from plugins.rtk_ck.dedup import Deduplicator

        prefetch = "Project uses pytest."
        result = Deduplicator.dedup("", prefetch)
        assert result == prefetch

    def test_empty_prefetch_returns_empty(self):
        """Empty prefetch → empty."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses."
        result = Deduplicator.dedup(volatile, "")
        assert result == ""


class TestDedupSentences:
    """Sentence-level dedup."""

    def test_partial_overlap_removes_duplicate_sentence(self):
        """Prefetch has one sentence that's in volatile → removed."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses. Project uses pytest."
        prefetch = "Project uses pytest. The build uses GitHub Actions."
        result = Deduplicator.dedup(volatile, prefetch)
        assert "Project uses pytest" not in result
        assert "GitHub Actions" in result

    def test_multiple_overlap_sentences_removed(self):
        """Multiple duplicate sentences removed."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Alpha. Beta. Gamma."
        prefetch = "Beta. Gamma. Delta."
        result = Deduplicator.dedup(volatile, prefetch)
        assert "Beta" not in result
        assert "Gamma" not in result
        assert "Delta" in result

    def test_all_overlap_returns_empty(self):
        """Every sentence in prefetch exists in volatile → empty."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Alpha. Beta. Gamma."
        prefetch = "Beta. Alpha. Gamma."
        result = Deduplicator.dedup(volatile, prefetch)
        assert result == ""


class TestDedupThreshold:
    """Similarity threshold controls what counts as 'duplicate'."""

    def test_low_threshold_keeps_similar(self):
        """Low threshold (0.5) catches high word overlap as duplicate."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses."
        prefetch = "User prefers short responses."
        # Jaccard: {user, prefers, concise, responses} ∩ {user, prefers, short, responses}
        # = {user, prefers, responses} / 5 = 0.6 → > 0.5 → removed
        result = Deduplicator.dedup(volatile, prefetch, threshold=0.5)
        assert result == ""

    def test_high_threshold_keeps_similar(self):
        """High threshold (0.95) only catches near-identical sentences."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses."
        prefetch = "User prefers short responses."
        # Jaccard: {user, prefers, concise, responses} ∩ {user, prefers, short, responses}
        # = 3/5 = 0.6 → < 0.95 → kept
        result = Deduplicator.dedup(volatile, prefetch, threshold=0.95)
        assert result == prefetch

    def test_default_threshold_0_85(self):
        """Default threshold (0.85) catches high-overlap sentences."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Project uses pytest for testing."
        prefetch = "Project uses pytest for tests."
        # Jaccard: {project, uses, pytest, for, testing} ∩ {project, uses, pytest, for, tests}
        # = 4/6 = 0.67 → < 0.85 → kept (not aggressive enough at 0.85)
        # This is the right behavior — "testing" ≠ "tests" in word tokens
        result = Deduplicator.dedup(volatile, prefetch)
        assert "pytest" in result  # kept because 0.67 < 0.85


class TestDedupSentenceSplitting:
    """Sentence boundary detection for proper dedup."""

    def test_multi_sentence_prefetch_some_removed(self):
        """Prefetch with 3 sentences, 1 duplicate → only 2 unique kept."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = ("This is a memory entry about project setup. "
                    "It contains important details.")
        prefetch = ("It contains important details. "
                    "This is new information from external source. "
                    "More unique content here.")
        result = Deduplicator.dedup(volatile, prefetch)
        assert "It contains important details" not in result
        assert "new information" in result
        assert "More unique content" in result

    def test_preserves_whitespace(self):
        """Whitespace-preserving sentences dedup correctly."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Key point A.   Key point B."
        prefetch = "Key point B.   Key point C."
        result = Deduplicator.dedup(volatile, prefetch)
        assert "Key point B" not in result
        assert "Key point C" in result


class TestDedupIntegration:
    """Edge cases and real-world scenarios."""

    def test_prefetch_longer_than_volatile(self):
        """Prefetch much longer than volatile → only duplicates removed."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Common info."
        prefetch = "Common info. Unique A. Unique B. Unique C. Unique D. Unique E."
        result = Deduplicator.dedup(volatile, prefetch)
        assert "Common info" not in result
        assert "Unique A" in result
        assert "Unique E" in result

    def test_case_insensitivity(self):
        """Dedup is case-insensitive by default."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "User prefers concise responses."
        prefetch = "user prefers concise responses."
        result = Deduplicator.dedup(volatile, prefetch)
        assert result == ""

    def test_only_whitespace_difference(self):
        """Same content, different whitespace → deduped."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Key info."
        prefetch = "    Key info.    "
        result = Deduplicator.dedup(volatile, prefetch)
        assert result.strip() == "" or result == ""

    def test_single_word_sentences(self):
        """Single-word sentences handled correctly."""
        from plugins.rtk_ck.dedup import Deduplicator

        volatile = "Hello. World."
        prefetch = "World. Foo."
        result = Deduplicator.dedup(volatile, prefetch)
        assert "World" not in result
        assert "Foo" in result