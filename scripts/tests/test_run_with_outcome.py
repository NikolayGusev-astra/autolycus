"""Tests for run_with_outcome.py — wrapper for executing scripts with Outcome Contract."""
import sys
import os
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_with_outcome import run_script, run_function, run_with_outcome
from task_outcome import OutcomeCode


class TestRunScript:
    """Tests for run_script()."""

    def test_run_successful_script(self):
        """Создать временный .py файл с print("hello"), запустить, проверить code == OK."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('print("hello")\n')
            script_path = f.name
        try:
            outcome = run_script(script_path)
            assert outcome.code == OutcomeCode.OK
            assert outcome.message == f"Скрипт {os.path.basename(script_path)} выполнен"
            assert script_path in outcome.grounding_refs
        finally:
            os.unlink(script_path)

    def test_run_failing_script(self):
        """Создать временный .py файл с raise RuntimeError, проверить code == ERROR."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('raise RuntimeError("fail")\n')
            script_path = f.name
        try:
            outcome = run_script(script_path)
            assert outcome.code == OutcomeCode.ERROR
            assert outcome.message == f"Скрипт {os.path.basename(script_path)} завершился с ошибкой"
            assert "fail" in outcome.details
            assert script_path in outcome.grounding_refs
        finally:
            os.unlink(script_path)

    def test_timeout(self):
        """Создать скрипт с time.sleep(60), запустить с timeout=2, проверить error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('import time; time.sleep(60)\n')
            script_path = f.name
        try:
            outcome = run_script(script_path, timeout=2)
            assert outcome.code == OutcomeCode.ERROR
            assert "Timeout" in outcome.details
            assert script_path in outcome.grounding_refs
        finally:
            os.unlink(script_path)

    def test_run_script_with_args(self):
        """Создать скрипт, использующий sys.argv, запустить с аргументами."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('import sys; print(sys.argv[1:])\n')
            script_path = f.name
        try:
            outcome = run_script(script_path, args=["--flag", "value"])
            assert outcome.code == OutcomeCode.OK
        finally:
            os.unlink(script_path)


class TestRunFunction:
    """Tests for run_function()."""

    def test_run_function_success(self):
        def add(a, b):
            return a + b
        outcome = run_function(add, 2, 3)
        assert outcome.code == OutcomeCode.OK

    def test_run_function_exception(self):
        def fail():
            raise ValueError("bad")
        outcome = run_function(fail)
        assert outcome.code == OutcomeCode.ERROR
        assert "bad" in outcome.details

    def test_run_function_success_with_kwargs(self):
        def greet(name, greeting="Hello"):
            return f"{greeting}, {name}!"
        outcome = run_function(greet, "World", greeting="Hi")
        assert outcome.code == OutcomeCode.OK


class TestRunWithOutcome:
    """Tests for run_with_outcome() — the unified interface."""

    def test_run_with_outcome_script_path(self):
        """Передать строку с путем, проверить что вызывается run_script (code OK)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('print("ok")\n')
            script_path = f.name
        try:
            outcome = run_with_outcome(script_path)
            assert outcome.code == OutcomeCode.OK
            assert os.path.basename(script_path) in outcome.message
        finally:
            os.unlink(script_path)

    def test_run_with_outcome_callable(self):
        """Передать лямбду, проверить что вызывается run_function (code OK)."""
        outcome = run_with_outcome(lambda: 42)
        assert outcome.code == OutcomeCode.OK

    def test_run_with_outcome_failing_callable(self):
        """Передать лямбду с ошибкой, проверить code == ERROR."""
        outcome = run_with_outcome(lambda: 1 / 0)
        assert outcome.code == OutcomeCode.ERROR
        assert "division by zero" in outcome.details


class TestStderrTruncation:
    """Tests for stderr truncation in run_script."""

    def test_stderr_truncation(self):
        """Создать скрипт, который выводит >1000 символов в stderr."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('import sys; sys.stderr.write("x" * 2000); sys.exit(1)\n')
            script_path = f.name
        try:
            outcome = run_script(script_path)
            assert outcome.code == OutcomeCode.ERROR
            # Should be truncated to ~1000 chars
            assert len(outcome.details) <= 1050, f"details too long: {len(outcome.details)}"
        finally:
            os.unlink(script_path)
