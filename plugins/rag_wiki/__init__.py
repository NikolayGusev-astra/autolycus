"""
RAG Wiki Search Plugin — pre_llm_call hook + tool.

Registers a pre_llm_call hook that:
1. Takes the user's message
2. Runs semantic search against ChromaDB wiki index
3. Injects relevant chunks as context into the user message

Also registers:
- rag_search tool: on-demand RAG search from agent
- rag_index tool: trigger incremental reindex

Zero overhead when ChromaDB collection is missing (skips gracefully).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

RAG_QUERY_SCRIPT = os.environ.get("RAG_QUERY_SCRIPT", "/root/rag-deploy/rag_query.py")
RAG_INDEX_SCRIPT = os.environ.get("RAG_INDEX_SCRIPT", "/root/rag-deploy/rag_indexer.py")
RAG_DEFAULT_K = int(os.environ.get("RAG_DEFAULT_K", "5"))
RAG_MAX_CONTEXT_CHARS = int(os.environ.get("RAG_MAX_CONTEXT_CHARS", "4000"))


def _run_rag_query(query: str, k: int = RAG_DEFAULT_K, agentic: bool = False) -> str:
    """Run RAG query script, return context string with noise filtering."""
    import json as _json

    cmd = [sys.executable, RAG_QUERY_SCRIPT, query, "--k", str(k), "--json"]
    if agentic:
        cmd.append("--agentic")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONPATH": "/root/rag-deploy"},
        )
        if result.returncode != 0:
            logger.warning("RAG query failed (rc=%d): %s", result.returncode, result.stderr[:200])
            return ""

        # Parse JSON output for filtering
        try:
            data = _json.loads(result.stdout)
            chunks = data.get("chunks", [])
        except (_json.JSONDecodeError, ValueError):
            # Fallback to plain text
            return result.stdout.strip()

        # Filter noise: exclude low-relevance and known noisy sources
        NOISE_PATTERNS = (
            "../wiki/queries/",
            "../wiki/raw/auto-findings/",
            "../wiki/session-notes/",
            "../wiki/raw/search_",
        )
        MIN_SCORE = 0.5

        filtered = []
        for c in chunks:
            source = c.get("source", "")
            score = c.get("score", 0)
            # Skip known noise directories
            if any(source.startswith(p) for p in NOISE_PATTERNS):
                continue
            # Skip low relevance
            if score < MIN_SCORE:
                continue
            filtered.append(c)

        if not filtered:
            return ""

        # Rebuild context from filtered chunks
        lines = ["## Relevant Wiki Documents", "=" * 40]
        for i, c in enumerate(filtered, 1):
            source = c.get("source", "unknown")
            heading = c.get("heading", "")
            score = c.get("score", 0)
            title_tag = f" ({c['title']})" if c.get("title") else ""
            lines.append(f"\n--- [{i}] {source}{title_tag} › {heading}  (relevance: {score:.2f})")
            lines.append(c.get("text", "")[:600])
        return "\n".join(lines)

    except subprocess.TimeoutExpired:
        logger.warning("RAG query timed out for: %s", query[:100])
        return ""
    except Exception as e:
        logger.warning("RAG query error: %s", e)
        return ""


# ── Hook ──────────────────────────────────────────────────────────────────────


def rag_pre_llm_call(
    user_message: str = "",
    conversation_history: Any = None,
    is_first_turn: bool = False,
    **kwargs: Any,
) -> Optional[Dict[str, str]]:
    """pre_llm_call hook: run RAG search, inject wiki context.

    Returns dict with 'context' key containing relevant wiki chunks.
    Returns None if no relevant context found or RAG unavailable.

    Context is injected into the user message (not system prompt)
    to preserve prompt cache prefix.
    """
    if not user_message or not user_message.strip():
        return None

    # Skip very short messages (greetings, confirmations)
    if len(user_message.strip()) < 10:
        return None

    # Skip messages that are clearly code/commands (not wiki queries)
    skip_prefixes = (
        "```", "#!/", "import ", "def ", "class ",
        "pip ", "npm ", "git ", "docker ", "ssh ",
        "cd ", "ls ", "cat ", "echo ", "mkdir ",
    )
    if user_message.strip().startswith(skip_prefixes):
        return None

    context = _run_rag_query(user_message.strip(), k=RAG_DEFAULT_K)

    if not context or context == "[No relevant wiki documents found]":
        return None

    # Truncate to avoid bloating context
    if len(context) > RAG_MAX_CONTEXT_CHARS:
        context = context[:RAG_MAX_CONTEXT_CHARS] + "\n... (truncated)"

    return {"context": context}


# ── Tool handlers ─────────────────────────────────────────────────────────────


def _handle_rag_search(
    query: str = "",
    k: int = RAG_DEFAULT_K,
    agentic: bool = False,
    **_: Any,
) -> str:
    """Handle rag_search tool call — on-demand RAG search."""
    if not query:
        return "ERROR: no query specified"

    context = _run_rag_query(query, k=k, agentic=agentic)

    if not context:
        return "No relevant wiki documents found."

    return context


def _handle_rag_index(
    incremental: bool = True,
    clear: bool = False,
    **_: Any,
) -> str:
    """Handle rag_index tool call — trigger reindex."""
    try:
        if clear:
            cmd = [sys.executable, RAG_INDEX_SCRIPT, "--clear"]
        elif incremental:
            cmd = [sys.executable, RAG_INDEX_SCRIPT, "--incremental"]
        else:
            cmd = [sys.executable, RAG_INDEX_SCRIPT]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            env={**os.environ, "PYTHONPATH": "/root/rag-deploy"},
        )

        if result.returncode == 0:
            # Get stats
            stats_result = subprocess.run(
                [sys.executable, RAG_INDEX_SCRIPT, "--stats"],
                capture_output=True, text=True, timeout=30,
                env={**os.environ, "PYTHONPATH": "/root/rag-deploy"},
            )
            return f"✅ Index updated.\n{stats_result.stdout.strip()}"
        else:
            return f"❌ Index failed (rc={result.returncode}):\n{result.stderr[:500]}"
    except subprocess.TimeoutExpired:
        return "❌ Index timed out (>300s)"
    except Exception as e:
        return f"❌ Index error: {e}"


# ── Plugin Registration ──────────────────────────────────────────────────────


def register(ctx: Any) -> None:
    """Register RAG Wiki hook and tools."""
    ctx.register_hook("pre_llm_call", rag_pre_llm_call)

    ctx.register_tool(
        name="rag_search",
        toolset="default",
        schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query for wiki RAG",
                },
                "k": {
                    "type": "integer",
                    "description": "Number of results (default: 5)",
                    "default": RAG_DEFAULT_K,
                },
                "agentic": {
                    "type": "boolean",
                    "description": "Use agentic multi-hop search (default: false)",
                    "default": False,
                },
            },
            "required": ["query"],
        },
        handler=_handle_rag_search,
        emoji="🔍",
    )

    ctx.register_tool(
        name="rag_index",
        toolset="default",
        schema={
            "type": "object",
            "properties": {
                "incremental": {
                    "type": "boolean",
                    "description": "Incremental index (default: true)",
                    "default": True,
                },
                "clear": {
                    "type": "boolean",
                    "description": "Clear and rebuild index (default: false)",
                    "default": False,
                },
            },
            "required": [],
        },
        handler=_handle_rag_index,
        emoji="📚",
    )

    logger.info("RAG Wiki plugin registered: pre_llm_call hook + 2 tools")
