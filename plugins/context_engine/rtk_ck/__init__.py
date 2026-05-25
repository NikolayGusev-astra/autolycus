"""RTK-CK context engine plugin.

Registers RTCKContextEngine as the context engine when activated via
``context.engine: rtk_ck`` in config.yaml.
"""
from __future__ import annotations

from plugins.rtk_ck.context_engine import RTCKContextEngine


def register(ctx) -> None:
    """Register the RTK-CK context engine with the agent."""
    ctx.register_context_engine(RTCKContextEngine())
