"""Module for classifying content trust levels and detecting prompt injection attempts."""

import re

# ── classify_source ────────────────────────────────────────────────────────────


def classify_source(source_type: str, source_path: str) -> str:
    """Classify the trust level of a content source.

    Args:
        source_type: One of "system", "user", "external", or custom.
        source_path: Path or identifier for the source.

    Returns:
        "trusted", "semi_trusted", or "untrusted".
    """
    if source_type == "system":
        return "trusted"
    if source_type == "user":
        return "semi_trusted"
    if source_type == "external":
        return "untrusted"
    return "untrusted"


# ── check_instruction_source ──────────────────────────────────────────────────


_IMPERATIVE_PATTERNS = [
    "сделай",
    "выполни",
    "игнорируй",
    "запусти",
    "напиши",
    "удали",
    "отправь",
    "измени",
    "read",
    "execute",
    "ignore",
    "run",
    "delete",
    "send",
    "modify",
    "write",
]


def check_instruction_source(content: str, source_trust: str) -> list[str]:
    """Check if untrusted content contains imperative/instruction patterns.

    Args:
        content: The text content to inspect.
        source_trust: Trust level ("trusted", "semi_trusted", "untrusted").

    Returns:
        List of matched imperative patterns (empty list if none found or
        if source is trusted).
    """
    if source_trust == "trusted":
        return []

    content_lower = content.lower()
    matched = [p for p in _IMPERATIVE_PATTERNS if p in content_lower]
    return matched


# ── classify_user_request ─────────────────────────────────────────────────────


# Regex: requests for secrets/passwords/tokens/API keys
_SECRET_REQUEST_RE = re.compile(
    r"(give|send|show|tell|share|выдай|скажи|покажи|отправь)\s.*"
    r"(password|secret|api.?key|token|пароль|секрет|токен|ключ)",
    re.IGNORECASE,
)

# Patterns that try to override the system prompt
_IGNORE_ALL_RE = re.compile(r"ignore all previous instructions", re.IGNORECASE)
_SYSTEM_PROMPT_OVERRIDE_RE = re.compile(
    r"(you are now .*|system prompt:)", re.IGNORECASE
)

# Suspicious patterns
_SUSPICIOUS_IGNORE_RE = re.compile(r"\bignore\b", re.IGNORECASE)
_SUSPICIOUS_IGNORUI_RE = re.compile(r"\bигнорируй\b", re.IGNORECASE)
_ACT_AS_RE = re.compile(r"act as", re.IGNORECASE)
_DEISTVUI_KAK_RE = re.compile(r"действуй как", re.IGNORECASE)
_SYSTEM_OVERRIDE_RE = re.compile(
    r"(override|переопредели|измени)\s.*(system|систему|prompt|поведение)",
    re.IGNORECASE,
)


def classify_user_request(message: str) -> tuple[str, list[str]]:
    """Classify a user message as safe, suspicious, or malicious.

    Args:
        message: The user's input message.

    Returns:
        Tuple of (verdict: "safe"|"suspicious"|"malicious", reasons: list[str]).
    """
    reasons: list[str] = []

    # ── Check malicious indicators ────────────────────────────────────────
    if _SECRET_REQUEST_RE.search(message):
        reasons.append("request for secrets/passwords/tokens")
        return ("malicious", reasons)

    if _IGNORE_ALL_RE.search(message):
        reasons.append("'ignore all previous instructions' pattern")
        return ("malicious", reasons)

    if _SYSTEM_PROMPT_OVERRIDE_RE.search(message):
        reasons.append("system prompt override attempt")
        return ("malicious", reasons)

    # ── Check suspicious indicators ───────────────────────────────────────
    if _SUSPICIOUS_IGNORE_RE.search(message) or _SUSPICIOUS_IGNORUI_RE.search(
        message
    ):
        reasons.append("'ignore'/'игнорируй' in context")
        return ("suspicious", reasons)

    if _ACT_AS_RE.search(message) or _DEISTVUI_KAK_RE.search(message):
        reasons.append("'act as'/'действуй как' pattern")
        return ("suspicious", reasons)

    if _SYSTEM_OVERRIDE_RE.search(message):
        reasons.append("request to override system behavior")
        return ("suspicious", reasons)

    # ── No indicators matched ─────────────────────────────────────────────
    return ("safe", [])


# ── get_trust_annotation ──────────────────────────────────────────────────────


def get_trust_annotation(file_path: str) -> dict:
    """Return trust annotation for a given file path.

    Args:
        file_path: Absolute or relative path to a file.

    Returns:
        Dict with keys: type, trust, sensitive.
    """
    path_lower = file_path.lower()

    # Check in priority order — first match wins

    # secret/sensitive files
    if any(
        keyword in path_lower
        for keyword in ["secret", "credential", "key", ".env", "token", ".pem"]
    ):
        return {"type": "secret", "trust": "semi_trusted", "sensitive": True}

    # wiki / knowledge
    if any(keyword in path_lower for keyword in ["wiki", "knowledge"]):
        return {"type": "wiki", "trust": "trusted", "sensitive": False}

    # code
    if ".py" in path_lower or "src/" in path_lower:
        return {"type": "code", "trust": "trusted", "sensitive": False}

    # config
    if "/etc/" in path_lower or ".conf" in path_lower or "config" in path_lower:
        return {"type": "config", "trust": "trusted", "sensitive": True}

    # log
    if "log" in path_lower or ".log" in path_lower:
        return {"type": "log", "trust": "semi_trusted", "sensitive": False}

    # data / untrusted
    if any(
        keyword in path_lower
        for keyword in ["/tmp/", "/downloads/", "cache"]
    ):
        return {"type": "data", "trust": "untrusted", "sensitive": False}

    # default
    return {"type": "unknown", "trust": "untrusted", "sensitive": False}
