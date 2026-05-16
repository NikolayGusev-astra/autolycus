"""E2E integration tests for Governance Coordinator.

Эти тесты проверяют координатор в связке с реальными компонентами.

Что НЕ мокируется:
  - policy.evaluate() — реальная проверка allow/deny/rules
  - SBL _classify_path / _normalize_to_path — чисто строковые функции
  - PolicyConfig — создаётся реальный объект

Что мокируется:
  - SBL snapshot/deps — требуют systemd
  - RTK signal.read — требует kvstore
  - _write_audit — только I/O
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from plugins.governance import _coordinator_pre_tool_call
from plugins.ultra_governance.policy import (
    PolicyConfig,
    PolicyDecision,
    ParamRule,
    evaluate as _real_evaluate,
    _load_config as _real_load_config,
    _write_audit as _real_write_audit,
)
from plugins.sbl import (
    _normalize_to_path as _real_normalize,
    _classify_path as _real_classify,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_real_config(
    mode: str = "audit",
    deny: list[str] | None = None,
    allow: list[str] | None = None,
    max_bytes: int = 4096,
    param_rules: list[ParamRule] | None = None,
) -> PolicyConfig:
    """Создаёт реальный PolicyConfig для тестов."""
    return PolicyConfig(
        mode=mode,
        allow_tools=set(allow or []),
        deny_tools=set(deny or []),
        max_param_bytes=max_bytes,
        param_rules=param_rules or [],
    )


def _patch_e2e(monkeypatch, config: PolicyConfig):
    """Patch governance coordinator to use real policy + real SBL classify."""

    # Policy config — возвращаем нашу
    monkeypatch.setattr(
        "plugins.ultra_governance.policy._load_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "plugins.governance._policy_config",
        lambda: config,
    )

    # Policy evaluate — реальный (читает _load_config выше)
    monkeypatch.setattr(
        "plugins.governance._policy_evaluate",
        lambda tn, ta: _real_evaluate(tn, ta),
    )

    # Audit log — замокан (только I/O)
    monkeypatch.setattr(
        "plugins.ultra_governance.policy._write_audit",
        lambda e: None,
    )
    monkeypatch.setattr(
        "plugins.governance._policy_audit",
        lambda e: None,
    )

    # SBL: snapshot/deps — замоканы (systemd)
    monkeypatch.setattr("plugins.governance._sbl_has_snapshot", lambda: False)
    monkeypatch.setattr("plugins.governance._sbl_snapshot", lambda: MagicMock())
    monkeypatch.setattr("plugins.governance._sbl_lookup", lambda path: [])
    monkeypatch.setattr("plugins.governance._sbl_format_deps", lambda deps: "")

    # SBL _sbl_normalize — НЕ мокаем; это чисто строковая функция
    # Она уже импортирована в governance на уровне модуля

    # RTK — замокан
    monkeypatch.setattr("plugins.governance._rtk_signal_read", lambda sid: None)


# ---------------------------------------------------------------------------
# E2E: Real policy evaluation
# ---------------------------------------------------------------------------


class TestRealPolicyE2E:
    """Policy engine работает реально, coordinator использует его результат."""

    def test_denied_tool_blocked(self, monkeypatch):
        """Денай-лист срабатывает через реальный policy.evaluate()."""
        config = _make_real_config("enforce", deny=["nuclear_launch"])
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="nuclear_launch",
            args={"target": "moon"},
            session_id="e2e-session",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "deny" in msg["error"].lower()

    def test_allowed_tool_passes(self, monkeypatch):
        """Инструмент не в денай-листе — проходит."""
        config = _make_real_config("enforce", deny=["nuclear_launch"])
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="read_file",
            args={"path": "/home/user/test.txt"},
            session_id="e2e-session",
        )
        assert result is None

    def test_allow_list_overrides_deny(self, monkeypatch):
        """allow_list приоритетнее deny_list."""
        config = _make_real_config("enforce", deny=["terminal"], allow=["terminal"])
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "echo ok"},
            session_id="e2e-session",
        )
        assert result is None  # allow перевешивает deny

    def test_default_param_rules_block_rm_rf(self, monkeypatch):
        """Дефолтные param rules блокируют rm -rf /."""
        config = _make_real_config(
            "enforce",
            param_rules=[
                ParamRule(pattern="rm -rf /", tool="terminal",
                           reason="Destructive recursive delete"),
            ],
        )
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "rm -rf /etc/passwd"},
            session_id="e2e-session",
        )
        assert result is not None
        assert result["action"] == "block"

    def test_clean_terminal_passes(self, monkeypatch):
        """Чистая terminal команда проходит."""
        config = _make_real_config("enforce")
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "ls -la /home"},
            session_id="e2e-session",
        )
        assert result is None

    def test_shutdown_param_rule(self, monkeypatch):
        """shutdown блокируется даже без deny-листа."""
        config = _make_real_config(
            "enforce",
            param_rules=[
                ParamRule(pattern="shutdown", tool="terminal",
                           reason="System shutdown"),
            ],
        )
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "shutdown -h now"},
            session_id="e2e-session",
        )
        assert result is not None
        assert result["action"] == "block"

    def test_simulate_doesnt_block_allowed(self, monkeypatch):
        """simulate не блокирует то, что разрешено политикой."""
        config = _make_real_config("simulate", deny=["dangerous_shell"])
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="read_file",
            args={"path": "/home/file.txt"},
            session_id="e2e-session",
        )
        assert result is None  # read_file не в денае

    def test_audit_passes_denied(self, monkeypatch):
        """audit пропускает даже denied."""
        config = _make_real_config("audit", deny=["terminal"])
        _patch_e2e(monkeypatch, config)

        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "rm -rf /"},
            session_id="e2e-session",
        )
        assert result is None  # audit пропускает


# ---------------------------------------------------------------------------
# E2E: Real SBL classify_path
# ---------------------------------------------------------------------------


class TestRealSBLE2E:
    """SBL classify_path и normalize_to_path — не мокированы."""

    def test_etc_is_system(self):
        """/etc/nginx/ → SYSTEM (реальная классификация)."""
        path, cls = _real_normalize("write_file", {"path": "/etc/nginx/config"})
        assert cls == "SYSTEM"

    def test_home_is_user(self):
        """/home/user/ → USER."""
        path, cls = _real_normalize("write_file", {"path": "/home/user/file.txt"})
        assert cls == "USER"

    def test_root_is_user(self):
        """/root/ → USER (root — рабочая директория пользователя)."""
        path, cls = _real_normalize("write_file", {"path": "/root/.env"})
        assert cls == "USER"

    def test_opt_is_system(self):
        """/opt/ → SYSTEM."""
        path, cls = _real_normalize("write_file", {"path": "/opt/autolycus/config.yaml"})
        assert cls == "SYSTEM"


# ---------------------------------------------------------------------------
# E2E: Combined — real policy + real SBL
# ---------------------------------------------------------------------------


class TestCombinedE2E:
    """Real policy + real SBL classify."""

    def test_system_path_allowed(self, monkeypatch):
        """SYSTEM path, чистая policy = pass."""
        config = _make_real_config("enforce")
        _patch_e2e(monkeypatch, config)
        result = _coordinator_pre_tool_call(
            tool_name="write_file",
            args={"path": "/etc/nginx/nginx.conf"},
            session_id="e2e",
        )
        assert result is None

    def test_user_path_allowed(self, monkeypatch):
        """USER path, чистая policy = pass."""
        config = _make_real_config("enforce")
        _patch_e2e(monkeypatch, config)
        result = _coordinator_pre_tool_call(
            tool_name="write_file",
            args={"path": "/home/user/test.txt"},
            session_id="e2e",
        )
        assert result is None

    def test_terminal_shutdown_blocked_by_policy(self, monkeypatch):
        """shutdown — SYSTEM terminal команда, блокируется policy."""
        config = _make_real_config(
            "enforce",
            param_rules=[
                ParamRule(pattern="shutdown", tool="terminal",
                           reason="Shutdown"),
            ],
        )
        _patch_e2e(monkeypatch, config)
        result = _coordinator_pre_tool_call(
            tool_name="terminal",
            args={"command": "shutdown -h now"},
            session_id="e2e",
        )
        assert result is not None
        assert result["action"] == "block"

    def test_non_write_skips_sbl(self, monkeypatch):
        """search_files не write-тул — SBL не запускается."""
        config = _make_real_config("enforce")
        _patch_e2e(monkeypatch, config)
        result = _coordinator_pre_tool_call(
            tool_name="search_files",
            args={"pattern": "*.py"},
            session_id="e2e",
        )
        assert result is None


# ---------------------------------------------------------------------------
# E2E: RTK circuit breaker (minimally mocked)
# ---------------------------------------------------------------------------


class TestRealRTKE2E:
    """RTK should_halt с реальным сигналом."""

    def test_should_halt_blocks_all_modes(self, monkeypatch):
        """RTK halt блокирует даже в off mode."""
        config = _make_real_config("off")
        _patch_e2e(monkeypatch, config)
        monkeypatch.setattr(
            "plugins.governance._rtk_signal_read",
            lambda sid: {
                "code": "BUDGET_EXCEEDED",
                "severity": "critical",
                "message": "Budget exceeded",
                "should_halt": True,
            },
        )
        result = _coordinator_pre_tool_call(
            tool_name="read_file",
            args={"path": "/tmp/test.txt"},
            session_id="e2e",
        )
        assert result is not None
        assert result["action"] == "block"
        msg = json.loads(result["message"])
        assert "circuit breaker" in msg["error"].lower()

    def test_should_halt_only_if_flag_true(self, monkeypatch):
        """should_halt=False не блокирует."""
        config = _make_real_config("off")
        _patch_e2e(monkeypatch, config)
        monkeypatch.setattr(
            "plugins.governance._rtk_signal_read",
            lambda sid: {
                "code": "NO_PROGRESS",
                "should_halt": False,
            },
        )
        result = _coordinator_pre_tool_call(
            tool_name="read_file",
            args={"path": "/tmp/test.txt"},
            session_id="e2e",
        )
        assert result is None
