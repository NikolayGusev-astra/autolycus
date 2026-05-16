"""
plugins/governance — Governance Coordinator.

Единственный владелец хука ``pre_tool_call``.
Объединяет три источника решений:

  1. **RTK signal** — should_halt (circuit breaker). Если RTK детектировал
     budget exceeded или 3 consecutive errors — блокируем ВСЕ инструменты.

  2. **SBL** — системная разведка (snapshot → deps). Только для write-тулов
     (write_file, patch, terminal). Классифицирует путь, проверяет
     зависимости от systemd-сервисов.

  3. **Policy Engine** (ultra-governance/policy.py) — allow/deny списки,
     param rules (rm -rf /, shutdown, pipe-to-bash), max param bytes.

Все три источника агрегируются в одно сообщение.
Режим (off/audit/simulate/enforce) определяет поведение:
  - off      → всё пропускается
  - audit    → логируется, пропускается
  - simulate → блокируется с развёрнутым контекстом
  - enforce  → блокируется жёстко

Удаляет дубли: ultra-governance больше не регистрирует pre_tool_call,
и его внутренний rtk.py удалён (делегирует plugins/rtk).
SBL больше не регистрирует pre_tool_call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Импорты SBL — системная разведка
# ---------------------------------------------------------------------------

from plugins.sbl import (  # noqa: E402
    WRITE_TOOLS as _SBL_WRITE_TOOLS,
    _classify_path as _sbl_classify,
    _normalize_to_path as _sbl_normalize,
    _take_snapshot as _sbl_snapshot,
    _has_snapshot as _sbl_has_snapshot,
    _lookup_dependencies as _sbl_lookup,
    _format_deps as _sbl_format_deps,
)

# ---------------------------------------------------------------------------
# Импорты Policy Engine — allow/deny/param rules
# ---------------------------------------------------------------------------

from plugins.ultra_governance.policy import (  # noqa: E402
    evaluate as _policy_evaluate,
    _load_config as _policy_config,
    _write_audit as _policy_audit,
    PolicyDecision,
)

# ---------------------------------------------------------------------------
# Импорты RTK — circuit breaker
# ---------------------------------------------------------------------------

from plugins.rtk.signal import read as _rtk_signal_read  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WRITE_TOOLS: set = _SBL_WRITE_TOOLS  # {"write_file", "patch", "terminal"}


# ---------------------------------------------------------------------------
# Pre-tool call coordinator
# ---------------------------------------------------------------------------


def _coordinator_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Единственный pre_tool_call хук. Собирает все источники решения.

    Returns:
        None → pass (разрешено).
        ``{"action": "block", "message": "..."}`` → блокировано.
    """
    if not isinstance(args, dict):
        return None

    # 0. RTK circuit breaker — блокирует ВСЕ инструменты если should_halt
    rtk_signal = _rtk_signal_read(session_id) if session_id else None
    if rtk_signal and rtk_signal.get("should_halt", False):
        return {
            "action": "block",
            "message": json.dumps(
                {
                    "error": (
                        f"[Governance · BLOCKED] RTK circuit breaker active: "
                        f"{rtk_signal.get('code', 'UNKNOWN')} — "
                        f"{rtk_signal.get('message', 'Session halted')}"
                    ),
                    "_governance": {
                        "source": "rtk_signal",
                        "code": rtk_signal.get("code"),
                        "severity": rtk_signal.get("severity"),
                    },
                },
                ensure_ascii=False,
            ),
        }

    # 1. Policy check — работает для ВСЕХ инструментов
    config = _policy_config()
    decision = _policy_evaluate(tool_name, args)

    _policy_audit({
        "event": "pre_tool_call",
        "tool": tool_name,
        "args": {k: _truncate_arg(v) for k, v in (args or {}).items()},
        "decision": decision.allowed,
        "reason": decision.reason,
        "mode": config.mode,
        "task_id": task_id,
        "session_id": session_id,
    })

    # 2. SBL проверка — только для write-тулов
    sbl_deps = None
    sbl_classification = None
    if tool_name in _WRITE_TOOLS:
        try:
            path, classification = _sbl_normalize(tool_name, args)
            sbl_classification = classification

            if classification == "SYSTEM":
                if not _sbl_has_snapshot():
                    _sbl_snapshot()
                deps = _sbl_lookup(path)
                if deps:
                    sbl_deps = _sbl_format_deps(deps)
        except Exception as exc:
            logger.debug("[governance] SBL check failed: %s", exc)

    # 3. Режим — определяет поведение
    mode = config.mode

    if mode == "off":
        return None

    # Собираем сообщение
    parts: list[str] = []
    policy_blocked = not decision.allowed

    if sbl_deps:
        parts.append(f"[SBL] System dependencies:\n{sbl_deps}")
    if policy_blocked:
        parts.append(f"[Governance] Policy: {decision.reason}")

    if mode == "audit":
        # Логируем, но пропускаем
        if policy_blocked:
            logger.info(
                "[governance · AUDIT] tool=%s reason=%s (allowed in audit mode)",
                tool_name, decision.reason,
            )
        if sbl_deps:
            logger.info(
                "[governance · AUDIT] %s affects services:\n%s",
                tool_name, sbl_deps,
            )
        return None

    if mode == "simulate":
        if not parts:
            return None  # ничего не блокируем
        message = "\n".join(parts)
        return {
            "action": "block",
            "message": json.dumps(
                {
                    "error": (
                        f"[Governance · SIMULATE] Tool '{tool_name}' "
                        f"would be blocked:\n{message}"
                    ),
                    "_governance": {
                        "mode": "simulate",
                        "tool": tool_name,
                        "policy_blocked": policy_blocked,
                        "policy_reason": decision.reason,
                        "sbl_deps": sbl_deps or None,
                    },
                },
                ensure_ascii=False,
            ),
        }

    if mode == "enforce":
        if not parts:
            return None  # ничего не блокируем
        message = "\n".join(parts)
        logger.warning(
            "[governance · BLOCKED] tool=%s reason=%s task=%s deps=%s",
            tool_name, decision.reason, task_id, bool(sbl_deps),
        )
        return {
            "action": "block",
            "message": json.dumps(
                {
                    "error": (
                        f"[Governance · BLOCKED] Tool '{tool_name}' "
                        f"blocked:\n{message}"
                    ),
                    "_governance": {
                        "mode": "enforce",
                        "tool": tool_name,
                        "policy_blocked": policy_blocked,
                        "policy_reason": decision.reason,
                        "sbl_deps": sbl_deps or None,
                    },
                },
                ensure_ascii=False,
            ),
        }

    return None


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register governance coordinator hooks."""
    ctx.register_hook("pre_tool_call", _coordinator_pre_tool_call)
    logger.info("governance coordinator loaded (mode=%s)", _policy_config().mode)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate_arg(value: Any, max_len: int = 500) -> str:
    """Truncate a tool argument for audit logging."""
    s = str(value)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s
