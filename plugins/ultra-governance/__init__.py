"""
Ultra Governance plugin — Tool Policy Engine + RTK Filter

Registered hooks:
  pre_tool_call      Evaluate policy and block/simulate/audit
  post_tool_call     Log policy outcomes
  transform_tool_result  Apply RTK output compression
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from . import policy, rtk

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _on_pre_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[str]:
    """Pre-dispatch governance: evaluate policy and block if needed."""
    return policy.pre_tool_call(
        tool_name=tool_name,
        args=args,
        task_id=task_id,
        session_id=session_id,
    )


def _on_post_tool_call(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    duration_ms: int = 0,
    **_: Any,
) -> None:
    """Post-execution logging."""
    pass


def _on_transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[str]:
    """Apply RTK filter to tool outputs."""
    return rtk.transform_tool_result(
        tool_name=tool_name,
        args=args,
        result=result,
        task_id=task_id,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)

    logger.info(
        "ultra-governance loaded (policy=%s, rtk=%s)",
        policy._load_config().mode,
        "enabled" if rtk._load_rtk_config()["enabled"] else "disabled",
    )