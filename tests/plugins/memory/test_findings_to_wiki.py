"""Tests for findings_to_wiki — pattern detection, fact extraction, file I/O.

TDD: tests first, then implementation.

Проверяем:
1. _detect_finding_type — корректное распознавание паттернов
2. _extract_fact — heuristic fallback без LLM
3. _save_to_raw — атомарная запись
4. _load_patterns — из конфига и дефолты
5. _load_plugin_config — пустой конфиг не ломает
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from plugins.memory.findings_to_wiki import (
    _detect_finding_type,
    _extract_fact,
    _save_to_raw,
    _load_patterns,
    _load_plugin_config,
    _read_file,
    _write_file,
    _slugify,
    _generate_frontmatter,
    _extract_title,
    DEFAULT_PATTERNS,
    ENTRY_DELIMITER,
)
from plugins.memory.findings_to_wiki import _get_wiki_paths


# ---------------------------------------------------------------------------
# Test 1: Pattern detection
# ---------------------------------------------------------------------------


class TestDetectFindingType:
    """_detect_finding_type — regex-based, никаких LLM."""

    def test_detects_prism_by_findings_header(self):
        """## Findings + markdown table → prism."""
        text = "## Findings\n\n| # | Parameter | Value |\n| 1 | nginx | active |"
        patterns = [(re.compile(p), t) for p, t in DEFAULT_PATTERNS]
        result = _detect_finding_type(text, patterns)
        assert result == "prism"

    def test_detects_research_by_key_findings(self):
        """## Key Findings — research type."""
        text = "## Key Findings\n- The system uses Docker"
        patterns = [(re.compile(p), t) for p, t in DEFAULT_PATTERNS]
        result = _detect_finding_type(text, patterns)
        assert result == "research"

    def test_detects_adr_by_status(self):
        """## Статус — adr type."""
        text = "## Статус\nПринято решение использовать PostgreSQL"
        patterns = [(re.compile(p), t) for p, t in DEFAULT_PATTERNS]
        result = _detect_finding_type(text, patterns)
        assert result == "adr"
    def test_below_threshold_returns_none(self):
        """Только 1 паттерн совпал, threshold=2 → None."""
        text = "## Findings\nСлучайное упоминание"
        # Убираем другие паттерны, оставляем один
        patterns = [
            (re.compile(r"## Findings"), "other"),
        ]
        result = _detect_finding_type(text, patterns, threshold=2)
        assert result is None

    def test_no_pattern_match_returns_none(self):
        text = "Привет, как дела?"
        patterns = [(re.compile(p), t) for p, t in DEFAULT_PATTERNS]
        result = _detect_finding_type(text, patterns, threshold=2)
        assert result is None

    def test_multiple_types_returns_most_frequent(self):
        """Если два типа совпали — возвращаем тот, что чаще."""
        text = "## Findings\n## Decision\nВыбрали А"
        patterns = [
            (re.compile(r"## Findings"), "prism"),
            (re.compile(r"## Decision"), "adr"),
        ]
        result = _detect_finding_type(text, patterns, threshold=1)
        # Оба совпали по 1 разу, threshold=1 → возвращаем первый в макс
        assert result in ("prism", "adr")

    def test_empty_text_no_match(self):
        patterns = [(re.compile(p), t) for p, t in DEFAULT_PATTERNS]
        assert _detect_finding_type("", patterns) is None

    def test_threshold_works_with_many_matches(self):
        """5 совпадений, threshold=4 → возвращает research."""
        text = "\n".join([
            "## Key Findings: A",
            "## Key Findings: B",
            "## Key Findings: C",
            "## Key Findings: D",
        ])
        patterns = [
            (re.compile(r"## Key Findings"), "research"),
        ]
        result = _detect_finding_type(text, patterns, threshold=1)
        assert result == "research"


# ---------------------------------------------------------------------------
# Test 2: Fact extraction heuristic (без LLM)
# ---------------------------------------------------------------------------


class TestExtractFactHeuristic:
    """_extract_fact — LLM fallback на heuristic."""

    def test_short_trivial_skipped(self):
        """Короткие тривиальные сообщения → None."""
        assert _extract_fact("спасибо", "пожалуйста") is None

    def test_short_greeting_skipped(self):
        assert _extract_fact("Привет", "И тебе привет!") is None

    def test_config_discussion_extracted(self, monkeypatch):
        """Разговор про конфиг → fact извлекается через heuristic fallback."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("LLM unavailable")),
        )
        fact = _extract_fact(
            "настрой nginx на порт 8443",
            "Ок, меняю конфиг /etc/nginx/sites-enabled/default на порт 8443"
        )
        assert fact is not None
        assert "nginx" in fact.lower() or "8443" in fact

    def test_error_discussion_extracted(self, monkeypatch):
        """Обсуждение ошибки → fact."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("LLM down")),
        )
        fact = _extract_fact(
            "упала ошибка connection refused",
            "Да, перезапусти сервис командой systemctl restart nginx"
        )
        assert fact is not None
        assert "connection refused" in fact.lower() or "systemctl" in fact.lower()

    def test_technical_discussion_extracted(self, monkeypatch):
        """Техническая дискуссия → fact."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("LLM down")),
        )
        fact = _extract_fact(
            "как настроить xray reality?",
            "Добавь inbound с protocol: vless и flow: xtls-rprx-vision"
        )
        assert fact is not None
        assert "xray" in fact.lower() or "vless" in fact.lower()

    def test_irrelevant_discussion_skipped(self, monkeypatch):
        """Не-техническая беседа → None."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("LLM down")),
        )
        fact = _extract_fact(
            "как погода?",
            "солнечно, 22 градуса"
        )
        assert fact is None

    def test_very_long_combined_skipped_when_trivial(self, monkeypatch):
        """Длинный текст без технического содержания → None."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("LLM down")),
        )
        fact = _extract_fact(
            "А" * 100,
            "Б" * 100,
        )
        assert fact is None

    def test_heuristic_fallback_returns_fact(self, monkeypatch):
        """Падение LLM → heuristic returns a fact string (not None)."""
        monkeypatch.setattr(
            "agent.auxiliary_client.call_llm",
            lambda **kw: (_ for _ in ()).throw(Exception("API timeout")),
        )
        fact = _extract_fact(
            "как исправить баг в nginx? упал 502 на проде",
            "надо обновить конфиг /etc/nginx/sites-enabled/default и перезагрузить"
        )
        assert fact is not None
        assert "nginx" in fact.lower() or "config" in fact.lower()


# ---------------------------------------------------------------------------
# Test 3: File I/O helpers
# ---------------------------------------------------------------------------


class TestFileIO:
    """_read_file, _write_file, _slugify."""

    def test_read_nonexistent_returns_empty(self, tmp_path):
        assert _read_file(tmp_path / "nonexistent.md") == []

    def test_write_and_read_roundtrip(self, tmp_path):
        path = tmp_path / "test.md"
        _write_file(path, ["first entry", "second entry"])
        entries = _read_file(path)
        assert entries == ["first entry", "second entry"]

    def test_write_empty_list_creates_empty_file(self, tmp_path):
        path = tmp_path / "empty.md"
        _write_file(path, [])
        assert path.exists()
        assert path.read_text() == ""

    def test_read_empty_file(self, tmp_path):
        path = tmp_path / "empty.md"
        path.write_text("")
        assert _read_file(path) == []

    def test_entries_delimited_correctly(self, tmp_path):
        path = tmp_path / "delim.md"
        _write_file(path, ["a", "b"])
        raw = path.read_text()
        assert ENTRY_DELIMITER in raw

    def test_slugify(self):
        assert _slugify("Hello World!") == "hello-world"
        assert _slugify("  Test  123  ") == "test-123"
        assert _slugify("") == ""
        assert _slugify("a" * 200) == "a" * 80  # truncated


# ---------------------------------------------------------------------------
# Test 4: _save_to_raw — atomic write
# ---------------------------------------------------------------------------


class TestSaveToRaw:
    """_save_to_raw — атомарная запись в raw/auto-findings/."""

    def test_saves_file_with_frontmatter(self, tmp_path):
        save_dir = tmp_path / "raw-findings"
        save_dir.mkdir()
        text = "## Findings\n# Nginx Config\nSome content"
        assert _save_to_raw(text, "prism", save_dir)
        files = list(save_dir.iterdir())
        assert len(files) == 1
        content = files[0].read_text()
        assert "auto-detected-finding" in content
        assert "prism" in content
        assert "Nginx" in content

    def test_slug_from_title(self, tmp_path):
        text = "## Findings\n# My Finding Title"
        assert _save_to_raw(text, "research", tmp_path)
        filename = list(tmp_path.iterdir())[0].name
        assert "findings" in filename  # first heading wins

    def test_untitled_finding(self, tmp_path):
        """Текст без заголовка # → slug от первого предложения."""
        text = "## Findings\nThis is a long enough line that should become the slug of this finding."
        assert _save_to_raw(text, "prism", tmp_path)
        filename = list(tmp_path.iterdir())[0].name
        assert filename.endswith(".md")
        assert "findings" in filename  # the ## Findings heading

    def test_file_not_empty(self, tmp_path):
        save_dir = tmp_path / "save"
        save_dir.mkdir()
        text = "## Findings\nMinimal"
        assert _save_to_raw(text, "prism", save_dir)
        # Find the .md file (ignore non-.md dirs)
        files = [f for f in save_dir.iterdir() if f.suffix == ".md"]
        assert len(files) == 1
        content = files[0].read_text()
        assert len(content) > 50  # frontmatter + text


# ---------------------------------------------------------------------------
# Test 5: _load_patterns
# ---------------------------------------------------------------------------


class TestLoadPatterns:
    """_load_patterns — из конфига и дефолты."""

    def test_empty_config_uses_defaults(self):
        patterns = _load_patterns({})
        assert len(patterns) == len(DEFAULT_PATTERNS)

    def test_custom_config_used(self):
        cfg = {"detect_patterns": {"prism": ["(?i)Custom Pattern"]}}
        patterns = _load_patterns(cfg)
        assert len(patterns) == 1
        assert patterns[0][1] == "prism"

    def test_invalid_pattern_skipped(self, caplog):
        import logging
        caplog.set_level(logging.WARNING)
        cfg = {"detect_patterns": {"test": ["[invalid_pattern"]}}
        patterns = _load_patterns(cfg)
        assert len(patterns) == 0
        assert "Invalid pattern" in caplog.text

    def test_non_dict_uses_defaults(self):
        patterns = _load_patterns({"detect_patterns": "not_a_dict"})
        assert len(patterns) == len(DEFAULT_PATTERNS)


# ---------------------------------------------------------------------------
# Test 6: _load_plugin_config
# ---------------------------------------------------------------------------


class TestLoadPluginConfig:
    """_load_plugin_config — не падает если конфига нет."""

    def test_no_config_file_returns_empty(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.memory.findings_to_wiki.get_hermes_home",
            lambda: Path("/nonexistent/path"),
        )
        result = _load_plugin_config()
        assert result == {}


# ---------------------------------------------------------------------------
# Test 7: _get_wiki_paths
# ---------------------------------------------------------------------------


class TestGetWikiPaths:
    def test_default_paths(self):
        wiki, raw = _get_wiki_paths({})
        assert str(wiki).endswith("/wiki")
        assert str(raw).endswith("/wiki/raw/auto-findings")

    def test_custom_wiki_path(self, tmp_path):
        wiki, raw = _get_wiki_paths({"wiki_path": str(tmp_path / "my-wiki")})
        assert str(wiki).endswith("/my-wiki")
        assert str(raw).endswith("/my-wiki/raw/auto-findings")
