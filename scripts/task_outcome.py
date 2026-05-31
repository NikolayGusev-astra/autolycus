"""Outcome Contract — формальный протокол завершения задач для AI-агентов.

Определяет OutcomeCode (строгие коды завершения) и TaskOutcome (dataclass
с результатом), а также функции проверки, форматирования и фабричные хелперы.
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Optional


class OutcomeCode(Enum):
    """Строгие коды завершения задачи."""
    OK = "success"
    DENIED_SECURITY = "denied_security"      # Отказ по соображениям безопасности
    DENIED_POLICY = "denied_policy"          # Отказ по политике/правилам
    CLARIFICATION = "clarification"          # Требуется уточнение от пользователя
    UNSUPPORTED = "unsupported"              # У агента нет такого инструмента/возможности
    ERROR = "error"                          # Техническая ошибка при выполнении


@dataclass
class TaskOutcome:
    """Формальный результат выполнения задачи.

    code: OutcomeCode — код завершения
    message: str — человекочитаемое сообщение (для пользователя)
    grounding_refs: list[str] — ссылки на источники (файлы, URL, wiki-страницы)
    details: Optional[str] = None — технические детали (для логов)
    """
    code: OutcomeCode
    message: str
    grounding_refs: list[str] = field(default_factory=list)
    details: Optional[str] = None


def verify_outcome(outcome: TaskOutcome) -> tuple[bool, list[str]]:
    """Проверяет корректность TaskOutcome.

    Возвращает (валидно, [список_ошибок]).
    """
    errors: list[str] = []

    if outcome.code is None:
        errors.append("code must not be None")
    elif not isinstance(outcome.code, OutcomeCode):
        errors.append("code must be an OutcomeCode member")

    if not outcome.message:
        errors.append("message must not be empty")

    if not isinstance(outcome.grounding_refs, list):
        errors.append("grounding_refs must be a list")
    else:
        for i, ref in enumerate(outcome.grounding_refs):
            if not isinstance(ref, str):
                errors.append(f"grounding_refs[{i}] must be a string")

    if outcome.code == OutcomeCode.ERROR and not outcome.details:
        errors.append("details must be provided when code is ERROR")

    return (len(errors) == 0, errors)


def format_outcome(outcome: TaskOutcome) -> str:
    """Форматирует TaskOutcome для отправки пользователю."""
    prefix_map = {
        OutcomeCode.DENIED_SECURITY: "🚫 Отказано: ",
        OutcomeCode.DENIED_POLICY: "🚫 Отказано: ",
        OutcomeCode.CLARIFICATION: "❓ Уточнение: ",
        OutcomeCode.UNSUPPORTED: "⚠️ Недоступно: ",
        OutcomeCode.ERROR: "❌ Ошибка: ",
    }

    if outcome.code == OutcomeCode.OK:
        text = outcome.message
    else:
        prefix = prefix_map.get(outcome.code, "")
        text = prefix + outcome.message
        if outcome.code == OutcomeCode.ERROR and outcome.details:
            text += f" ({outcome.details})"

    if outcome.grounding_refs:
        refs_str = "; ".join(outcome.grounding_refs)
        text += f"\nИсточники: {refs_str}"

    return text


def format_trace(outcome: TaskOutcome) -> str:
    """Форматирует TaskOutcome для логов.

    Формат: [OUTCOME: {code.value}] {message[:100]} | refs: {N} | details: {details}
    """
    msg_truncated = outcome.message[:100]
    refs_count = len(outcome.grounding_refs) if outcome.grounding_refs else 0
    details_str = outcome.details if outcome.details else ""
    return f"[OUTCOME: {outcome.code.value}] {msg_truncated} | refs: {refs_count} | details: {details_str}"


def _make_outcome(
    code: OutcomeCode,
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Базовый хелпер для создания TaskOutcome."""
    return TaskOutcome(
        code=code,
        message=message,
        grounding_refs=grounding_refs if grounding_refs is not None else [],
        details=details,
    )


def outcome_ok(
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом OK."""
    return _make_outcome(OutcomeCode.OK, message, grounding_refs, details)


def outcome_denied_security(
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом DENIED_SECURITY."""
    return _make_outcome(OutcomeCode.DENIED_SECURITY, message, grounding_refs, details)


def outcome_denied_policy(
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом DENIED_POLICY."""
    return _make_outcome(OutcomeCode.DENIED_POLICY, message, grounding_refs, details)


def outcome_clarification(
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом CLARIFICATION."""
    return _make_outcome(OutcomeCode.CLARIFICATION, message, grounding_refs, details)


def outcome_unsupported(
    message: str,
    grounding_refs: Optional[list[str]] = None,
    details: Optional[str] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом UNSUPPORTED."""
    return _make_outcome(OutcomeCode.UNSUPPORTED, message, grounding_refs, details)


def outcome_error(
    message: str,
    details: str,
    grounding_refs: Optional[list[str]] = None,
) -> TaskOutcome:
    """Создаёт TaskOutcome с кодом ERROR.

    Для ERROR details обязателен (проверяется verify_outcome).
    """
    return TaskOutcome(
        code=OutcomeCode.ERROR,
        message=message,
        grounding_refs=grounding_refs if grounding_refs is not None else [],
        details=details,
    )
