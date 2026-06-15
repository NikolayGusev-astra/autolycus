"""
plugins/credits_notices — Credits display plugin for Autolycus

Hooks into run_agent.py to show credits/billing notices after LLM responses.

Hook: after_llm_response
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def is_free_tier_model(model: str, base_url: str = "") -> bool:
    """Check if the model is a free-tier model (no cost)."""
    if not model:
        return False
    model_lower = model.lower()
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


def after_llm_response(**kwargs) -> None:
    """Hook: evaluate credits notices after LLM response.
    
    Called by run_agent.py with kwargs: agent, state, latch, model, base_url.
    """
    agent = kwargs.get("agent")
    state = kwargs.get("state")
    latch = kwargs.get("latch")
    model = kwargs.get("model", "")
    base_url = kwargs.get("base_url", "")
    
    if agent is None or state is None:
        return
    
    # Check if notices are enabled
    try:
        enabled = agent._credits_notices_enabled()
    except AttributeError:
        enabled = credits_notices_enabled()
    
    if not enabled:
        return
    
    try:
        from agent.credits_tracker import evaluate_credits_notices, is_free_tier_model as _is_free
        
        if latch is None:
            latch = {"active": set(), "seen_below_90": False, "usage_band": None}
        
        model_is_free = _is_free(model, base_url)
        to_show, to_clear = evaluate_credits_notices(state, latch, model_is_free=model_is_free)
        
        # Emit notices via agent callbacks
        if to_show and hasattr(agent, "notice_callback") and agent.notice_callback:
            for notice in to_show:
                try:
                    agent.notice_callback(notice)
                except Exception as e:
                    logger.debug("credits notice error: %s", e)
        
        if to_clear and hasattr(agent, "notice_clear_callback") and agent.notice_clear_callback:
            for key in to_clear:
                try:
                    agent.notice_clear_callback(key)
                except Exception as e:
                    logger.debug("credits notice clear error: %s", e)
                    
    except Exception as e:
        logger.debug("after_llm_response hook error: %s", e)


def register(ctx) -> None:
    """Register credits_notices hooks with the plugin manager."""
    ctx.register_hook("after_llm_response", after_llm_response)
