#!/usr/bin/env python3
"""
RAG Wiki query script — поиск по Zvec коллекции.

Используется плагином rag_wiki для поиска релевантных чанков
в FTS/векторном индексе вики.

Выход: JSON-список результатов в stdout.
"""

import argparse
import json
import os
import sys
import time

# Путь к zvec
ZVEC_BASE = os.path.expanduser("~/.cache/zvec")
ZVEC_COLLECTION = os.environ.get("RAG_ZVEC_PATH", os.path.join(ZVEC_BASE, "wiki"))


def main():
    parser = argparse.ArgumentParser(description="RAG Wiki Zvec Query")
    parser.add_argument("-k", "--top-k", type=int, default=8,
                        help="Количество результатов (default: 8)")
    parser.add_argument("-s", "--min-score", type=float, default=0.0,
                        help="Минимальный score FTS (default: 0.0)")
    args = parser.parse_args()

    # Импортируем zvec
    import zvec

    if not os.path.isdir(ZVEC_COLLECTION):
        print(json.dumps({
            "error": f"collection path {ZVEC_COLLECTION} not exist.",
            "results": []
        }))
        sys.exit(1)

    # Читаем запрос из stdin
    query = sys.stdin.read().strip()
    if not query:
        print(json.dumps({"error": "empty query", "results": []}))
        sys.exit(1)

    t0 = time.time()

    # Открываем коллекцию
    col = zvec.open(ZVEC_COLLECTION)

    # FTS поиск через правильный API zvec
    from zvec import Query, Fts
    q = Query(
        field_name="content",
        fts=Fts(match_string=query)
    )

    try:
        results = col.query(queries=q, topk=args.top_k)
    except Exception as e:
        # Fallback: пробуем query_string если match_string не сработал
        try:
            q = Query(
                field_name="content",
                fts=Fts(query_string=query)
            )
            results = col.query(queries=q, topk=args.top_k)
        except Exception as e2:
            print(json.dumps({
                "error": f"query failed: {e2}",
                "results": []
            }))
            sys.exit(1)

    elapsed = time.time() - t0

    # Нормализуем выход
    output = []
    for doc in results:
        score = float(doc.score) if doc.score else 0.0
        if score < args.min_score:
            continue
        fields = doc.fields
        output.append({
            "title": fields.get("title", ""),
            "path": fields.get("source", fields.get("path", "")),
            "heading": fields.get("heading", ""),
            "content": fields.get("content", ""),
            "tags": fields.get("tags", ""),
            "category": fields.get("category", ""),
            "score": round(score, 4),
            "source": "zvec_fts",
        })

    response = {
        "results": output,
        "count": len(output),
        "elapsed_ms": round(elapsed * 1000, 1),
        "collection": ZVEC_COLLECTION,
        "query": query[:100],
    }

    print(json.dumps(response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
