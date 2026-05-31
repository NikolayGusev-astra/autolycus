"""Tests for the response_verifier module (response_verifier.py)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from task_outcome import (
    OutcomeCode,
    TaskOutcome,
    outcome_ok,
    outcome_denied_security,
    outcome_denied_policy,
    outcome_clarification,
    outcome_error,
)
from response_verifier import (
    verify_grounding_refs,
    verify_outcome_completeness,
    verify_no_contradiction,
    verify_response,
)


class TestVerifyGroundingRefs:
    """Tests for verify_grounding_refs()."""

    def test_verify_grounding_refs_empty(self):
        """[] → [] (empty list is ok)."""
        problems = verify_grounding_refs([])
        assert problems == []

    def test_verify_grounding_refs_valid(self):
        """["/etc/passwd", "wiki/page.md"] → [] (if /etc/passwd exists)."""
        problems = verify_grounding_refs(["/etc/passwd", "wiki/page.md"])
        assert problems == []

    def test_verify_grounding_refs_non_string(self):
        """[42] → error "must be a string"."""
        problems = verify_grounding_refs([42])
        assert any("must be a string" in p for p in problems)

    def test_verify_grounding_refs_empty_string(self):
        """[""] → error "must not be empty"."""
        problems = verify_grounding_refs([""])
        assert any("must not be empty" in p for p in problems)


class TestVerifyOutcomeCompleteness:
    """Tests for verify_outcome_completeness()."""

    def test_verify_completeness_ok_no_refs_assertion(self):
        """OK with "System config updated" and [] → warning "no sources"."""
        outcome = outcome_ok("System config updated")
        problems = verify_outcome_completeness(outcome)
        assert any("no sources" in p or "sources" in p for p in problems)

    def test_verify_completeness_ok_with_refs(self):
        """OK with "Done" and ["file.txt"] → ok (command, not assertion)."""
        outcome = outcome_ok("Done", grounding_refs=["file.txt"])
        problems = verify_outcome_completeness(outcome)
        assert problems == []

    def test_verify_completeness_denied_short_message(self):
        """DENIED_SECURITY with "No" → warning "too short"."""
        outcome = outcome_denied_security("No")
        problems = verify_outcome_completeness(outcome)
        assert any("too short" in p or "short" in p for p in problems)

    def test_verify_completeness_denied_good_message(self):
        """DENIED_SECURITY with "Access to /etc/shadow denied by policy" → ok."""
        outcome = outcome_denied_security("Access to /etc/shadow denied by policy")
        problems = verify_outcome_completeness(outcome)
        assert problems == []

    def test_verify_completeness_error_generic(self):
        """ERROR with "Something went wrong" → warning "generic error"."""
        outcome = outcome_error("Something went wrong", details="timeout")
        problems = verify_outcome_completeness(outcome)
        assert any("generic" in p or "Error occurred" in p or "Something went wrong" in p for p in problems)

    def test_verify_completeness_error_specific(self):
        """ERROR with "Connection timeout after 30s to database" → ok."""
        outcome = outcome_error("Connection timeout after 30s to database", details="timeout")
        problems = verify_outcome_completeness(outcome)
        assert problems == []

    def test_verify_completeness_clarification_generic(self):
        """CLARIFICATION with "?" → warning "too short"."""
        outcome = outcome_clarification("?")
        problems = verify_outcome_completeness(outcome)
        assert any("too short" in p or "short" in p for p in problems)


class TestVerifyNoContradiction:
    """Tests for verify_no_contradiction()."""

    def test_verify_context_missing_for_denied(self):
        """DENIED without context → warning "context recommended"."""
        outcome = outcome_denied_security("Access denied by policy")
        problems = verify_no_contradiction(outcome, context=None)
        assert any("context" in p for p in problems)

    def test_verify_context_wrong_type(self):
        """context="string" → warning "context must be dict"."""
        outcome = outcome_ok("All good")
        problems = verify_no_contradiction(outcome, context="string")  # type: ignore
        assert any("context must be dict" in p or "dict" in p for p in problems)


class TestVerifyResponse:
    """Tests for verify_response()."""

    def test_verify_response_all_good(self):
        """All valid → passed=True, problems=[]."""
        outcome = outcome_ok("All good", grounding_refs=["/etc/passwd"])
        context = {"read_files": ["/etc/config.ini"]}
        passed, problems = verify_response(outcome, context=context)
        assert passed is True
        assert problems == []

    def test_verify_response_with_issues(self):
        """Has issues → passed=False."""
        outcome = outcome_ok("System was updated", grounding_refs=[])
        passed, problems = verify_response(outcome)
        assert passed is False
        assert len(problems) > 0
