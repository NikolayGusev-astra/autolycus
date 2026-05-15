"""
plugins/doc_session/template_loader — Load YAML template files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

_TEMPLATES_DIR = Path(__file__).parent / "templates"

logger = __import__("logging").getLogger(__name__)


def list_templates() -> list[str]:
    """Return available template names (without .yaml extension)."""
    if not _TEMPLATES_DIR.is_dir():
        return []
    return sorted(f.stem for f in _TEMPLATES_DIR.glob("*.yaml"))


def load_template(name: str) -> Optional[dict[str, Any]]:
    """Load a template by name. Returns dict or None if not found."""
    path = _TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        return None
    try:
        return yaml.safe_load(path.read_text())
    except Exception as e:
        logger.warning("Failed to load template '%s': %s", name, e)
        return None
