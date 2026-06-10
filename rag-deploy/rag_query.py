#!/usr/bin/env python3
"""
RAG Query Helper — вызывается через execute_code из агента.
Использование:
    python3 rag_query.py "поисковый запрос" [--agentic] [--k 5]
Выводит контекст для инжекции в LLM.
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
    parser.add_argument("--agentic", action="store_true", help="Agentic multi-hop search")
    parser.add_argument("--k", type=int, default=5, help="Количество результатов")
    parser.add_argument("--json", action="store_true", help="Output full JSON (for plugin hook)")
    args = parser.parse_args()

    rs = RagSearch()

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
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(result["context"])

if __name__ == "__main__":
    main()
