"""
plugins/kanban_heartbeat — Kanban worker heartbeat plugin for Autolycus

Hooks into run_agent.py post_activity to bridge worker heartbeat to kanban board.

Hook: post_activity
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


def post_activity(**kwargs) -> None:
    """Hook: bridge agent activity to kanban board heartbeat.
    
    Called by run_agent.py with kwargs: agent, desc, kanban_task.
    When kanban_task is set, sends heartbeat to the kanban dispatcher.
    """
    kanban_task = kwargs.get("kanban_task", "")
    if not kanban_task:
        return
    
    try:
        from tools.kanban_tools import heartbeat_current_worker_from_env
        heartbeat_current_worker_from_env()
    except Exception:
        # Never let the bridge break the agent loop. The function
        # already swallows exceptions internally; this outer guard
        # covers import-time failures (kanban_tools unavailable,
        # etc.) on niche deployment surfaces.
        pass


def register(ctx) -> None:
    """Register kanban_heartbeat hooks with the plugin manager."""
    ctx.register_hook("post_activity", post_activity)
