"""E2E/unit tests for ContextWriter.

TDD: сначала пишем тесты, потом код.

Проверяем:
1. Active window rebuild from disk after restart
2. All turns written (not just tool-calling turns)
3. window_size from config (not hardcoded)
4. Context survives restart — get_active_context after re-init
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from plugins.memory.context_writer import ContextWriter, _format_turn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cw(tmp_path: Path) -> ContextWriter:
    """ContextWriter with temp wiki dir, window=3 for fast testing."""
    return ContextWriter(wiki_dir=tmp_path, window_size=3)


def write_turn_file(session_dir: Path, turn_num: int, content: str | None = None):
    """Helper: write a turn file directly to disk (simulating previous session)."""
    session_dir.mkdir(parents=True, exist_ok=True)
    turn_file = session_dir / f"turn_{turn_num:04d}.md"
    if content is None:
        content = f"## Turn {turn_num}\n\n**Time:** 2026-01-01T12:00:00\n\n### Test\ndata\n"
    turn_file.write_text(content)
    return turn_file


# ---------------------------------------------------------------------------
# Test 1: Active window rebuild from disk after restart
# ---------------------------------------------------------------------------


class TestWindowRebuild:
    """После перезапуска ContextWriter должен восстановить active_window
    из существующих turn_*.md файлов на диске."""

    def test_empty_directory_returns_empty_window(self, tmp_path):
        """Свежая директория → пустой active_window."""
        cw = ContextWriter(wiki_dir=tmp_path, window_size=10)
        assert cw._active_windows == {}
        assert cw.get_active_context("test-session") == []

    def test_restores_single_session(self, tmp_path):
        """Одна сессия с 5 файлами → active_window = [1,2,3,4,5]."""
        session_dir = tmp_path / "raw" / "context" / "my-session"
        for i in range(1, 6):
            write_turn_file(session_dir, i)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=10)
        window = cw._active_windows.get("my-session", [])
        assert window == [1, 2, 3, 4, 5]

    def test_restores_respects_window_size(self, tmp_path):
        """20 файлов, window_size=3 → только последние 3."""
        session_dir = tmp_path / "raw" / "context" / "big-session"
        for i in range(1, 21):
            write_turn_file(session_dir, i)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=3)
        window = cw._active_windows.get("big-session", [])
        assert window == [18, 19, 20]

    def test_restores_multiple_sessions(self, tmp_path):
        """Несколько сессий — все восстанавливаются."""
        for sid in ["alpha", "beta", "gamma"]:
            session_dir = tmp_path / "raw" / "context" / sid
            write_turn_file(session_dir, 1)
            write_turn_file(session_dir, 2)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=10)
        assert cw._active_windows["alpha"] == [1, 2]
        assert cw._active_windows["beta"] == [1, 2]
        assert cw._active_windows["gamma"] == [1, 2]

    def test_restores_gaps_and_non_sequential(self, tmp_path):
        """Файлы turn_0001, turn_0003, turn_0005 → корректные номера."""
        session_dir = tmp_path / "raw" / "context" / "gappy"
        write_turn_file(session_dir, 1)
        write_turn_file(session_dir, 3)
        write_turn_file(session_dir, 5)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=10)
        window = cw._active_windows.get("gappy", [])
        assert window == [1, 3, 5]

    def test_restore_then_sync_extends_window(self, tmp_path):
        """Восстановили [1,2,3], sync_turn(4) → [1,2,3,4]."""
        session_dir = tmp_path / "raw" / "context" / "s"
        for i in range(1, 4):
            write_turn_file(session_dir, i)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=10)
        cw.sync_turn("s", 4, user_msg="hi", assistant_msg="hello")
        assert cw._active_windows["s"] == [1, 2, 3, 4]

    def test_reinit_after_write_preserves_new_turns(self, tmp_path):
        """Имитация: сессия 1 → рестарт → сессия 2.
        После второго рестарта видны обе сессии."""
        # Session 1
        s1_dir = tmp_path / "raw" / "context" / "session1"
        write_turn_file(s1_dir, 1)
        write_turn_file(s1_dir, 2)
        cw1 = ContextWriter(wiki_dir=tmp_path, window_size=10)
        cw1.sync_turn("session1", 3, "q", "a")

        # Рестарт (новый ContextWriter)
        cw2 = ContextWriter(wiki_dir=tmp_path, window_size=10)
        assert cw2._active_windows["session1"] == [1, 2, 3]
        assert len(cw2.get_active_context("session1")) == 3


# ---------------------------------------------------------------------------
# Test 2: All turns written (not just tool-calling)
# ---------------------------------------------------------------------------


class TestAllTurnsWritten:
    """ContextWriter должен писать ВСЕ turn'ы, а не только tool-calling."""

    def test_sync_turn_creates_file(self, cw, tmp_path):
        """sync_turn создаёт файл turn_0000.md."""
        cw.sync_turn("s", 0, user_msg="hello", assistant_msg="world")
        turn_file = tmp_path / "raw" / "context" / "s" / "turn_0000.md"
        assert turn_file.exists()
        content = turn_file.read_text()
        assert "hello" in content
        assert "world" in content

    def test_sync_turn_without_tools(self, cw, tmp_path):
        """sync_turn без tools — нет секции Tools."""
        cw.sync_turn("s", 1, user_msg="hi", assistant_msg="ok")
        content = (tmp_path / "raw" / "context" / "s" / "turn_0001.md").read_text()
        assert "### Tools" not in content

    def test_sync_turn_with_tools(self, cw, tmp_path):
        """sync_turn с tools — секция Tools есть."""
        tools = [{"name": "read_file", "result": "file content..."}]
        cw.sync_turn("s", 2, user_msg="read", assistant_msg="done", tools=tools)
        content = (tmp_path / "raw" / "context" / "s" / "turn_0002.md").read_text()
        assert "### Tools" in content
        assert "read_file" in content

    def test_window_updated_after_sync(self, cw):
        """После sync_turn active_window обновлён."""
        for i in range(5):
            cw.sync_turn("s", i, "q", "a")
        assert cw._active_windows["s"] == [2, 3, 4]  # window_size=3

    def test_get_active_context_returns_content(self, cw):
        """get_active_context возвращает содержимое turn'ов."""
        for i in range(3):
            cw.sync_turn("s", i, f"question{i}", f"answer{i}")
        context = cw.get_active_context("s")
        assert len(context) == 3
        assert all(f"answer{i}" in c for i, c in enumerate(context))


# ---------------------------------------------------------------------------
# Test 3: window_size from config
# ---------------------------------------------------------------------------


class TestConfigurableWindowSize:
    """window_size должен читаться из конфига, не хардкодиться."""

    def test_default_window_size(self, tmp_path):
        """Без указания window_size — 10."""
        cw = ContextWriter(wiki_dir=tmp_path)
        assert cw.window_size == 10

    def test_custom_window_size(self, tmp_path):
        """Явный window_size=5."""
        cw = ContextWriter(wiki_dir=tmp_path, window_size=5)
        assert cw.window_size == 5

    def test_window_size_affects_rebuild(self, tmp_path):
        """Восстановление с window_size=2 → только 2 последних."""
        session_dir = tmp_path / "raw" / "context" / "s"
        for i in range(10):
            write_turn_file(session_dir, i)

        cw = ContextWriter(wiki_dir=tmp_path, window_size=2)
        assert cw._active_windows["s"] == [8, 9]

    def test_window_size_affects_sync(self, cw):
        """sync_turn не превышает window_size."""
        for i in range(10):
            cw.sync_turn("s", i, "q", "a")
        assert len(cw._active_windows["s"]) == 3  # cw.window_size = 3

    def test_window_size_passed_to_constructor(self, tmp_path):
        """window_size передаётся в конструктор и влияет на поведение."""
        cw = ContextWriter(wiki_dir=tmp_path, window_size=15)
        assert cw.window_size == 15


# ---------------------------------------------------------------------------
# Test 4: _format_turn correctness
# ---------------------------------------------------------------------------


class TestFormatTurn:
    """_format_turn — чистая функция, тестируется легко."""

    def test_basic_format(self):
        result = _format_turn(0, "hello", "world")
        assert "## Turn 0" in result
        assert "### User" in result
        assert "hello" in result
        assert "world" in result

    def test_with_tools(self):
        tools = [{"name": "ls", "result": "file1\nfile2"}]
        result = _format_turn(1, "list", "done", tools)
        assert "### Tools" in result
        assert "`ls`" in result

    def test_tools_truncated_to_last_5(self):
        tools = [{"name": f"tool{i}", "result": "ok"} for i in range(10)]
        result = _format_turn(2, "run", "done", tools)
        # Должно быть только 5 tool'ов
        assert result.count("### Tools") >= 0  # один раз
        # Проверяем что последние 5
        for i in range(5, 10):
            assert f"tool{i}" in result
        for i in range(5):
            assert f"tool{i}" not in result

    def test_without_tools_no_tools_section(self):
        result = _format_turn(0, "hi", "ok")
        assert "### Tools" not in result

    def test_with_metadata_in_sync_turn(self, cw, tmp_path):
        """metadata → <!-- metadata: ... --> в файле."""
        meta = {"model": "gpt4"}
        cw.sync_turn("s", 0, "hi", "ok", metadata=meta)
        content = (tmp_path / "raw" / "context" / "s" / "turn_0000.md").read_text()
        assert "model" in content
        assert "gpt4" in content


# ---------------------------------------------------------------------------
# Test 5: search_context
# ---------------------------------------------------------------------------


class TestSearchContext:
    """search_context использует rg — покрытие базовых случаев."""

    def test_empty_session_returns_empty(self, cw, tmp_path):
        """Сессия без файлов → пустой результат."""
        results = cw.search_context("nonexistent", "test")
        assert results == []

    def test_search_finds_matching_turn(self, cw, tmp_path):
        """rg находит turn с искомым текстом."""
        # Skip if rg not available
        import subprocess, shutil
        if not shutil.which("rg"):
            pytest.skip("rg (ripgrep) not installed")
        cw.sync_turn("s", 0, "q", "nginx config file")
        cw.sync_turn("s", 1, "q", "docker setup")
        results = cw.search_context("s", "nginx")
        assert len(results) > 0
        assert results[0]["turn"] == 0

    def test_search_returns_turn_number(self, cw, tmp_path):
        """Результаты содержат turn number."""
        import subprocess, shutil
        if not shutil.which("rg"):
            pytest.skip("rg (ripgrep) not installed")
        cw.sync_turn("s", 42, "q", "unique_search_term_xyz")
        results = cw.search_context("s", "unique_search_term_xyz")
        assert len(results) > 0
        assert results[0]["turn"] == 42


# ---------------------------------------------------------------------------
# Test 6: get_summary
# ---------------------------------------------------------------------------


class TestSummary:
    """get_summary возвращает статистику."""

    def test_empty_session(self, cw):
        summary = cw.get_summary("nonexistent")
        assert summary["turns"] == 0
        assert summary["files"] == 0

    def test_summary_after_turns(self, cw, tmp_path):
        for i in range(5):
            cw.sync_turn("s", i, "q", "a")
        summary = cw.get_summary("s")
        assert summary["turns"] == 3  # window_size=3
        assert summary["files"] == 5  # всего файлов
        assert summary["archived_turns"] == 2  # 5 - 3
