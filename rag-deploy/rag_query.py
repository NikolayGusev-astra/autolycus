#!/usr/bin/env python3
"""
RAG Query Helper — вызывается из агента или plugin hook.
Использование:
    python3 rag_query.py "поисковый запрос" [--agentic] [--k 5] [--no-web]
    python3 rag_query.py "поисковый запрос" --json      # полный JSON вывод

CRAG режим (--agentic):
    - classifies, retrieves, evaluates, corrects
    - web fallback при Incorrect evaluation (кроме --no-web)
    - decompose при Ambiguous evaluation
На выходе — контекст для инжекции в LLM.
"""
import sys
import os
import json

sys.path.insert(0, "/root/rag-deploy")
os.chdir("/root/rag-deploy")

from rag_search import RagSearch


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query", type=str, help="Поисковый запрос")
    parser.add_argument("--agentic", action="store_true", help="CRAG multi-hop search")
    parser.add_argument("--k", type=int, default=5, help="Количество результатов")
    parser.add_argument("--json", action="store_true", help="Output full JSON (for plugin hook)")
    parser.add_argument("--no-web", action="store_true", help="Отключить web fallback")
    parser.add_argument("--sessions", action="store_true", help="Поиск только по сессиям (sessions collection)")
    parser.add_argument("--no-sessions", action="store_true", help="Искать только по wiki, без сессий")
    args = parser.parse_args()

    rs = RagSearch()

    if args.sessions:
        # Поиск только по sessions collection
        chunks = rs.search_sessions(args.query, k=args.k)
        result = {
            "query_type": "sessions_only",
            "evaluation": "direct",
            "web_used": False,
            "decompose_used": False,
            "chunks": chunks,
            "context": rs.format_context(chunks),
            "iterations": 1,
            "cosine_threshold": 0.7,
        }
    elif args.agentic:
        result = rs.agentic_search(args.query, web_fallback=not args.no_web)
    else:
        chunks = rs.search(args.query, k=args.k, include_sessions=not args.no_sessions)
        result = {
            "query_type": "direct",
            "evaluation": "direct",
            "web_used": False,
            "decompose_used": False,
            "chunks": chunks,
            "context": rs.format_context(chunks),
            "iterations": 1,
            "cosine_threshold": 0.7,
        }

    if args.json:
        # Для plugin hook — полный JSON с метаданными CRAG
        print(json.dumps(result, ensure_ascii=False))
    else:
        # Для прямого вызова — форматированный контекст
        if args.agentic:
            eval_icon = {"correct": "✅", "ambiguous": "⚠️", "incorrect": "❌", "correct_web": "🌐"}.get(result["evaluation"], "❓")
            meta_parts = [
                f"[{result['query_type']}]",
                f"{eval_icon} {result['evaluation']}",
                f"{result['iterations']} iters",
            ]
            if result.get("web_used"):
                meta_parts.append("🌐 web")
            if result.get("decompose_used"):
                meta_parts.append("📋 decompose")
            print(" | ".join(meta_parts))
        print(result["context"])


if __name__ == "__main__":
    main()