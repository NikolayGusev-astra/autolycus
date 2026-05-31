"""response_verifier — Verify Gate для проверки TaskOutcome перед отправкой.

Проверяет:
- grounding_refs: непустые строки, существование файлов на диске
- completeness: достаточность объяснения, наличие refs для утверждений
- contradiction: непротиворечие сообщения прочитанным данным
"""
import os
from typing import Any
from task_outcome import TaskOutcome, OutcomeCode


def verify_grounding_refs(refs: list[str]) -> list[str]:
    """Проверяет что каждый ref — str и непустой.

    - Если ref выглядит как локальный путь (начинается с / или ./) и файл существует — хорошо
    - Если ref выглядит как локальный путь, но файл НЕ существует — замечание (не ошибка)
    - URL и wiki-ссылки (без / или ./ в начале) пропускаются без проверки существования
    - Возвращает список проблем (пустой = всё ок)
    """
    problems: list[str] = []

    for i, ref in enumerate(refs):
        if not isinstance(ref, str):
            problems.append(f"grounding_refs[{i}] must be a string, got {type(ref).__name__}")
            continue

        if not ref:
            problems.append(f"grounding_refs[{i}] must not be empty")
            continue

        # Проверка существования только для абсолютных или явных относительных путей
        if ref.startswith("/") or ref.startswith("./"):
            if not os.path.exists(ref):
                problems.append(f"grounding_refs[{i}] file not found: {ref}")
            # else: file exists — хорошо, ничего не добавляем

    return problems


def verify_outcome_completeness(outcome: TaskOutcome) -> list[str]:
    """Проверяет достаточность объяснения в сообщении.

    - DENIED_*: message должен объяснять ПОЧЕМУ отказано (мин. 10 символов)
    - CLARIFICATION: message должен указывать ЧТО нужно уточнить (мин. 10 символов)
    - ERROR: message не должен быть generic ("Error occurred", "Something went wrong")
    - OK: если grounding_refs пуст, а message — утверждение → замечание
    """
    problems: list[str] = []

    if outcome.code in (OutcomeCode.DENIED_SECURITY, OutcomeCode.DENIED_POLICY):
        if len(outcome.message) < 10:
            problems.append(
                f"DENIED message too short ({len(outcome.message)} chars); "
                "must explain why access was denied (min 10 chars)"
            )

    elif outcome.code == OutcomeCode.CLARIFICATION:
        if len(outcome.message) < 10:
            problems.append(
                f"CLARIFICATION message too short ({len(outcome.message)} chars); "
                "must specify what needs clarification (min 10 chars)"
            )

    elif outcome.code == OutcomeCode.ERROR:
        generic_msgs = {"Error occurred", "Something went wrong"}
        if outcome.message.strip() in generic_msgs:
            problems.append(
                "ERROR message is too generic; provide a specific error description"
            )

    elif outcome.code == OutcomeCode.OK:
        # Если нет grounding_refs и message похоже на утверждение (начинается с буквы)
        if not outcome.grounding_refs:
            msg = outcome.message.strip()
            if msg and msg[0].isalpha():
                problems.append(
                    "OK message appears to be an assertion but has no sources "
                    "(grounding_refs) to support it"
                )

    return problems


def verify_no_contradiction(outcome: TaskOutcome, context: dict | None = None) -> list[str]:
    """Проверяет что outcome.message не противоречит контексту.

    Если context передан, проверяет:
    - Если context содержит "read_files": [...] и outcome говорит "файл не найден" — ок
    - Если context содержит "found_result": True и outcome говорит "ничего не найдено" — проблема
    - Пока это заглушка: записывает проблемы если context неправильного типа или отсутствует при DENIED
    """
    problems: list[str] = []

    # Проверка типа context
    if context is not None and not isinstance(context, dict):
        problems.append(
            f"context must be a dict, got {type(context).__name__}"
        )
        return problems

    # Если context не передан при DENIED — замечание
    if context is None and outcome.code in (OutcomeCode.DENIED_SECURITY, OutcomeCode.DENIED_POLICY):
        problems.append(
            "context is recommended when outcome code is DENIED_*; "
            "pass context for contradiction check"
        )

    # TODO: полноценная проверка на противоречия (заглушка)
    if context is not None:
        msg_lower = outcome.message.lower()
        read_files = context.get("read_files", [])
        if read_files and ("файл не найден" in msg_lower or "file not found" in msg_lower):
            # Сказано что файл не найден — ок, нет противоречия
            pass

        found_result = context.get("found_result", None)
        if found_result is True and ("ничего не найдено" in msg_lower or "nothing found" in msg_lower or "not found" in msg_lower):
            problems.append(
                "Contradiction: context indicates result was found, "
                "but message says nothing was found"
            )

    return problems


def verify_response(outcome: TaskOutcome, context: dict | None = None) -> tuple[bool, list[str]]:
    """Запускает все проверки + verify_outcome из task_outcome.py.

    Агрегирует все проблемы.
    Возвращает (passed, [problems]).
    passed=True только если problems пуст.
    """
    all_problems: list[str] = []

    # Базовая проверка из task_outcome
    _, verify_errors = outcome.verify_outcome() if hasattr(outcome, 'verify_outcome') else (True, [])
    # На случай если используем импортированную функцию
    from task_outcome import verify_outcome as _verify_outcome
    _, vo_errors = _verify_outcome(outcome)
    all_problems.extend(vo_errors)

    # Специфические проверки
    all_problems.extend(verify_grounding_refs(outcome.grounding_refs))
    all_problems.extend(verify_outcome_completeness(outcome))
    all_problems.extend(verify_no_contradiction(outcome, context))

    return (len(all_problems) == 0, all_problems)
