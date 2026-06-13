#!/usr/bin/env python3
"""
CRAG (Corrective Retrieval-Augmented Generation) Search Handler.

Архитектура:
1. Classify query → factual / analytical / synthesis
2. Retrieve from ChromaDB
3. Evaluate quality via adaptive cosine threshold
   - "correct" → достаточно релевантных чанков → используем
   - "ambiguous" → частично релевантны → decompose/rewrite
   - "incorrect" → ничего не нашлось → web search fallback
4. Web fallback: DuckDuckGo search → структурированный контекст

Модели:
- e5-large-instruct: embedding
- gemma-4-e4b: query classification
- qwen2.5-7b-instruct: query rewriting + decomposition
- adaptive cosine thresholds: chunk grading (без LLM)
- DuckDuckGo: web search fallback (без API key)
"""
import argparse
import json
import logging
import os
import sys
import time
from urllib.parse import quote_plus

import requests
import chromadb
from chromadb.config import Settings
try:
    from ddgs import DDGS
except ImportError:
    from duckduckgo_search import DDGS

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rag_config import *


class EmbeddingClient:
    """Клиент для embedding API."""

    def embed(self, text: str, as_query: bool = True) -> list[float]:
        if as_query:
            text = f"Instruct: Given a wiki search query, retrieve relevant wiki passages\nQuery: {text[:MAX_CHARS_PER_INPUT]}"
        else:
            text = f"Passage: {text[:MAX_CHARS_PER_INPUT]}"

        resp = requests.post(EMBEDDING_URL, json={
            "model": EMBEDDING_MODEL,
            "input": [text]
        }, timeout=30)
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


class ClassifyClient:
    """Классификация запросов через Gemma 4B."""

    def classify(self, query: str) -> str:
        try:
            resp = requests.post(LLM_CLASSIFY_URL, json={
                "model": LLM_CLASSIFY_MODEL,
                "messages": [
                    {"role": "system", "content": "Classify the query into one word: factual, analytical, synthesis. Reply exactly one word."},
                    {"role": "user", "content": query}
                ],
                "temperature": 0.1,
                "max_tokens": 10,
            }, timeout=15)
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip().lower().rstrip('.!?,')
            if result not in ("factual", "analytical", "synthesis"):
                return "factual"
            return result
        except Exception as e:
            print(f"⚠ classify error: {e}", file=sys.stderr)
            return "factual"


class RewriteClient:
    """Query rewriting через Qwen 7B."""

    def rewrite(self, query: str, prev_context: str) -> str:
        try:
            resp = requests.post(LLM_REWRITE_URL, json={
                "model": LLM_REWRITE_MODEL,
                "messages": [
                    {"role": "system", "content": "You reformulate search queries to find better results. Reply with the new query only, no explanation."},
                    {"role": "user", "content": f"Original query: {query}\nPrevious results were not relevant enough. Context from previous search: {prev_context[:300]}\n\nRewrite the query to find better results:"}
                ],
                "temperature": 0.3,
                "max_tokens": 100,
            }, timeout=20)
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()
            return result if result else query
        except Exception as e:
            print(f"⚠ rewrite error: {e}", file=sys.stderr)
            return query


class DecomposeClient:
    """Query decomposition через Qwen 7B — разбивает сложный запрос на подзапросы."""

    def decompose(self, query: str) -> list[str]:
        """Разбивает запрос на подзапросы. Возвращает список или [query] при ошибке."""
        if not DECOMPOSE_ENABLED:
            return [query]
        try:
            resp = requests.post(LLM_DECOMPOSE_URL, json={
                "model": LLM_DECOMPOSE_MODEL,
                "messages": [
                    {"role": "system", "content": f"Break this query into up to {DECOMPOSE_MAX_SUBQUERIES} simpler sub-queries. Each on a new line. Reply with sub-queries only, no numbering or explanation."},
                    {"role": "user", "content": query}
                ],
                "temperature": 0.2,
                "max_tokens": 200,
            }, timeout=20)
            resp.raise_for_status()
            result = resp.json()["choices"][0]["message"]["content"].strip()
            subqueries = [q.strip().rstrip('.,?') for q in result.split('\n') if q.strip()]
            # Фильтр: убираем пустые и слишком похожие на оригинал
            subqueries = [q for q in subqueries if q.lower() != query.lower() and len(q) > 5]
            if subqueries:
                return subqueries[:DECOMPOSE_MAX_SUBQUERIES]
            return [query]
        except Exception as e:
            print(f"⚠ decompose error: {e}", file=sys.stderr)
            return [query]


class WebSearchClient:
    """Web search fallback через DuckDuckGo (без API key)."""

    def search(self, query: str, max_results: int = WEB_SEARCH_MAX_RESULTS) -> list[dict]:
        """Поиск в вебе, возвращает список чанков в том же формате что RAG search."""
        if not WEB_SEARCH_ENABLED:
            return []
        try:
            results = []
            with DDGS() as ddgs:
                for i, r in enumerate(ddgs.text(query, max_results=max_results)):
                    if i >= max_results:
                        break
                    text = r.get("body", "") or r.get("snippet", "")
                    title = r.get("title", "")
                    href = r.get("href", "")
                    results.append({
                        "text": text[:WEB_SEARCH_MAX_CHARS],
                        "source": f"web/{href}",
                        "heading": title or "Web Result",
                        "title": title,
                        "type": "web",
                        "tags": "web,external",
                        "score": round(0.9 - (i * 0.05), 4),  # убывающая релевантность
                        "cosine_score": 0.9,
                        "boost": 0.0,
                        "from_web": True,
                    })
            return results
        except Exception as e:
            print(f"⚠ web search error: {e}", file=sys.stderr)
            return []

    def search_with_retry(self, query: str, max_attempts: int = 2) -> list[dict]:
        """Поиск с ретраем при ошибке."""
        for attempt in range(max_attempts):
            results = self.search(query)
            if results:
                return results
            time.sleep(1)
        return []


class RagSearch:
    """Основной класс для CRAG поиска (Corrective RAG)."""

    def __init__(self):
        self.emb = EmbeddingClient()
        self.classifier = ClassifyClient()
        self.rewriter = RewriteClient()
        self.decomposer = DecomposeClient()
        self.web = WebSearchClient()
        self.client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            self.collection = self.client.get_collection(COLLECTION_NAME)
        except Exception:
            print(f"⚠ Коллекция '{COLLECTION_NAME}' не найдена. Запусти rag_indexer.py сначала.", file=sys.stderr)
            self.collection = None
        # Sessions collection (опционально)
        try:
            self.sess_collection = self.client.get_collection(SESSION_COLLECTION_NAME)
        except Exception:
            self.sess_collection = None

    def _get_threshold(self, query_type: str) -> float:
        """Возвращает adaptive cosine threshold для данного типа запроса."""
        return COSINE_THRESHOLDS.get(query_type, COSINE_THRESHOLDS["default"])

    def search(self, query: str, k: int = DEFAULT_K, cosine_threshold: float = None, include_sessions: bool = True) -> list[dict]:
        """Базовый поиск: embed → Chroma → top-k с cosine threshold + source boost.

        Если include_sessions=True и sessions collection доступна — ищет по wiki + sessions,
        объединяет результаты и помечает from_session.
        """
        if not self.collection or not query.strip():
            return []

        if cosine_threshold is None:
            cosine_threshold = COSINE_THRESHOLDS["default"]

        fetch_k = min(k * 3, 30)
        try:
            query_emb = self.emb.embed(query.strip(), as_query=True)
        except Exception as e:
            logger.warning("Embedding error: %s", e)
            return []

        chunks = self._query_collection(self.collection, query_emb, fetch_k, cosine_threshold, from_session=False)

        # Поиск по sessions collection
        if include_sessions and self.sess_collection is not None:
            try:
                # Для sessions используем session-специфичный префикс
                sess_query = f"{SESSION_EMBED_PREFIX} {query.strip()[:MAX_CHARS_PER_INPUT]}"
                sess_emb = self.emb.embed(sess_query, as_query=False)
                # Пере-embed с правильным префиксом
                resp = requests.post(EMBEDDING_URL, json={
                    "model": EMBEDDING_MODEL,
                    "input": [sess_query]
                }, timeout=30)
                resp.raise_for_status()
                sess_emb = resp.json()["data"][0]["embedding"]
                sess_chunks = self._query_collection(self.sess_collection, sess_emb, fetch_k, cosine_threshold, from_session=True)
                chunks.extend(sess_chunks)
            except Exception as e:
                logger.warning("Session search error: %s", e)

        chunks.sort(key=lambda c: c["score"], reverse=True)
        return chunks[:k]

    def search_sessions(self, query: str, k: int = DEFAULT_K, cosine_threshold: float = None) -> list[dict]:
        """Поиск только по sessions collection."""
        if not self.sess_collection or not query.strip():
            return []

        if cosine_threshold is None:
            cosine_threshold = COSINE_THRESHOLDS["default"]

        fetch_k = min(k * 3, 30)
        try:
            sess_query = f"{SESSION_EMBED_PREFIX} {query.strip()[:MAX_CHARS_PER_INPUT]}"
            resp = requests.post(EMBEDDING_URL, json={
                "model": EMBEDDING_MODEL,
                "input": [sess_query]
            }, timeout=30)
            resp.raise_for_status()
            sess_emb = resp.json()["data"][0]["embedding"]
        except Exception as e:
            logger.warning("Session embedding error: %s", e)
            return []

        return self._query_collection(self.sess_collection, sess_emb, fetch_k, cosine_threshold, from_session=True)[:k]

    def _query_collection(self, collection, query_emb: list[float], fetch_k: int, cosine_threshold: float, from_session: bool = False) -> list[dict]:
        """Внутренний метод: query одной коллекции → список chunks."""
        results = collection.query(
            query_embeddings=[query_emb],
            n_results=fetch_k,
            include=["documents", "metadatas", "distances"],
        )

        chunks = []
        if results.get("documents") and results["documents"][0]:
            for i, doc in enumerate(results["documents"][0]):
                md = results["metadatas"][0][i] if results.get("metadatas") else {}
                distance = results["distances"][0][i] if results.get("distances") else 0
                cosine_score = 1 - distance

                if cosine_score < cosine_threshold:
                    continue

                source = md.get("source", "unknown")
                boost = 0.0
                for prefix, b in SOURCE_BOOST.items():
                    if prefix in source:
                        boost = b
                        break

                chunks.append({
                    "text": doc,
                    "source": source,
                    "heading": md.get("heading", ""),
                    "title": md.get("title", ""),
                    "type": md.get("type", ""),
                    "tags": md.get("tags", ""),
                    "score": round(cosine_score + boost, 4),
                    "cosine_score": round(cosine_score, 4),
                    "boost": boost,
                    "from_web": False,
                    "from_session": from_session,
                })

        return chunks

    def evaluate(self, chunks: list[dict], query_type: str) -> str:
        """
        CRAG-style evaluation of retrieval quality.
        
        Uses two-stage approach:
        1. Cosine threshold check (adaptive per query_type)
        2. Score distribution confidence check (detects e5-large noise)
        
        Returns:
            "correct"    → достаточно релевантных чанков, используем
            "ambiguous"  → частично релевантны, нужен rewrite/decompose
            "incorrect"  → ничего релевантного, нужен web fallback
        """
        if not chunks:
            return "incorrect"

        threshold = self._get_threshold(query_type)
        relevant = [c for c in chunks if c["cosine_score"] >= threshold]
        total = len(chunks)
        relevant_count = len(relevant)
        relevant_ratio = relevant_count / total if total > 0 else 0

        # Stage 1: Threshold check
        if relevant_count < MIN_RELEVANT_COUNT or relevant_ratio < MIN_RELEVANT_RATIO:
            return "incorrect"

        # Stage 2: Score distribution confidence check
        # e5-large often compresses scores into a narrow 0.7-0.85 range
        # If all chunks cluster tightly without a clear winner → likely noise
        scores = [c["cosine_score"] for c in chunks]
        max_score = max(scores)
        min_score = min(scores)
        score_gap = max_score - min_score
        avg_score = sum(scores) / len(scores)

        # Проверяем на "шум": узкий диапазон + нет явного лидера
        is_noise_cluster = (
            score_gap < 0.04           # все баллы в диапазоне < 0.04
            and max_score < 0.85        # ни один не дотягивает до "хорошего"
            and avg_score < 0.80        # средний тоже низкий
        )

        if is_noise_cluster:
            return "incorrect"

        # Также: если max_score низкий даже для одного — это плохо
        if max_score < threshold + 0.05:
            # Максимальный балл едва перешагнул threshold — слабая релевантность
            if relevant_ratio < AMBIGUOUS_RATIO:
                return "ambiguous"

        # Stage 3: Decision
        if relevant_ratio >= AMBIGUOUS_RATIO:
            return "correct"
        else:
            return "ambiguous"

    def format_context(self, chunks: list[dict]) -> str:
        """Форматирует чанки для подачи в контекст LLM."""
        if not chunks:
            return "[No relevant documents found]"

        lines = ["## Relevant Documents", "=" * 40]
        for i, c in enumerate(chunks, 1):
            source = c["source"]
            heading = c["heading"]
            score = c["score"]
            title_tag = f" ({c['title']})" if c.get("title") else ""
            web_tag = " 🌐" if c.get("from_web") else ""
            sess_tag = " 💬" if c.get("from_session") else ""
            lines.append(f"\n--- [{i}] {source}{title_tag} › {heading}  (relevance: {score:.2f}){web_tag}{sess_tag}")
            lines.append(c["text"][:600])
        return "\n".join(lines)

    def search_web(self, query: str, k: int = DEFAULT_K) -> list[dict]:
        """Web search fallback. Возвращает чанки в том же формате."""
        web_results = self.web.search_with_retry(query)
        if web_results:
            return web_results[:k]

        # Если DuckDuckGo не сработал — пробуем прямой HTTP
        try:
            url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            headers = {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
            }
            resp = requests.get(url, headers=headers, timeout=WEB_SEARCH_TIMEOUT)
            resp.raise_for_status()
            # Парсинг результатов из HTML — упрощённый
            import re
            snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.DOTALL)
            results = []
            for i, s in enumerate(snippets[:k]):
                text = re.sub(r'<[^>]+>', '', s).strip()
                if text:
                    results.append({
                        "text": text[:WEB_SEARCH_MAX_CHARS],
                        "source": f"web/duckduckgo",
                        "heading": f"Web Result {i+1}",
                        "title": "",
                        "type": "web",
                        "tags": "web,external",
                        "score": round(0.9 - (i * 0.05), 4),
                        "cosine_score": 0.9,
                        "boost": 0.0,
                        "from_web": True,
                    })
            return results
        except Exception as e:
            print(f"⚠ web search HTTP fallback error: {e}", file=sys.stderr)
            return []

    def agentic_search(self, query: str, max_iterations: int = 3, web_fallback: bool = True) -> dict:
        """
        CRAG agentic search: classify → retrieve → evaluate → correct.
        
        CRAG flow:
        1. Classify query type (factual/analytical/synthesis)
        2. Retrieve with adaptive cosine threshold
        3. Evaluate: correct / ambiguous / incorrect
           - "correct" → используем найденные чанки
           - "ambiguous" → decompose (разбить запрос) + merge результатов
           - "incorrect" → web search fallback
        4. Optional: rewrite loop для analytical/synthesis
        """
        query_type = self.classifier.classify(query)
        threshold = self._get_threshold(query_type)
        
        iteration_count = 0
        all_chunks = []
        web_used = False
        decompose_used = False
        evaluation = "unknown"

        # ── Шаг 1: Первичный retrieval ─────────────────────────────────
        chunks = self.search(query, k=DEFAULT_K, cosine_threshold=threshold)
        iteration_count = 1

        # ── Шаг 2: Evaluate ────────────────────────────────────────────
        evaluation = self.evaluate(chunks, query_type)
        
        if evaluation == "correct":
            # ✅ Достаточно релевантных чанков
            all_chunks = chunks

        elif evaluation == "ambiguous":
            # ⚠️ Частично релевантны → decompose
            if query_type in ("analytical", "synthesis"):
                subqueries = self.decomposer.decompose(query)
                decompose_used = True
                
                for sq in subqueries:
                    sub_chunks = self.search(sq, k=DEFAULT_K // 2, cosine_threshold=threshold - 0.05)
                    all_chunks.extend(sub_chunks)
                
                # Добавляем оригинальные (лучшие из них)
                all_chunks.extend(chunks[:max(1, len(chunks)//2)])
                iteration_count += len(subqueries)
            else:
                # Для factual: rewrite вместо decompose
                prev_context = "\n".join([c["text"][:200] for c in chunks[:2]]) if chunks else ""
                new_query = self.rewriter.rewrite(query, prev_context)
                if new_query != query:
                    refined = self.search(new_query, k=DEFAULT_K, cosine_threshold=threshold)
                    all_chunks.extend(refined)
                    iteration_count += 1
                all_chunks.extend(chunks)

        elif evaluation == "incorrect":
            # ❌ Ничего не нашли → web fallback (CRAG core)
            if web_fallback and WEB_SEARCH_ENABLED:
                web_results = self.search_web(query, k=DEFAULT_K)
                if web_results:
                    all_chunks.extend(web_results)
                    web_used = True
                    evaluation = "correct_web"
                else:
                    all_chunks = chunks  # отдаём что есть
            else:
                all_chunks = chunks

        # ── Шаг 3: Для analytical/synthesis — rewrite loop ─────────────
        # (только если не было decompose — иначе уже достаточно)
        if query_type in ("analytical", "synthesis") and not decompose_used and evaluation != "incorrect":
            current_query = query
            for i in range(max_iterations - iteration_count):
                if len(all_chunks) >= DEFAULT_K:
                    break
                
                prev_context = "\n".join([c["text"][:200] for c in all_chunks[:2]]) if all_chunks else ""
                new_query = self.rewriter.rewrite(current_query, prev_context)
                if new_query == current_query:
                    break
                current_query = new_query
                
                new_chunks = self.search(current_query, k=DEFAULT_K, cosine_threshold=threshold)
                if new_chunks:
                    # Проверяем дубликаты перед добавлением
                    seen_sources = {(c["source"], c["heading"]) for c in all_chunks}
                    fresh = [c for c in new_chunks if (c["source"], c["heading"]) not in seen_sources]
                    all_chunks.extend(fresh)
                    iteration_count += 1

        # ── Финальный dedup ────────────────────────────────────────────
        seen = set()
        deduped = []
        for c in all_chunks:
            key = (c["source"], c["heading"])
            if key not in seen:
                seen.add(key)
                deduped.append(c)
        deduped.sort(key=lambda x: x["score"], reverse=True)
        deduped = deduped[:DEFAULT_K * 2]

        return {
            "query_type": query_type,
            "evaluation": evaluation,
            "web_used": web_used,
            "decompose_used": decompose_used,
            "chunks": deduped,
            "context": self.format_context(deduped),
            "iterations": iteration_count,
            "cosine_threshold": threshold,
        }


def main():
    parser = argparse.ArgumentParser(description="CRAG Search для wiki")
    parser.add_argument("--query", "-q", type=str, help="Поисковый запрос")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--agentic", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-web", action="store_true", help="Отключить web fallback")
    args = parser.parse_args()

    rs = RagSearch()

    if not args.query:
        print(f"🔍 CRAG Search (Ctrl+C для выход)")
        print(f"   Collection: '{COLLECTION_NAME}' | Embedding: {EMBEDDING_MODEL}")
        print(f"   Classify: {LLM_CLASSIFY_MODEL} | Rewrite: {LLM_REWRITE_MODEL}")
        print(f"   Decompose: {LLM_DECOMPOSE_MODEL}")
        print(f"   Thresholds: factual={COSINE_THRESHOLDS['factual']} analytical={COSINE_THRESHOLDS['analytical']} synthesis={COSINE_THRESHOLDS['synthesis']}")
        print(f"   Web fallback: {'✓ enabled' if WEB_SEARCH_ENABLED else '✗ disabled'}")
        print()
        try:
            while True:
                q = input("query> ").strip()
                if not q:
                    continue
                if args.agentic:
                    result = rs.agentic_search(q, web_fallback=not args.no_web)
                    eval_icon = {"correct": "✅", "ambiguous": "⚠️", "incorrect": "❌", "correct_web": "🌐"}.get(result["evaluation"], "❓")
                    meta_parts = [f"[{result['query_type']}]", f"{eval_icon} {result['evaluation']}", f"{result['iterations']} iters"]
                    if result["web_used"]:
                        meta_parts.append("🌐 web")
                    if result["decompose_used"]:
                        meta_parts.append("📋 decompose")
                    print(f"\n{' | '.join(meta_parts)}")
                    print(result["context"])
                else:
                    chunks = rs.search(q, k=args.k)
                    print(rs.format_context(chunks))
                print()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
        return

    if args.agentic:
        result = rs.agentic_search(args.query, web_fallback=not args.no_web)
    else:
        chunks = rs.search(args.query, k=args.k)
        result = {
            "query_type": "direct",
            "evaluation": "direct",
            "web_used": False,
            "decompose_used": False,
            "chunks": chunks,
            "context": rs.format_context(chunks),
            "iterations": 1,
            "cosine_threshold": COSINE_THRESHOLDS["default"],
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        eval_icon = {"correct": "✅", "ambiguous": "⚠️", "incorrect": "❌", "correct_web": "🌐"}.get(result["evaluation"], "❓")
        meta_parts = [f"[{result['query_type']}]", f"{eval_icon} {result['evaluation']}", f"{result['iterations']} iterations"]
        if result["web_used"]:
            meta_parts.append("🌐 web")
        if result["decompose_used"]:
            meta_parts.append("📋 decompose")
        print(f"\n{' | '.join(meta_parts)}")
        print(result["context"])


if __name__ == "__main__":
    main()