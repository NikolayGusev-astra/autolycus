"""
plugins/redactor — Secret redaction plugin for Autolycus

Hooks into run_agent.py via before_persist_message to redact credentials
from messages before they are saved to state.db.

Hooks: before_persist_message, before_persist_system_prompt
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Patterns to redact
_PATTERNS = [
    # Bearer tokens
    (re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*', re.IGNORECASE), 'Bearer <REDACTED>'),
    # API keys (common prefixes)
    (re.compile(r'(?:api[_-]?key|apikey|token)\s*[:=]\s*["\']?[A-Za-z0-9\-._~+/]+=*["\']?', re.IGNORECASE), 'api_key=<REDACTED>'),
    # GitHub PATs
    (re.compile(r'gh[pousr]_[A-Za-z0-9_]{36,}', re.IGNORECASE), 'ghp_<REDACTED>'),
    # AWS keys
    (re.compile(r'(?:AKIA|ASIA)[A-Z0-9]{16}', re.IGNORECASE), 'AKIA<REDACTED>'),
    # Generic secrets in JSON-like values
    (re.compile(r'(?:secret|password|passwd|pwd)\s*[:=]\s*["\']?[^\s"\']+["\']?', re.IGNORECASE), 'secret=<REDACTED>'),
]


def redact_sensitive_text(text: str) -> str:
    """Redact credentials and secrets from text."""
    if not text:
        return text
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_message_content(content: Any) -> Any:
    """Redact secrets from message content (str or list of parts)."""
    if isinstance(content, str):
        return redact_sensitive_text(content)
    if isinstance(content, list):
        redacted = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                redacted.append({**part, "text": redact_sensitive_text(part.get("text", ""))})
            else:
                redacted.append(part)
        return redacted
    return content


def before_persist_message(**kwargs) -> Optional[Dict[str, Any]]:
    """Hook: redact credentials from message before persistence.
    
    Called by run_agent.py with kwargs: agent, msg.
    Returns modified msg dict or None.
    """
    msg = kwargs.get("msg")
    if msg is None or "content" not in msg:
        return None
    msg["content"] = redact_message_content(msg["content"])
    msg["_redacted"] = True  # mark so fallback knows
    return msg


def before_persist_system_prompt(**kwargs) -> Optional[str]:
    """Hook: redact credentials from system prompt before persistence.
    
    Called by run_agent.py with kwargs: agent, prompt.
    Returns modified prompt string or None.
    """
    prompt = kwargs.get("prompt")
    if prompt is None:
        return None
    return redact_sensitive_text(prompt)


def register(ctx) -> None:
    """Register redactor hooks with the plugin manager."""
    ctx.register_hook("before_persist_message", before_persist_message)
    ctx.register_hook("before_persist_system_prompt", before_persist_system_prompt)
