"""
Ultra Governance — Tool Policy Engine

Centralised pre-dispatch governance for tool calls. Four modes:

- **Off**:     No enforcement, pass-through.
- **Audit**:   Log tool calls and check against rules, but always allow.
              Reports violations to ``audit.log`` for review.
- **Simulate**: Log + block execution, returning a preview of what WOULD
               have been allowed/denied. Useful for testing policy changes.
- **Enforce**: Actually block denied calls with a descriptive error.

Configuration is read from ``config.yaml`` under ``plugins.ultra_governance``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

PolicyMode = str  # "off" | "audit" | "simulate" | "enforce"


@dataclass
class ParamRule:
    """A single parameter-level block rule.

    *pattern* is matched (case-insensitive substring) against the tool
    argument value identified by *param_name* (or *any* param if blank).
    If *tool* is given, the rule only applies when that tool is called.
    """

    pattern: str
    tool: str = ""  # empty = applies to all tools
    param_name: str = ""  # empty = check all params
    reason: str = ""


@dataclass
class PolicyConfig:
    mode: PolicyMode = "audit"
    allow_tools: Set[str] = field(default_factory=set)
    deny_tools: Set[str] = field(default_factory=set)
    max_param_bytes: int = 4096
    param_rules: List[ParamRule] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

PRESETS: Dict[str, Dict[str, Any]] = {
    "strict": {
        "mode": "enforce",
        "deny_tools": ["dangerous_shell", "wipe_disk"],
        "max_param_bytes": 2048,
        "description": "Full enforcement: blocks dangerous tools and oversized params",
    },
    "balanced": {
        "mode": "audit",
        "deny_tools": ["dangerous_shell", "wipe_disk"],
        "max_param_bytes": 4096,
        "description": "Audit violations, block only explicitly dangerous tools",
    },
    "dev": {
        "mode": "off",
        "deny_tools": [],
        "max_param_bytes": 8192,
        "description": "No enforcement, high param limit — local development only",
    },
}

_DEFAULT_PRESET = "balanced"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

_audit_lock = threading.Lock()
_audit_log_path: Optional[Path] = None


def _ensure_audit_log() -> Path:
    global _audit_log_path
    if _audit_log_path is None:
        from hermes_constants import get_hermes_home

        log_dir = get_hermes_home() / "ultra-governance"
        log_dir.mkdir(parents=True, exist_ok=True)
        _audit_log_path = log_dir / "audit.log"
    return _audit_log_path


def _write_audit(entry: Dict[str, Any]) -> None:
    """Append a JSON audit entry. Thread-safe via lock."""
    path = _ensure_audit_log()
    entry["_ts"] = __import__("datetime").datetime.now().isoformat()
    with _audit_lock:
        try:
            with open(path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            logger.debug("ultra-governance audit write failed: %s", exc)


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

# Default dangerous patterns
_DEFAULT_PARAM_RULES = [
    ParamRule(pattern="rm -rf /", tool="terminal", reason="Destructive recursive delete"),
    ParamRule(pattern="rm -rf /*", tool="terminal", reason="Destructive recursive delete"),
    ParamRule(pattern=r":\(\)\{.*\};:", tool="terminal", reason="Fork bomb"),
    ParamRule(pattern="shutdown", tool="terminal", reason="System shutdown"),
    ParamRule(pattern="reboot", tool="terminal", reason="System reboot"),
    ParamRule(pattern="poweroff", tool="terminal", reason="System poweroff"),
    ParamRule(pattern="halt", tool="terminal", reason="System halt"),
    ParamRule(pattern="mkfs", tool="terminal", reason="Filesystem format"),
    ParamRule(pattern="dd if=", tool="terminal", reason="Raw block device write"),
    ParamRule(pattern="> /dev/", tool="terminal", reason="Destructive device write"),
    ParamRule(pattern="chmod -R 000", tool="terminal", reason="Permission lockout"),
    ParamRule(pattern="wget.*bash", tool="terminal", reason="Remote pipe-to-shell"),
    ParamRule(pattern="curl.*| bash", tool="terminal", reason="Remote pipe-to-shell"),
]

_DEFAULT_DENY_TOOLS: Set[str] = set()


@dataclass
class PolicyDecision:
    """Result of evaluating a tool call against policy."""

    allowed: bool
    reason: str = ""
    mode: str = "audit"  # The mode active at evaluation time


def _load_config() -> PolicyConfig:
    """Load policy config from the YAML config file, with sensible defaults."""
    config = PolicyConfig()

    try:
        from hermes_cli.config import cfg_get

        root = cfg_get("plugins", "ultra_governance", default={})
        if not isinstance(root, dict):
            return config

        # Pre-set: apply preset first, then individual overrides
        policy_cfg = root.get("policy", {})
        raw_preset = policy_cfg.get("preset", "")
        if raw_preset in PRESETS:
            preset = PRESETS[raw_preset]
            config.mode = preset["mode"]
            config.deny_tools = set(preset["deny_tools"])
            config.max_param_bytes = preset["max_param_bytes"]

        # Mode — individual override
        raw_mode = policy_cfg.get("mode", config.mode)
        if raw_mode in ("off", "audit", "simulate", "enforce"):
            config.mode = raw_mode

        # Allow / deny lists — only override if key is explicitly present
        if "allow_tools" in policy_cfg:
            allow = policy_cfg["allow_tools"]
            if isinstance(allow, list):
                config.allow_tools = set(allow)
        if "deny_tools" in policy_cfg:
            deny = policy_cfg["deny_tools"]
            if isinstance(deny, list):
                config.deny_tools = set(deny)

        # Max param bytes
        mbytes = policy_cfg.get("max_param_bytes")
        if isinstance(mbytes, (int, float)) and mbytes > 0:
            config.max_param_bytes = int(mbytes)

        # Param blocklist
        raw_rules = policy_cfg.get("param_blocklist", [])
        if isinstance(raw_rules, list):
            for r in raw_rules:
                if isinstance(r, dict):
                    config.param_rules.append(
                        ParamRule(
                            pattern=r.get("pattern", ""),
                            tool=r.get("tool", ""),
                            param_name=r.get("param_name", ""),
                            reason=r.get("reason", r.get("pattern", "")),
                        )
                    )

    except Exception as exc:
        logger.debug("ultra-governance config load error: %s", exc)

    return config


def evaluate(tool_name: str, tool_args: Dict[str, Any]) -> PolicyDecision:
    """Check whether *tool_name* with *tool_args* should be allowed.

    Returns a ``PolicyDecision`` with ``allowed`` = True/False and a reason
    string.  The caller decides what to do with it based on the active mode.
    """
    config = _load_config()

    # 1. Allow-list takes priority
    if config.allow_tools and tool_name in config.allow_tools:
        return PolicyDecision(allowed=True, reason="On allow-list", mode=config.mode)

    # 2. Deny-list
    if tool_name in config.deny_tools:
        return PolicyDecision(
            allowed=False,
            reason=f"Tool '{tool_name}' is on the deny-list",
            mode=config.mode,
        )

    # 3. Param-based rules
    for rule in (*config.param_rules, *_DEFAULT_PARAM_RULES):
        if rule.tool and rule.tool != tool_name:
            continue
        if tool_name == "terminal":
            cmd = str(tool_args.get("command", ""))
            if re.search(rule.pattern, cmd, re.IGNORECASE):
                reason = rule.reason or f"Pattern '{rule.pattern}' matched in command"
                return PolicyDecision(allowed=False, reason=reason, mode=config.mode)
        else:
            # Check all string args
            for param_name, param_val in tool_args.items():
                if rule.param_name and rule.param_name != param_name:
                    continue
                if isinstance(param_val, str) and re.search(
                    rule.pattern, param_val, re.IGNORECASE
                ):
                    reason = (
                        rule.reason
                        or f"Pattern '{rule.pattern}' matched in {tool_name}.{param_name}"
                    )
                    return PolicyDecision(allowed=False, reason=reason, mode=config.mode)

    # 4. Max param bytes
    total_param_bytes = sum(
        len(str(v)) for v in tool_args.values() if isinstance(v, (str, bytes))
    )
    if total_param_bytes > config.max_param_bytes:
        return PolicyDecision(
            allowed=False,
            reason=f"Total param size {total_param_bytes}b exceeds limit {config.max_param_bytes}b",
            mode=config.mode,
        )

    return PolicyDecision(allowed=True, reason="Passed all policy checks", mode=config.mode)


# ---------------------------------------------------------------------------
# Pre-tool call integration
# ---------------------------------------------------------------------------


def pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[Dict[str, Any]]:
    """Pre-tool call hook: evaluate policy.

    Returns ``None`` to allow the call, or a dict with
    ``{"action": "block", "message": "..."}`` to block it
    (in simulate/enforce modes).
    """
    if not isinstance(args, dict):
        return None

    decision = evaluate(tool_name, args)

    # Always audit
    _write_audit(
        {
            "event": "pre_tool_call",
            "tool": tool_name,
            "args": {k: _truncate_arg(v) for k, v in args.items()},
            "decision": decision.allowed,
            "reason": decision.reason,
            "mode": decision.mode,
            "task_id": task_id,
            "session_id": session_id,
        }
    )

    if decision.mode == "off":
        return None

    if not decision.allowed:
        reason = decision.reason
        if decision.mode == "simulate":
            return {
                "action": "block",
                "message": json.dumps(
                    {
                        "error": (
                            f"[ultra-governance · SIMULATE] Tool '{tool_name}' "
                            f"would be blocked: {reason}"
                        ),
                        "_policy": {
                            "mode": "simulate",
                            "tool": tool_name,
                            "reason": reason,
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        if decision.mode == "enforce":
            logger.warning(
                "BLOCKED tool=%s reason=%s task=%s", tool_name, reason, task_id
            )
            return {
                "action": "block",
                "message": json.dumps(
                    {
                        "error": (
                            f"[ultra-governance · BLOCKED] Tool '{tool_name}' "
                            f"blocked by policy: {reason}"
                        ),
                        "_policy": {
                            "mode": "enforce",
                            "tool": tool_name,
                            "reason": reason,
                        },
                    },
                    ensure_ascii=False,
                ),
            }
        # Audit mode — log only, allow
        logger.info(
            "AUDIT VIOLATION tool=%s reason=%s (allowed in audit mode)",
            tool_name,
            reason,
        )
        return None

    return None


def _truncate_arg(val: Any, max_len: int = 200) -> Any:
    if isinstance(val, str) and len(val) > max_len:
        return val[:max_len] + f"... (+{len(val) - max_len} chars)"
    return val
