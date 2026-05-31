"""run_with_outcome — обёртка для выполнения скриптов с Outcome Contract.

Позволяет запускать любой Python-скрипт и получать TaskOutcome
с корректным outcome code (OK при успехе, ERROR при ошибке, и т.д.)
"""
import os
import signal
import subprocess
import sys
import textwrap
import traceback
from typing import Any, Callable, Optional

from task_outcome import TaskOutcome, OutcomeCode, outcome_ok, outcome_error, format_trace


def run_script(
    script_path: str,
    args: Optional[list[str]] = None,
    timeout: int = 30,
    workdir: Optional[str] = None,
) -> TaskOutcome:
    """Запускает Python-скрипт через subprocess.

    Args:
        script_path: Путь к Python-скрипту.
        args: Аргументы командной строки для скрипта.
        timeout: Таймаут в секундах (по умолчанию 30).
        workdir: Рабочая директория (если None — текущая).

    Returns:
        TaskOutcome с результатом выполнения.
    """
    basename = os.path.basename(script_path)
    args = args or []
    grounding_refs = [script_path]

    cmd = [sys.executable, script_path] + args

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=workdir,
            text=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            detail = f"Timeout after {timeout}s"
            msg = f"Скрипт {basename} завершился с ошибкой"
            return outcome_error(message=msg, details=detail, grounding_refs=grounding_refs)

        if proc.returncode == 0:
            msg = f"Скрипт {basename} выполнен"
            return outcome_ok(message=msg, grounding_refs=grounding_refs)
        else:
            # Захватываем последние 1000 символов stderr
            truncated_stderr = stderr[-1000:] if stderr else ""
            detail = f"Exit code {proc.returncode}"
            if truncated_stderr:
                detail += f": {truncated_stderr}"
            msg = f"Скрипт {basename} завершился с ошибкой"
            return outcome_error(message=msg, details=detail, grounding_refs=grounding_refs)

    except FileNotFoundError:
        detail = f"Script not found: {script_path}"
        msg = f"Скрипт {basename} завершился с ошибкой"
        return outcome_error(message=msg, details=detail, grounding_refs=grounding_refs)
    except Exception as e:
        detail = f"Unexpected error: {e}"
        msg = f"Скрипт {basename} завершился с ошибкой"
        return outcome_error(message=msg, details=detail, grounding_refs=grounding_refs)


def run_function(
    fn: Callable,
    *args: Any,
    timeout: int = 30,
    **kwargs: Any,
) -> TaskOutcome:
    """Запускает Python-функцию, перехватывает исключения.

    Args:
        fn: Функция для выполнения.
        *args: Позиционные аргументы для функции.
        timeout: Таймаут в секундах (по умолчанию 30).
        **kwargs: Именованные аргументы для функции.

    Returns:
        TaskOutcome с результатом выполнения.
    """
    fn_name = getattr(fn, "__name__", repr(fn))

    class TimeoutError(Exception):
        pass

    def timeout_handler(signum, frame):
        raise TimeoutError(f"Timeout after {timeout}s")

    # Сохраняем текущий обработчик
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    try:
        signal.alarm(timeout)
        try:
            fn(*args, **kwargs)
        except TimeoutError as e:
            msg = f"Функция {fn_name} завершилась с ошибкой"
            return outcome_error(message=msg, details=str(e))
        except Exception as e:
            tb = traceback.format_exc()
            msg = f"Функция {fn_name} завершилась с ошибкой"
            return outcome_error(message=msg, details=tb)
        else:
            msg = f"Функция {fn_name} выполнена"
            return outcome_ok(message=msg)
        finally:
            signal.alarm(0)
    finally:
        signal.signal(signal.SIGALRM, old_handler)


def run_with_outcome(
    script_or_fn,
    *args: Any,
    timeout: int = 30,
    **kwargs: Any,
) -> TaskOutcome:
    """Единый интерфейс для выполнения скриптов или функций.

    Если script_or_fn — строка (путь к файлу), вызывает run_script.
    Если callable, вызывает run_function.

    Args:
        script_or_fn: Путь к скрипту (str) или функция (callable).
        *args: Аргументы для run_function (не используются для run_script).
        timeout: Таймаут в секундах.
        **kwargs: Именованные аргументы для run_function.

    Returns:
        TaskOutcome с результатом выполнения.
    """
    if isinstance(script_or_fn, str):
        return run_script(script_or_fn, timeout=timeout)
    elif callable(script_or_fn):
        return run_function(script_or_fn, *args, timeout=timeout, **kwargs)
    else:
        return outcome_error(
            message="Неподдерживаемый тип аргумента",
            details=f"Ожидается строка (путь к скрипту) или callable, получен {type(script_or_fn).__name__}",
        )
