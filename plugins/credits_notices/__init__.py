"""
plugins/credits_notices — Credits display plugin for Autolycus

Hooks into run_agent.py to show credits/billing notices after LLM responses.

Hook: after_llm_response
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def is_free_tier_model(model: str, base_url: str = "") -> bool:
    """Check if the model is a free-tier model (no cost)."""
    if not model:
        return False
    model_lower = model.lower()
    # Common free model patterns
    free_patterns = [
        "free", ":free", "nova", "gemini-2.0-flash",
    ]
    return any(p in model_lower for p in free_patterns)


def credits_notices_enabled() -> bool:
    """Check if credits notices are enabled in config."""
    try:
        from hermes_cli.config import load_config as _load_config
        cfg = _load_config() or {}
        display = cfg.get("display") if isinstance(cfg.get("display"), dict) else None
        if isinstance(display, dict) and "credits_notices" in display:
            return bool(display.get("credits_notices", True))
    except Exception:
        pass
    return True


def after_llm_response(state: Any, latch: Any,
                      model_is_free: bool = False) -> tuple:
    """Hook: evaluate credits notices after LLM response.
    
    Called by run_agent.py after each LLM API response.
    Returns (notices_to_show, notices_to_clear).
    """
    try:
        from agent.credits_tracker import evaluate_credits_notices
        return evaluate_credits_notices(state, latch, model_is_free=model_is_free)
    except Exception as e:
        logger.debug("credits_notices hook error: %s", e)
        return [], []


def get_credit_display_enabled() -> bool:
    """Check if credits display is enabled (cached per agent session)."""
    return credits_notices_enabled()


def register(ctx) -> None:
    """Register credits_notices hooks with the plugin manager."""
    ctx.register_hook("after_llm_response", after_llm_response)
