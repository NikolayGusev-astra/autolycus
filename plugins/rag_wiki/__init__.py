"""
plugins/rag_wiki — RAG Wiki Search Plugin (CRAG + Federated edition)

Полный CRAG pipeline (classify → retrieve → evaluate → correct → rerank)
с федеративным поиском (ChromaDB + remote nodes через SSH) и fallback на Zvec FTS.

Архитектура:
  pre_llm_call hook:
    1. Берёт сообщение пользователя
    2. Запускает agentic_search() через RagSearch (полный CRAG):
       - Classify: factual/analytical/synthesis (Gemma 4B + TF-IDF)
       - Retrieve: federated_search (ChromaDB wiki+sessions + SSH remote nodes)
       - Evaluate: cosine threshold + noise detection
       - Correct: rewrite/decompose (Qwen 7B) или web fallback (DuckDuckGo)
       - Rerank: LM Studio reranker
    3. Fallback на Zvec FTS если RagSearch недоступен
    4. Фильтрует шум (auto-findings, queries, session-notes)
    5. Возвращает context — релевантные чанки инжектятся в user message

Инструменты:
  - rag_search — поиск по запросу (on-demand, полный CRAG)
  - rag_index — переиндексация (incremental / full / clear)

Конфигурация (env vars):
  RAG_ZVEC_PATH       = /root/.cache/zvec/wiki
  RAG_WIKI_MIN_SCORE  = 0.0
  RAG_WIKI_TOP_K      = 8
  RAG_WIKI_MAX_CONTEXT_LEN = 1500
  WIKI_DIR            = /root/wiki
  RAG_DEPLOY_DIR      = /root/rag-deploy
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
RAG_DEPLOY_DIR = os.environ.get("RAG_DEPLOY_DIR", "/root/rag-deploy")

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


def _to_plugin_format(chunks: List[Dict]) -> List[Dict]:
    """Конвертирует RagSearch chunks в формат плагина."""
    results = []
    for chunk in chunks:
        text = chunk.get("text", chunk.get("content", ""))
        source = chunk.get("source", "")
        heading = chunk.get("heading", "")
        title = chunk.get("title", heading)
        score = chunk.get("score", 0)
        tags = chunk.get("tags", "")
        category = chunk.get("category", "")
        node = chunk.get("node", "local")
        results.append({
            "title": title,
            "path": source,
            "heading": heading,
            "content": text[:MAX_CONTEXT_LEN],
            "score": round(float(score), 4),
            "source": f"federated/{node}",
            "tags": tags,
            "category": category,
            "node": node,
        })
    return results


def _query_crag(query: str, top_k: int = TOP_K) -> List[Dict]:
    """
    Полный CRAG pipeline через RagSearch.agentic_search().

    Classify → Retrieve (federated) → Evaluate → Correct (rewrite/decompose/web) → Rerank

    Returns:
        Список чанков в формате плагина
    """
    if RAG_DEPLOY_DIR not in sys.path:
        sys.path.insert(0, RAG_DEPLOY_DIR)

    try:
        from rag_search import RagSearch
        rs = RagSearch(local_only=False)
        result = rs.agentic_search(query, web_fallback=True)

        chunks = result.get("chunks", [])
        evaluation = result.get("evaluation", "unknown")
        query_type = result.get("query_type", "unknown")
        iterations = result.get("iterations", 1)
        web_used = result.get("web_used", False)
        decompose_used = result.get("decompose_used", False)
        nodes_searched = getattr(rs, "last_nodes_searched", ["local"])

        logger.info(
            "CRAG: eval=%s type=%s iters=%d web=%s decompose=%s nodes=%s chunks=%d for '%s'",
            evaluation, query_type, iterations, web_used, decompose_used,
            nodes_searched, len(chunks), query[:60]
        )

        return _to_plugin_format(chunks)

    except Exception as e:
        logger.warning("CRAG search failed: %s, falling back to Zvec", e)
        return []


def search(query: str, top_k: int = TOP_K) -> List[Dict]:
    """
    Поиск по вики: CRAG (полный pipeline) → Zvec FTS → grep fallback.

    Приоритет:
    1. CRAG (RagSearch.agentic_search) — classify + federated retrieve + evaluate + correct + rerank
    2. Zvec FTS — локальный полнотекстовый поиск
    3. grep fallback — аварийный поиск по файлам

    Returns:
        [{"title", "path", "content", "score", "source", "tags", "category", "node"}]
    """
    all_results: List[Dict] = []
    seen_paths: set = set()

    # 1. CRAG pipeline (classify → retrieve → evaluate → correct → rerank)
    crag_results = _query_crag(query, top_k)
    for r in crag_results:
        path = r.get("path", "")
        if path and path not in seen_paths:
            all_results.append(r)
            seen_paths.add(path)
        elif not path:
            all_results.append(r)

    # 2. Zvec FTS (дополнительные результаты если CRAG мало)
    if len(all_results) < top_k and os.path.isdir(ZVEC_COLLECTION):
        zvec_results = _query_zvec(query, top_k)
        for r in zvec_results:
            path = r.get("path", "")
            if path and path not in seen_paths:
                all_results.append(r)
                seen_paths.add(path)
            elif not path:
                all_results.append(r)

    # 3. Фильтруем шум
    filtered = [r for r in all_results if not _is_noisy(r.get("path", ""))]

    # 4. Grep fallback если вообще ничего
    if not filtered:
        filtered = _grep_fallback(query, top_k)

    # Сортируем по score и обрезаем
    filtered.sort(key=lambda r: float(r.get("score", 0)), reverse=True)

    logger.info("RAG Wiki: %d total results for '%s'", len(filtered[:top_k]), query[:60])
    return filtered[:top_k]


# -------------------------------------------------------------------
# Tool functions (on-demand search + index)
# -------------------------------------------------------------------

def rag_search(query: str, top_k: int = TOP_K) -> str:
    """
    Поиск по вики (on-demand tool). Полный CRAG pipeline.

    Args:
        query: поисковый запрос
        top_k: количество результатов

    Returns:
        Форматированный контекст для инжекции в LLM
    """
    results = search(query, top_k)
    return format_results(results)


def rag_index(mode: str = "incremental") -> str:
    """
    Переиндексация вики (on-demand tool).

    Args:
        mode: "incremental", "full", "clear"

    Returns:
        Статус операции
    """
    if mode == "clear":
        zvec_script = os.path.join(SCRIPTS_DIR, "zvec_clear.py")
        if os.path.isfile(zvec_script):
            result = subprocess.run(
                ["python3", zvec_script],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                return f"Zvec clear failed: {result.stderr[:200]}"
        mode = "full"

    indexer_script = os.path.join(RAG_DEPLOY_DIR, "rag_indexer.py")
    if os.path.isfile(indexer_script):
        cmd = ["python3", indexer_script]
        if mode == "full":
            cmd.append("--full")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return f"RAG index ({mode}) completed: {result.stdout[:200]}"
        return f"RAG index failed: {result.stderr[:200]}"

    zvec_indexer = os.path.join(RAG_DEPLOY_DIR, "zvec_indexer.py")
    if os.path.isfile(zvec_indexer):
        cmd = ["python3", zvec_indexer]
        if mode == "full":
            cmd.append("--full")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            return f"Zvec index ({mode}) completed: {result.stdout[:200]}"
        return f"Zvec index failed: {result.stderr[:200]}"

    return "No indexer script found. Set RAG_DEPLOY_DIR env var."


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
        node = r.get("node", "")

        part = f"### {title}\nИсточник: {path}"
        if score:
            part += f" (score: {score:.2f})"
        if node:
            part += f" [node: {node}]"
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
    Использует полный CRAG pipeline.
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
        ctx.register_tool(rag_search, override=True)
        ctx.register_tool(rag_index, override=True)
        logger.info("[rag-wiki] Registered: pre_llm_call hook + rag_search/rag_index tools (CRAG+Federated)")
    except Exception as e:
        logger.critical("[rag-wiki] Registration FAILED: %s", e)
