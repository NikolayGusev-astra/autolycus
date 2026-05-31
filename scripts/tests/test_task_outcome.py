"""Tests for the Outcome Contract module (task_outcome.py)."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from task_outcome import (
    OutcomeCode,
    TaskOutcome,
    verify_outcome,
    format_outcome,
    format_trace,
    outcome_ok,
    outcome_denied_security,
    outcome_denied_policy,
    outcome_clarification,
    outcome_unsupported,
    outcome_error,
)


class TestVerifyOutcome:
    """Tests for verify_outcome()."""

    def test_ok_outcome(self):
        outcome = TaskOutcome(code=OutcomeCode.OK, message="Task completed successfully")
        valid, errors = verify_outcome(outcome)
        assert valid is True
        assert errors == []

    def test_denied_security_outcome(self):
        outcome = TaskOutcome(
            code=OutcomeCode.DENIED_SECURITY,
            message="Access to /etc/shadow denied",
        )
        valid, errors = verify_outcome(outcome)
        assert valid is True
        assert errors == []

    def test_clarification_outcome(self):
        outcome = TaskOutcome(
            code=OutcomeCode.CLARIFICATION,
            message="Which server do you mean?",
        )
        valid, errors = verify_outcome(outcome)
        assert valid is True
        assert errors == []

    def test_unsupported_outcome(self):
        outcome = TaskOutcome(
            code=OutcomeCode.UNSUPPORTED,
            message="No image generation tool available",
        )
        valid, errors = verify_outcome(outcome)
        assert valid is True
        assert errors == []

    def test_error_outcome_with_details(self):
        outcome = TaskOutcome(
            code=OutcomeCode.ERROR,
            message="Failed to connect to database",
            details="Connection timeout after 30s",
        )
        valid, errors = verify_outcome(outcome)
        assert valid is True
        assert errors == []

    def test_error_outcome_without_details_fails(self):
        outcome = TaskOutcome(
            code=OutcomeCode.ERROR,
            message="Something went wrong",
            details=None,
        )
        valid, errors = verify_outcome(outcome)
        assert valid is False
        assert "details" in errors[0] if errors else False

    def test_empty_message_fails_verify(self):
        outcome = TaskOutcome(code=OutcomeCode.OK, message="")
        valid, errors = verify_outcome(outcome)
        assert valid is False
        assert any("message" in e for e in errors)

    def test_format_outcome_with_grounding_refs(self):
        outcome = TaskOutcome(
            code=OutcomeCode.OK,
            message="Config updated",
            grounding_refs=["/etc/config.ini", "https://docs.example.com"],
        )
        result = format_outcome(outcome)
        assert "Источники:" in result
        assert "/etc/config.ini" in result
        assert "https://docs.example.com" in result


class TestFormatOutcome:
    """Tests for format_outcome()."""

    def test_format_outcome_ok(self):
        outcome = TaskOutcome(code=OutcomeCode.OK, message="All good")
        result = format_outcome(outcome)
        assert result == "All good"

    def test_format_outcome_denied(self):
        outcome = TaskOutcome(
            code=OutcomeCode.DENIED_SECURITY,
            message="Cannot access secret file",
        )
        result = format_outcome(outcome)
        assert "🚫 Отказано:" in result
        assert "Cannot access secret file" in result

    def test_format_outcome_clarification(self):
        outcome = TaskOutcome(
            code=OutcomeCode.CLARIFICATION,
            message="Please specify the target directory",
        )
        result = format_outcome(outcome)
        assert "❓ Уточнение:" in result
        assert "Please specify the target directory" in result

    def test_format_outcome_unsupported(self):
        outcome = TaskOutcome(
            code=OutcomeCode.UNSUPPORTED,
            message="Docker commands not available",
        )
        result = format_outcome(outcome)
        assert "⚠️ Недоступно:" in result
        assert "Docker commands not available" in result

    def test_format_outcome_error(self):
        outcome = TaskOutcome(
            code=OutcomeCode.ERROR,
            message="Backend crashed",
            details="SIGSEGV at 0xdeadbeef",
        )
        result = format_outcome(outcome)
        assert "❌ Ошибка:" in result
        assert "Backend crashed" in result
        assert "SIGSEGV at 0xdeadbeef" in result


class TestFormatTrace:
    """Tests for format_trace()."""

    def test_format_trace(self):
        long_msg = "x" * 150 + "Something terrible happened and we need a very long message to test truncation"
        outcome = TaskOutcome(
            code=OutcomeCode.ERROR,
            message=long_msg,
            grounding_refs=["ref1", "ref2", "ref3"],
            details="stack trace here",
        )
        trace = format_trace(outcome)
        assert "[OUTCOME: error]" in trace
        assert "refs: 3" in trace
        assert "details: stack trace here" in trace
        # The trace message part should be truncated to 100 chars
        trace_msg_part = trace.split("|")[0].split("]")[1].strip()
        assert len(trace_msg_part) == 100


class TestOutcomeHelpers:
    """Tests for the outcome helper factory functions."""

    def test_outcome_helpers_all_codes(self):
        """Verify all 6 helper functions create correct TaskOutcomes."""
        o1 = outcome_ok("Success")
        assert o1.code == OutcomeCode.OK
        assert o1.message == "Success"
        assert o1.grounding_refs == []
        assert o1.details is None

        o2 = outcome_denied_security("Access denied", details="no permission")
        assert o2.code == OutcomeCode.DENIED_SECURITY
        assert o2.message == "Access denied"
        assert o2.details == "no permission"

        o3 = outcome_denied_policy("Not allowed")
        assert o3.code == OutcomeCode.DENIED_POLICY
        assert o3.message == "Not allowed"

        o4 = outcome_clarification("Which one?", grounding_refs=["faq.md"])
        assert o4.code == OutcomeCode.CLARIFICATION
        assert o4.message == "Which one?"
        assert o4.grounding_refs == ["faq.md"]

        o5 = outcome_unsupported("No such tool")
        assert o5.code == OutcomeCode.UNSUPPORTED
        assert o5.message == "No such tool"

        o6 = outcome_error("Failed", "timeout")
        assert o6.code == OutcomeCode.ERROR
        assert o6.message == "Failed"
        assert o6.details == "timeout"
        assert o6.grounding_refs == []
