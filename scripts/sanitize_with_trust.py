"""sanitize_with_trust — интеграция trust_classifier с Outcome Contract.

Принимает входящие сообщения/файлы, классифицирует уровень доверия,
и возвращает TaskOutcome: OK для safe, DENIED для malicious, CLARIFICATION для suspicious.
"""
from task_outcome import (
    TaskOutcome,
    outcome_ok,
    outcome_denied_security,
    outcome_clarification,
)
from trust_classifier import (
    classify_source,
    classify_user_request,
    get_trust_annotation,
    check_instruction_source,
)


# ── sanitize_input ───────────────────────────────────────────────────────────


def sanitize_input(
    message: str, source_type: str = "user"
) -> tuple[str | None, TaskOutcome]:
    """Принимает входящее сообщение + тип источника и возвращает результат проверки.

    Args:
        message: Входящее сообщение пользователя/системы.
        source_type: Тип источника ("user", "system", "external").

    Returns:
        (None, DENIED_SECURITY) если malicious,
        (None, CLARIFICATION) если suspicious,
        (message, OK) если safe.
    """
    verdict, reasons = classify_user_request(message)

    if verdict == "malicious":
        reason_str = "; ".join(reasons) if reasons else "malicious content detected"
        return (
            None,
            outcome_denied_security(
                f"Обнаружена попытка prompt injection: {reason_str}"
            ),
        )

    if verdict == "suspicious":
        reason_str = "; ".join(reasons) if reasons else "suspicious content detected"
        return (
            None,
            outcome_clarification(
                f"Ваш запрос содержит подозрительные конструкции: {reason_str}"
            ),
        )

    # safe
    return (message, outcome_ok("Input validated"))


# ── sanitize_file_content ────────────────────────────────────────────────────


def sanitize_file_content(
    content: str, file_path: str
) -> tuple[str, TaskOutcome]:
    """Принимает содержимое файла + путь и возвращает результат проверки.

    Args:
        content: Содержимое файла.
        file_path: Путь к файлу.

    Returns:
        (content, OK) если файл trusted или untrusted без инструкций,
        (content, CLARIFICATION) если untrusted с инструкциями.
        content никогда не изменяется.
    """
    annotation = get_trust_annotation(file_path)
    trust_level = annotation.get("trust", "untrusted")

    if trust_level == "trusted":
        return (content, outcome_ok("File is trusted"))

    # untrusted or semi_trusted — check for instructions
    matched = check_instruction_source(content, trust_level)
    if matched:
        match_str = ", ".join(matched)
        return (
            content,
            outcome_clarification(
                f"Warning: untrusted file contains executable instructions: {match_str}"
            ),
        )

    return (content, outcome_ok("No executable instructions detected"))


# ── sanitize_api_response ────────────────────────────────────────────────────


def sanitize_api_response(
    data: str, source_url: str
) -> tuple[str | None, TaskOutcome]:
    """Принимает данные из внешнего API + URL и возвращает результат проверки.

    Args:
        data: Данные из внешнего API.
        source_url: URL источника данных.

    Returns:
        (data, OK) если данные безопасны,
        (None, DENIED_SECURITY) если содержат явные инструкции.
    """
    # Определяем доверие к источнику
    trust_level = classify_source("external", source_url)

    # Проверяем на наличие явных инструкций (javascript code blocks, system commands)
    data_lower = data.lower()
    explicit_instructions = []

    if "```javascript" in data_lower or "<script>" in data_lower:
        explicit_instructions.append("javascript code block")
    if "```bash" in data_lower or "```sh" in data_lower or "```shell" in data_lower:
        explicit_instructions.append("shell code block")
    if "```python" in data_lower and (
        "os.system" in data_lower or "subprocess" in data_lower
    ):
        explicit_instructions.append("python code with system calls")

    # Проверяем также через check_instruction_source для untrusted источников
    if trust_level == "untrusted":
        instruction_matches = check_instruction_source(data, "untrusted")
        explicit_instructions.extend(
            f"imperative pattern: {m}" for m in instruction_matches
        )

    if explicit_instructions:
        reason = "; ".join(explicit_instructions)
        return (
            None,
            outcome_denied_security(
                f"API response contains executable instructions: {reason}"
            ),
        )

    return (data, outcome_ok("API response validated"))
