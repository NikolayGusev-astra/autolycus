"""Tests for Governance Coordinator — plugins/governance/__init__.py

Tests the unified pre_tool_call with all 4 modes, RTK circuit breaker,
SBL dependency integration, and policy enforcement.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from plugins.governance import _coordinator_pre_tool_call


# ---------------------------------------------------------------------------
# Fixtures — mock SBL and RTK dependencies
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_external_deps(monkeypatch: pytest.MonkeyPatch):
    """Mock SBL and policy dependencies so tests don't depend on system state.

    Must patch governance's module-level references because governance imports
    these at module-load time (before monkeypatch can intercept).
    """
    # Mock SBL — patch governance's references
    monkeypatch.setattr("plugins.governance._sbl_normalize",
                        lambda tn, args: ("/etc/nginx/nginx.conf", "SYSTEM"))
    monkeypatch.setattr("plugins.governance._sbl_has_snapshot", lambda: True)
    monkeypatch.setattr("plugins.governance._sbl_snapshot", lambda: MagicMock())
    monkeypatch.setattr("plugins.governance._sbl_lookup",
                        lambda path: [{"service": "nginx", "through": path,
                                       "ports": [80, 443], "type": "http"}])
    monkeypatch.setattr("plugins.governance._sbl_format_deps",
                        lambda deps: "- nginx [http] (ports: 80, 443)")
    monkeypatch.setattr("plugins.governance._SBL_WRITE_TOOLS",
                        {"write_file", "patch", "terminal"})

    # Mock RTK signal — patch governance's reference
    monkeypatch.setattr("plugins.governance._rtk_signal_read", lambda sid: None)

    # Mock policy — patch governance's references
    monkeypatch.setattr("plugins.governance._policy_config", _make_policy_config("audit"))
    monkeypatch.setattr("plugins.governance._policy_evaluate",
                        lambda tn, ta: _PolicyDecisionStub(True, "", "audit"))
    monkeypatch.setattr("plugins.governance._policy_audit", lambda e: None)


def _set_mode(monkeypatch, mode: str):
    """Helper: switch governance mode."""
    monkeypatch.setattr("plugins.governance._policy_config", _make_policy_config(mode))


def _set_policy(monkeypatch, allowed: bool, reason: str = "", mode: str = "audit"):
    """Helper: set policy evaluation result."""
    monkeypatch.setattr(
        "plugins.governance._policy_evaluate",
        lambda tn, ta: _PolicyDecisionStub(allowed, reason, mode),
    )


def _set_rtk(monkeypatch, signal: dict | None):
    """Helper: set RTK signal result."""
    if signal is None:
        monkeypatch.setattr("plugins.governance._rtk_signal_read", lambda sid: None)
    else:
        monkeypatch.setattr("plugins.governance._rtk_signal_read", lambda sid: signal)


def _make_policy_config(mode: str):
    """Return a callable that returns a mock PolicyConfig with given mode."""
    def _load():
        config = MagicMock()
        config.mode = mode
        config.allow_tools = set()
        config.deny_tools = set()
        config.max_param_bytes = 4096
        config.param_rules = []
        return config
    return _load


class _PolicyDecisionStub:
    """Stand-in for PolicyDecision dataclass."""
    def __init__(self, allowed, reason, mode):
        self.allowed = allowed
        self.reason = reason
        self.mode = mode


# ---------------------------------------------------------------------------
# Mode tests
# ---------------------------------------------------------------------------


class TestModes:
    """All four governance modes: off, audit, simulate, enforce."""

    def test_off_mode_passes_everything(self, monkeypatch):
        _set_mode(monkeypatch, "off")
        _set_policy(monkeypatch, False, "rm -rf / is dangerous", "off")
        result = _coordinator_pre_tool_call(
            tool_name="terminal", args={"command": "rm -rf /etc/nginx/"},
            session_id="test-session",
        )
        assert result is None, "off mode should not block"

    def test_audit_mode_passes_violations(self, monkeypatch):
        _set_mode(monkeypatch, "audit")
        _set_policy(monkeypatch, False, "rm -rf / is dangerous", "audit")
        result = _coordinator_pre_tool_call(
            tool_name="terminal", args={"command": "rm -rf /etc/nginx/"},
            session_id="test-session",
        )
        assert result is None, "audit mode should pass even with violations"

    def test_simulate_mode_blocks_with_context(self, monkeypatch):
        _set_mode(monkeypatch, "simulate")
        _set_policy(monkeypatch, False, "rm -rf / is dangerous", "simulate")
        result = _coordinator_pre_tool_call(
            tool_name="terminal", args={"command": "rm -rf /etc/nginx/"},
            session_id="test-session",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "SIMULATE" in msg["error"]
        assert "nginx" in msg["error"]

    def test_enforce_mode_block(self, monkeypatch):
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, False, "rm -rf / is dangerous", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="terminal", args={"command": "rm -rf /etc/nginx/"},
            session_id="test-session",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "BLOCKED" in msg["error"]
        assert "nginx" in msg["error"]

    def test_clean_call_passes_in_all_modes(self, monkeypatch):
        """Clean calls pass even in enforce mode."""
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, True, "", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="read_file", args={"path": "/home/user/file.txt"},
            session_id="test-session",
        )
        assert result is None


# ---------------------------------------------------------------------------
# RTK circuit breaker
# ---------------------------------------------------------------------------


class TestRTKCircuitBreaker:
    """RTK should_halt must block ALL tools."""

    def test_rtk_halt_blocks_all_tools(self, monkeypatch):
        _set_rtk(monkeypatch, {
            "code": "BUDGET_EXCEEDED", "severity": "critical",
            "message": "Budget $10.0 exceeded ($15.00)", "should_halt": True,
        })
        result = _coordinator_pre_tool_call(
            tool_name="read_file", args={"path": "/home/user/file.txt"},
            session_id="test-session",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "circuit breaker" in msg["error"].lower()
        assert "BUDGET_EXCEEDED" in msg["error"]

    def test_rtk_halt_no_session(self):
        """Without session_id, RTK check is skipped (no crash)."""
        result = _coordinator_pre_tool_call(
            tool_name="read_file", args={"path": "/tmp/test.txt"},
        )
        assert result is None

    def test_rtk_no_signal_passes(self, monkeypatch):
        _set_rtk(monkeypatch, None)
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/tmp/test.txt"},
            session_id="test-session",
        )
        assert result is None

    def test_rtk_signal_without_halt_passes(self, monkeypatch):
        _set_rtk(monkeypatch, {
            "code": "NO_PROGRESS", "severity": "warn",
            "message": "3 однотипных вызовов terminal", "should_halt": False,
        })
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/tmp/test.txt"},
            session_id="test-session",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Policy enforcement
# ---------------------------------------------------------------------------


class TestPolicyEnforcement:
    def test_denied_tool_blocked_in_enforce(self, monkeypatch):
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, False, "Tool 'dangerous_shell' is on the deny-list", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="dangerous_shell", args={"command": "echo pwned"},
            session_id="test-session",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "deny-list" in msg["error"]

    def test_param_rule_triggers_block(self, monkeypatch):
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, False, "param block: rm -rf /", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="terminal", args={"command": "rm -rf /etc"},
            session_id="test-session",
        )
        assert result is not None
        assert result["action"] == "block"


# ---------------------------------------------------------------------------
# SBL integration
# ---------------------------------------------------------------------------


class TestSBLIntegration:
    def test_sbl_deps_in_simulate_message(self, monkeypatch):
        _set_mode(monkeypatch, "simulate")
        _set_policy(monkeypatch, False, "param rule match", "simulate")
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/etc/nginx/nginx.conf"},
            session_id="test-session",
        )
        assert result is not None
        msg = json.loads(result["message"])
        assert "nginx" in msg["error"]
        assert "param" in msg["error"]

    def test_sbl_deps_not_included_for_user_paths(self, monkeypatch):
        monkeypatch.setattr("plugins.governance._sbl_normalize",
                            lambda tn, args: ("/home/user/test.txt", "USER"))
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, True, "", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/home/user/test.txt"},
            session_id="test-session",
        )
        assert result is None

    def test_sbl_deps_without_policy_violation_passes(self, monkeypatch):
        _set_mode(monkeypatch, "audit")
        _set_policy(monkeypatch, True, "", "audit")
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/etc/nginx/nginx.conf"},
            session_id="test-session",
        )
        assert result is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_non_dict_args_returns_none(self):
        result = _coordinator_pre_tool_call(tool_name="write_file", args=None)
        assert result is None

        result = _coordinator_pre_tool_call(tool_name="write_file", args="string")
        assert result is None

    def test_non_write_tool_skips_sbl(self, monkeypatch):
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, True, "", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="search_files", args={"pattern": "test"},
            session_id="test-session",
        )
        assert result is None

    def test_sbl_error_does_not_crash_coordinator(self, monkeypatch):
        monkeypatch.setattr("plugins.governance._sbl_normalize",
                            lambda tn, args: (_ for _ in ()).throw(RuntimeError("crash")))
        _set_mode(monkeypatch, "enforce")
        _set_policy(monkeypatch, True, "", "enforce")
        result = _coordinator_pre_tool_call(
            tool_name="write_file", args={"path": "/etc/test.conf"},
            session_id="test-session",
        )
        assert result is None
