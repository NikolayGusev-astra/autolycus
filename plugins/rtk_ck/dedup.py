"""RTK-CK Deduplicator — remove duplicate content between volatile memory and prefetch.

Pure functions. No I/O. No LLM calls.

Algorithm:
1. Split both texts into sentences (by '.', '!', '?' + whitespace)
2. Normalize sentences (lowercase, strip, remove extra whitespace)
3. For each prefetch sentence, check if it's similar to any volatile sentence
4. Remove sentences that exceed the similarity threshold
5. Return remaining prefetch sentences joined back
"""
from __future__ import annotations

import re
import string
from typing import Optional

DEFAULT_THRESHOLD = 0.85


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving original whitespace per sentence.

    Handles common abbreviations (Mr., Dr., etc.) heuristically by
    requiring the period to be followed by whitespace or end-of-string.
    """
    if not text:
        return []

    # Split on sentence boundaries: . ! ? followed by space or end
    raw = re.split(r'(?<=[.!?])\s+(?=[A-Z0-9])', text.strip())
    # Flatten — some splits may still contain multiple sentences if
    # the pattern didn't match (e.g. lowercase after period)
    result = []
    for part in raw:
        part = part.strip()
        if part:
            result.append(part)
    if not result and text.strip():
        result = [text.strip()]
    return result


def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.strip(string.punctuation + ' ')
    return text


def _similarity(a: str, b: str) -> float:
    """Compute text similarity score [0.0, 1.0] using Jaccard on word tokens.

    Character-level similarity (SequenceMatcher) overmatches on short
    sentences (e.g. 'key point c' vs 'key point b' → 0.92 just because
    11 of 12 chars match). Word-token Jaccard is more robust:
    {'key','point','c'} ∩ {'key','point','b'} / ∪ = 2/4 = 0.5
    """
    if not a or not b:
        return 0.0
    tokens_a = set(a.split())
    tokens_b = set(b.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


def _sentences_overlap(
    prefetch_sentences: list[tuple[str, str]],
    volatile_norm: list[str],
    threshold: float,
) -> list[str]:
    """Return prefetch sentences that do NOT overlap with volatile.

    Args:
        prefetch_sentences: list of (original_text, normalized_text) tuples
        volatile_norm: list of normalized volatile sentences
        threshold: similarity threshold [0.0, 1.0]

    Returns:
        List of original text sentences to keep (not duplicated).
    """
    kept: list[str] = []
    for orig, norm in prefetch_sentences:
        is_duplicate = False
        for vn in volatile_norm:
            if _similarity(norm, vn) >= threshold:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(orig)
    return kept


class Deduplicator:
    """Removes content from prefetch that duplicates volatile memory."""

    @staticmethod
    def dedup(
        volatile_text: str,
        prefetch_text: str,
        threshold: Optional[float] = None,
    ) -> str:
        """Remove prefetch content that duplicates volatile memory.

        Args:
            volatile_text: Content from volatile memory tier (MEMORY.md + USER.md).
            prefetch_text: Content from external memory provider prefetch.
            threshold: Similarity threshold [0.0, 1.0]. Default 0.85.

        Returns:
            Deduplicated prefetch text (empty string if all duplicates).
        """
        if not prefetch_text:
            return ""

        if not volatile_text:
            return prefetch_text

        effective_threshold = threshold if threshold is not None else DEFAULT_THRESHOLD

        # Split and normalize
        volatile_sentences = _split_sentences(volatile_text)
        prefetch_sentences_raw = _split_sentences(prefetch_text)

        if not volatile_sentences or not prefetch_sentences_raw:
            return prefetch_text

        # Normalize volatile once
        volatile_norm = [_normalize(s) for s in volatile_sentences]

        # Build (original, normalized) pairs for prefetch
        prefetch_pairs = [(s, _normalize(s)) for s in prefetch_sentences_raw]

        # Remove duplicates
        kept = _sentences_overlap(prefetch_pairs, volatile_norm, effective_threshold)

        return " ".join(kept)