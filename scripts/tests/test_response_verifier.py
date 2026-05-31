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
    outcome_unsupported,
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

    def test_empty(self):
        """[] -> [] (empty list is ok)."""
        assert verify_grounding_refs([]) == []

    def test_none_refs(self):
        """None -> error."""
        problems = verify_grounding_refs(None)  # type: ignore
        assert problems == ["grounding_refs is None"]

    def test_non_string(self):
        """[42] -> error."""
        problems = verify_grounding_refs([42])  # type: ignore
        assert any("must be a string" in p for p in problems)

    def test_empty_string(self):
        """[""] -> error."""
        problems = verify_grounding_refs([""])
        assert any("must not be empty" in p for p in problems)

    def test_wiki_ref_skips_existence_check(self):
        """wiki refs (no leading /) skip existence check."""
        problems = verify_grounding_refs(["wiki/page.md", "https://example.com"])
        assert problems == []

    def test_absolute_path_exists(self):
        """Абсолютный путь к существующему файлу -> ok."""
        problems = verify_grounding_refs(["/etc/passwd"])
        assert problems == []


class TestVerifyOutcomeCompleteness:
    """Tests for verify_outcome_completeness()."""

    def test_ok_no_refs_assertion(self):
        """OK with assertion but no refs -> warning."""
        outcome = outcome_ok("System config updated")
        problems = verify_outcome_completeness(outcome)
        assert any("sources" in p for p in problems)

    def test_ok_with_refs(self):
        """OK with refs -> ok."""
        outcome = outcome_ok("Done", grounding_refs=["file.txt"])
        assert verify_outcome_completeness(outcome) == []

    def test_ok_command_verb_no_refs(self):
        """OK with single-word assertion -> warning (starts with letter)."""
        outcome = outcome_ok("Done")
        problems = verify_outcome_completeness(outcome)
        assert any("sources" in p for p in problems)

    def test_denied_short_message(self):
        """DENIED with short message -> warning."""
        outcome = outcome_denied_security("No")
        problems = verify_outcome_completeness(outcome)
        assert any("too short" in p for p in problems)

    def test_denied_good_message(self):
        """DENIED with good explanation -> ok."""
        outcome = outcome_denied_security("Access to /etc/shadow denied by policy")
        assert verify_outcome_completeness(outcome) == []

    def test_error_generic(self):
        """ERROR with generic message -> warning."""
        outcome = outcome_error("Something went wrong", details="timeout")
        problems = verify_outcome_completeness(outcome)
        assert any("generic" in p for p in problems)

    def test_error_specific(self):
        """ERROR with specific message -> ok."""
        outcome = outcome_error("Connection timeout after 30s to database", details="timeout")
        assert verify_outcome_completeness(outcome) == []

    def test_clarification_short(self):
        """CLARIFICATION with "?" -> warning."""
        outcome = outcome_clarification("?")
        problems = verify_outcome_completeness(outcome)
        assert any("too short" in p for p in problems)

    def test_clarification_good(self):
        """CLARIFICATION with detailed ask -> ok."""
        outcome = outcome_clarification("Which server target IP do you mean?")
        assert verify_outcome_completeness(outcome) == []

    def test_unsupported_passes_silently(self):
        """UNSUPPORTED passes through without checks."""
        outcome = outcome_unsupported("No Docker tool available")
        assert verify_outcome_completeness(outcome) == []


class TestVerifyNoContradiction:
    """Tests for verify_no_contradiction()."""

    def test_context_missing_for_denied(self):
        """DENIED without context -> warning."""
        outcome = outcome_denied_security("Access denied by policy")
        problems = verify_no_contradiction(outcome, context=None)
        assert any("context" in p for p in problems)

    def test_context_wrong_type(self):
        """context="string" -> warning."""
        outcome = outcome_ok("All good")
        problems = verify_no_contradiction(outcome, context="string")  # type: ignore
        assert any("must be a dict" in p for p in problems)

    def test_contradiction_found_true_but_message_says_not_found(self):
        """found_result=True but message says ничегo не найдено -> error."""
        outcome = outcome_ok("ничего не найдено", grounding_refs=[])
        problems = verify_no_contradiction(outcome, context={"found_result": True})
        assert any("Contradiction" in p for p in problems)

    def test_no_contradiction_when_read_files_exist_and_message_ok(self):
        """read_files exist, message is normal -> no contradiction."""
        outcome = outcome_ok("Config file was read", grounding_refs=["/etc/config.ini"])
        problems = verify_no_contradiction(outcome, context={"read_files": ["/etc/config.ini"]})
        assert problems == []

    def test_ok_no_context_no_warning(self):
        """OK without context -> no warning."""
        outcome = outcome_ok("All good", grounding_refs=["file.txt"])
        assert verify_no_contradiction(outcome) == []


class TestVerifyResponse:
    """Tests for verify_response()."""

    def test_all_good(self):
        """All valid -> passed=True."""
        outcome = outcome_ok("Done", grounding_refs=["wiki/page.md"])
        context = {"read_files": ["wiki/page.md"]}
        passed, problems = verify_response(outcome, context=context)
        assert passed is True
        assert problems == []

    def test_with_issues(self):
        """Has issues -> passed=False."""
        outcome = outcome_ok("System was updated", grounding_refs=[])
        passed, problems = verify_response(outcome)
        assert passed is False
        assert len(problems) > 0

    def test_error_without_details_rejected(self):
        """ERROR without details -> verify_outcome returns False."""
        outcome = outcome_error("DB connection lost after timeout", details=None)  # type: ignore
        passed, problems = verify_response(outcome)
        assert passed is False
