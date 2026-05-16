"""
Ultra Governance plugin — Tool Policy Engine (library only).

pre_tool_call передан Governance Coordinator (plugins/governance/).
Оставлен только transform_tool_result, делегирующий plugins/rtk.

RTK filter: местный rtk.py удалён. Используется plugins/rtk (production-grade).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from plugins.rtk import transform_tool_result as _rtk_transform

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def _on_transform_tool_result(
    tool_name: str = "",
    args: Optional[Dict[str, Any]] = None,
    result: Any = None,
    task_id: str = "",
    session_id: str = "",
    **_: Any,
) -> Optional[str]:
    """Apply RTK filter to tool outputs (delegated to plugins/rtk)."""
    return _rtk_transform(
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
    """Register ultra-governance hooks (transform_tool_result only)."""
    ctx.register_hook("transform_tool_result", _on_transform_tool_result)
    logger.info("ultra-governance loaded (rtk=plugins/rtk)")
