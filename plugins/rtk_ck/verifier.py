"""RTK-VF Response Verifier — claim extraction + falsification.

Two modes:
- Heuristic (default): regex-based extraction + string matching falsification
- LLM (production): claim extraction via auxiliary model + adversarial falsification

Flow:
1. ClaimExtractor.extract(response) → list of claim strings
2. ClaimFalsifier.falsify(claims, tool_results) → list of flag strings
3. ResponseVerifier.verify(response, tool_results) → response + correction (if flags)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from plugins.rtk.pattern import Signal

logger = logging.getLogger(__name__)

# Minimum response length to verify (shorter = skip)
_MIN_RESPONSE_LENGTH = 50

# Contradiction indicators
_CONTRADICTION_INDICATORS = [
    "not found", "does not exist", "error", "failed", "no such",
    "cannot", "unable", "missing", "incorrect", "wrong",
]


class ClaimExtractor:
    """Extracts factual claims from response text using regex patterns."""

    # Simple patterns that capture the factual content
    _PATTERNS = [
        # "X contains Y" — capture Y (the content)
        re.compile(r'contains?\s+(.+?)(?:\.\s|\.$|\n)', re.IGNORECASE),
        # "X has N Y" — capture N Y
        re.compile(r'has\s+(\d+\s+.+?)(?:\.\s|\.$|\n)', re.IGNORECASE),
        # "There are N X" — capture N X
        re.compile(r'there\s+are\s+(.+?)(?:\.\s|\.$|\n)', re.IGNORECASE),
        # "Found N X" — capture N X
        re.compile(r'found\s+(\d+\s+.+?)(?:\.\s|\.$|\n)', re.IGNORECASE),
        # "N matches/results" — capture the count
        re.compile(r'(\d+)\s+matches?', re.IGNORECASE),
        # "passed/failed with N" — capture N
        re.compile(r'(?:passed|failed)\s+with\s+(.+?)(?:\.\s|\.$|\n)', re.IGNORECASE),
    ]

    @staticmethod
    def extract(response: str) -> List[str]:
        """Extract factual claims from response. Returns list of claim strings."""
        if not response or not response.strip():
            return []

        claims = []
        for pattern in ClaimExtractor._PATTERNS:
            for match in pattern.finditer(response):
                claim = match.group(1).strip() if match.lastindex else match.group(0).strip()
                if claim and len(claim) > 2:
                    claims.append(claim)

        # Deduplicate
        seen = set()
        unique = []
        for c in claims:
            key = c.lower()
            if key not in seen:
                seen.add(key)
                unique.append(c)

        return unique


class ClaimFalsifier:
    """Checks claims against tool results using string matching."""

    @staticmethod
    def falsify(
        claims: List[str],
        tool_results: List[Dict[str, Any]],
    ) -> List[str]:
        """Check claims against tool results. Returns list of flag strings."""
        if not claims or not tool_results:
            return []

        flags = []
        all_tool_text = " ".join(
            str(tr.get("content", "")) for tr in tool_results
        ).lower()

        for claim in claims:
            claim_lower = claim.lower()
            key_terms = _extract_key_terms(claim_lower)

            if not key_terms:
                continue

            supported = _check_supported(key_terms, all_tool_text)
            contradicted = _check_contradicted(claim_lower, all_tool_text)

            if contradicted:
                flags.append(f"CONTRADICTION: Claim '{claim}' contradicts tool results")
            elif not supported and len(key_terms) >= 1:
                flags.append(f"UNVERIFIABLE: Claim '{claim}' has no supporting tool result")

        return flags


def _extract_key_terms(text: str) -> List[str]:
    """Extract key terms from a claim for matching."""
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "has", "have",
        "had", "does", "do", "did", "will", "would", "could", "should",
        "to", "of", "in", "on", "at", "by", "for", "with", "from",
        "and", "or", "but", "not", "no", "this", "that", "these", "those",
        "it", "its", "there", "here", "what", "which", "who", "whom",
    }
    words = re.findall(r'\b[\w.]+\b', text.lower())
    return [w for w in words if w not in stop_words and (len(w) > 1 or "." in w)]


def _check_supported(key_terms: List[str], tool_text: str) -> bool:
    """Check if key terms from claim appear in tool results."""
    if not key_terms:
        return True
    matches = sum(1 for term in key_terms if term in tool_text)
    return matches >= max(1, len(key_terms) * 2 // 5)


def _check_contradicted(claim_lower: str, tool_text: str) -> bool:
    """Check if claim contradicts tool results."""
    # Extract IP addresses
    claim_ips = set(re.findall(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', claim_lower))
    tool_ips = set(re.findall(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', tool_text))
    if claim_ips and tool_ips and not claim_ips.intersection(tool_ips):
        return True

    # Extract numbers
    claim_numbers = set(re.findall(r'\b\d+\.?\d*\b', claim_lower))
    tool_numbers = set(re.findall(r'\b\d+\.?\d*\b', tool_text))
    if claim_numbers and tool_numbers:
        for cn in claim_numbers:
            if cn not in tool_numbers:
                if any(w in claim_lower for w in ["has", "have", "are", "were", "contains", "is"]):
                    return True

    # Contradiction indicators
    for indicator in _CONTRADICTION_INDICATORS:
        if indicator in tool_text and indicator not in claim_lower:
            if any(w in claim_lower for w in ["success", "passed", "ok", "correct"]):
                return True

    return False


class ResponseVerifier:
    """Orchestrates claim extraction → falsification → correction."""

    @staticmethod
    def verify(
        response: str,
        tool_results: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Verify response against tool results."""
        if not response or len(response) < _MIN_RESPONSE_LENGTH:
            return response

        if not tool_results:
            return response

        claims = ClaimExtractor.extract(response)
        if not claims:
            return response

        flags = ClaimFalsifier.falsify(claims, tool_results)
        if not flags:
            return response

        correction = ResponseVerifier._format_correction(flags)
        return response + "\n\n" + correction

    @staticmethod
    def _format_correction(flags: List[str]) -> str:
        """Format correction note from flags."""
        lines = ["⚠️ RTK-VF Verification:"]
        for i, flag in enumerate(flags, 1):
            lines.append(f"  {i}. {flag}")
        lines.append("Please verify the above claims against actual tool results.")
        return "\n".join(lines)


def verifier_post_llm_call(
    session_id: str = "",
    response: str = "",
    tool_results: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> str:
    """post_llm_call hook: verify response against tool results."""
    if not tool_results:
        return response

    try:
        return ResponseVerifier.verify(response, tool_results)
    except Exception as exc:
        logger.warning("RTK-VF verification failed (non-fatal): %s", exc)
        return response