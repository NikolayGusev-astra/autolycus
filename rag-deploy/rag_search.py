#!/usr/bin/env python3
"""
RAG Search Handler — поиск по wiki через ChromaDB с agentic циклом.

Архитектура:
1. Embed query → ChromaDB → top-k чанков
2. Cosine threshold filtering (вместо LLM grading)
3. Query rewriting через Qwen 7B (если мало релевантных)
4. Source-type boosting (мягкий, без clamp)

Модели:
- e5-large-instruct: embedding
- gemma-4-e4b: query classification
- qwen2.5-7b-instruct: query rewriting
- cosine threshold: chunk grading (без LLM)
"""
import argparse
import json
import logging
import os
import sys

import requests
import chromadb
from chromadb.config import Settings

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


class RagSearch:
    """Основной класс для RAG поиска."""

    def __init__(self):
        self.emb = EmbeddingClient()
        self.classifier = ClassifyClient()
        self.rewriter = RewriteClient()
        self.client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False),
        )
        try:
            self.collection = self.client.get_collection(COLLECTION_NAME)
        except Exception:
            print(f"⚠ Коллекция '{COLLECTION_NAME}' не найдена. Запусти rag_indexer.py сначала.", file=sys.stderr)
            self.collection = None

    def search(self, query: str, k: int = DEFAULT_K, cosine_threshold: float = COSINE_THRESHOLD) -> list[dict]:
        """Базовый поиск: embed → Chroma → top-k с cosine threshold + source boost."""
        if not self.collection or not query.strip():
            return []

        fetch_k = min(k * 3, 30)
        try:
            query_emb = self.emb.embed(query.strip(), as_query=True)
        except Exception as e:
            logger.warning("Embedding error: %s", e)
            return []
        results = self.collection.query(
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

                # Cosine threshold — убираем нерелевантные чанки
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
                })

        chunks.sort(key=lambda c: c["score"], reverse=True)
        return chunks[:k]

    def format_context(self, chunks: list[dict]) -> str:
        """Форматирует чанки для подачи в контекст LLM."""
        if not chunks:
            return "[No relevant wiki documents found]"

        lines = ["## Relevant Wiki Documents", "=" * 40]
        for i, c in enumerate(chunks, 1):
            source = c["source"]
            heading = c["heading"]
            score = c["score"]
            title_tag = f" ({c['title']})" if c.get("title") else ""
            lines.append(f"\n--- [{i}] {source}{title_tag} › {heading}  (relevance: {score:.2f})")
            lines.append(c["text"][:600])
        return "\n".join(lines)

    def agentic_search(self, query: str, max_iterations: int = 3) -> dict:
        """
        Agentic RAG: classify → retrieve → cosine filter → rewrite if needed.
        Без LLM grading — используем cosine threshold.
        """
        query_type = self.classifier.classify(query)

        all_chunks = []
        current_query = query

        if query_type in ("analytical", "synthesis"):
            for i in range(max_iterations):
                chunks = self.search(current_query, k=DEFAULT_K)

                if chunks:
                    all_chunks.extend(chunks)
                    # Достаточно релевантных — останавливаемся
                    if len(chunks) >= 3:
                        break
                # Мало результатов → переформулируем
                prev_context = "\n".join([c["text"][:200] for c in chunks[:2]]) if chunks else ""
                new_query = self.rewriter.rewrite(query, prev_context)
                if new_query == current_query:
                    break
                current_query = new_query
        else:
            all_chunks = self.search(query, k=DEFAULT_K)

        # Dedup by source+heading
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
            "chunks": deduped,
            "context": self.format_context(deduped),
            "iterations": max_iterations,
        }


def main():
    parser = argparse.ArgumentParser(description="RAG Search для wiki")
    parser.add_argument("--query", "-q", type=str, help="Поисковый запрос")
    parser.add_argument("--k", type=int, default=DEFAULT_K)
    parser.add_argument("--agentic", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    rs = RagSearch()

    if not args.query:
        print(f"🔍 RAG Search (Ctrl+C для выход)")
        print(f"   Collection: '{COLLECTION_NAME}' | Embedding: {EMBEDDING_MODEL}")
        print(f"   Classify: {LLM_CLASSIFY_MODEL} | Rewrite: {LLM_REWRITE_MODEL}")
        print(f"   Cosine threshold: {COSINE_THRESHOLD}")
        print()
        try:
            while True:
                q = input("query> ").strip()
                if not q:
                    continue
                if args.agentic:
                    result = rs.agentic_search(q)
                    print(f"\n[{result['query_type']}] ({result['iterations']} iters)")
                    print(result["context"])
                else:
                    chunks = rs.search(q, k=args.k)
                    print(rs.format_context(chunks))
                print()
        except (KeyboardInterrupt, EOFError):
            print("\nBye.")
        return

    if args.agentic:
        result = rs.agentic_search(args.query)
    else:
        chunks = rs.search(args.query, k=args.k)
        result = {
            "query_type": "direct",
            "chunks": chunks,
            "context": rs.format_context(chunks),
            "iterations": 1,
        }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n[{result['query_type']}] ({result['iterations']} iterations)")
        print(result["context"])


if __name__ == "__main__":
    main()
