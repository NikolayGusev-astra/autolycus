"""
plugins/rag_wiki — RAG Wiki Search Plugin (Zvec edition)

Автоматический семантический поиск по wiki через Zvec с инжекцией
контекста в каждый запрос агента.

Архитектура:
  pre_llm_call hook:
    1. Берёт сообщение пользователя
    2. Запускает FTS-поиск по Zvec коллекции вики
    3. Фильтрует шум (auto-findings, queries, session-notes)
    4. Возвращает context — релевантные чанки инжектятся в user message
       (не в system prompt — сохраняет prompt cache)

Инструменты:
  - rag_search — поиск по запросу (on-demand)
  - rag_index — переиндексация (incremental / full / clear)

Конфигурация (env vars):
  RAG_ZVEC_PATH       = /root/.cache/zvec/wiki
  RAG_WIKI_MIN_SCORE  = 0.0
  RAG_WIKI_TOP_K      = 8
  RAG_WIKI_MAX_CONTEXT_LEN = 1500
  WIKI_DIR            = /root/wiki
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger("autolycus.plugins.rag_wiki")

# Пути
PLUGIN_DIR = os.path.dirname(__file__)
SCRIPTS_DIR = os.path.join(PLUGIN_DIR, "scripts")

# Настройки
ZVEC_BASE = os.path.expanduser("~/.cache/zvec")
ZVEC_COLLECTION = os.environ.get("RAG_ZVEC_PATH", os.path.join(ZVEC_BASE, "wiki"))
MIN_SCORE = float(os.environ.get("RAG_WIKI_MIN_SCORE", "0.0"))
TOP_K = int(os.environ.get("RAG_WIKI_TOP_K", "8"))
MAX_CONTEXT_LEN = int(os.environ.get("RAG_WIKI_MAX_CONTEXT_LEN", "1500"))
WIKI_DIR = os.environ.get("WIKI_DIR", "/root/wiki")

# Шумовые паттерны (исключаемые директории)
NOISE_PATTERNS = [
    re.compile(r"queries/"),
    re.compile(r"raw/auto-findings/"),
    re.compile(r"raw/search_"),
    re.compile(r"session-notes/"),
    re.compile(r"\.git/"),
]


def _is_noisy(path: str) -> bool:
    """Проверяет является ли источник шумом."""
    return any(p.search(path) for p in NOISE_PATTERNS)


def _query_zvec(query: str, top_k: int = TOP_K) -> List[Dict]:
    """Выполняет FTS-поиск через Zvec."""
    query_script = os.path.join(SCRIPTS_DIR, "zvec_rag_query.py")
    if not os.path.isfile(query_script):
        logger.warning("RAG query script not found: %s", query_script)
        return []

    try:
        result = subprocess.run(
            ["python3", query_script, "-k", str(top_k), "-s", str(MIN_SCORE)],
            input=query, text=True,
            capture_output=True, timeout=15,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return data.get("results", [])
        else:
            logger.warning("RAG Query failed (rc=%d): %s",
                           result.returncode, result.stderr[:200])
            return []
    except Exception as e:
        logger.warning("RAG query error: %s", e)
        return []


def _grep_fallback(query: str, top_k: int = TOP_K) -> List[Dict]:
    """Аварийный поиск через grep по файлам вики."""
    words = re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", query)
    if not words:
        return []
    try:
        result = subprocess.run(
            ["grep", "-ril", "-m", "1"] + words[:5],
            capture_output=True, text=True, timeout=5,
            cwd=WIKI_DIR,
        )
        files = [f for f in result.stdout.split("\n") if f][:top_k]
        results = []
        for fp in files:
            title = os.path.basename(fp).replace(".md", "")
            relpath = os.path.relpath(fp, WIKI_DIR)
            try:
                with open(fp) as f:
                    content = f.read(MAX_CONTEXT_LEN)
            except Exception:
                content = ""
            results.append({
                "title": title,
                "path": relpath,
                "content": content,
                "score": 0,
                "source": "grep_fallback",
            })
        return results
    except Exception as e:
        logger.error("grep fallback error: %s", e)
        return []


def search(query: str, top_k: int = TOP_K) -> List[Dict]:
    """
    Поиск по вики через Zvec.

    Returns:
        [{"title", "path", "content", "score", "source", "tags", "category"}]
    """
    if not os.path.isdir(ZVEC_COLLECTION):
        logger.warning("Zvec collection not found: %s", ZVEC_COLLECTION)
        return _grep_fallback(query, top_k)

    results = _query_zvec(query, top_k)

    # Фильтруем шум
    filtered = [r for r in results if not _is_noisy(r.get("path", ""))]

    if not filtered and not results:
        # Fallback на grep если Zvec не дал результатов
        filtered = _grep_fallback(query, top_k)

    logger.info("RAG Wiki: %d results (filtered from %d) for '%s'",
                len(filtered), len(results), query[:60])
    return filtered


def format_results(results: List[Dict]) -> str:
    """Форматирование результатов в строку для инжекции в user message."""
    if not results:
        return ""

    lines = []
    for r in results:
        title = r.get("title", "")
        path = r.get("path", "")
        content = r.get("content", "")[:MAX_CONTEXT_LEN]
        score = r.get("score", 0)
        tags = r.get("tags", "")
        category = r.get("category", "")

        part = f"### {title}\nИсточник: {path}"
        if score:
            part += f" (score: {score:.2f})"
        if category:
            part += f"\nКатегория: {category}"
        if tags:
            part += f"\nТеги: {tags}"
        part += f"\n\n{content}"
        lines.append(part)

    return "\n\n---\n\n".join(lines[:TOP_K])


# -------------------------------------------------------------------
# Hook callbacks
# -------------------------------------------------------------------

def _on_pre_llm_call(user_message: str, messages: list, **kwargs: Any) -> Optional[Dict]:
    """
    pre_llm_call hook: инжектирует релевантный wiki контекст в user message.

    Возвращает:
        {"context": "..."} — будет добавлено к user message
        None — если ничего не найдено
    """
    if not user_message or not user_message.strip():
        return None

    results = search(user_message)
    if not results:
        return None

    context = format_results(results)
    if not context:
        return None

    logger.info("RAG Wiki: injecting %d chunks into user message", len(results))
    return {"context": context}


# -------------------------------------------------------------------
# Plugin registration
# -------------------------------------------------------------------

def register(ctx) -> None:
    """Register rag-wiki plugin hooks and tools."""
    try:
        ctx.register_hook("pre_llm_call", _on_pre_llm_call)
        logger.info("[rag-wiki] Registered: pre_llm_call hook (Zvec edition)")
    except Exception as e:
        logger.critical("[rag-wiki] Registration FAILED: %s", e)
