#!/usr/bin/env python3
"""
SearXNG cached search wrapper with engine prioritization.

Usage:
    python3 searxng_search.py "Ford Explorer GEM module"
    python3 searxng_search.py "query" --engines google,brave,mojeek --limit 10
    python3 searxng_search.py "query" --category general --lang ru
    python3 searxng_search.py "query" --no-cache

Engine priority (default): google, brave, mojeek, duckduckgo, bing, startpage, qwant
Categories: general, news, science, it, files, images, videos, social media
Cache: ~/.cache/searxng/ (JSON, TTL 1 hour)
"""

import urllib.request, urllib.parse, json, argparse, sys, os, hashlib, time
from pathlib import Path

SEARXNG_URL = "http://127.0.0.1:8080"
CACHE_DIR = Path.home() / ".cache" / "searxng"
CACHE_TTL = 3600  # 1 hour

DEFAULT_PRIORITY = ["google", "brave", "mojeek", "duckduckgo", "bing", "startpage", "qwant"]

def cache_key(query: str, engines: list, category: str, lang: str) -> str:
    raw = f"{query}:{','.join(engines)}:{category}:{lang}"
    return hashlib.md5(raw.encode()).hexdigest()

def get_cached(key: str) -> dict | None:
    path = CACHE_DIR / f"{key}.json"
    if path.exists():
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < CACHE_TTL:
            return data["results"]
    return None

def set_cache(key: str, results: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{key}.json"
    path.write_text(json.dumps({"ts": time.time(), "results": results}, ensure_ascii=False))

def search(query: str, engines: list = None, category: str = "general",
           lang: str = "", limit: int = 10, no_cache: bool = False) -> dict:
    """Search via SearXNG with caching and engine prioritization."""
    engines = engines or DEFAULT_PRIORITY
    lang = lang or "all"

    key = cache_key(query, engines, category, lang)
    if not no_cache:
        cached = get_cached(key)
        if cached is not None:
            cached["_source"] = "cache"
            return cached

    # Build query: prefer specified engines via `!` syntax
    # SearXNG supports `!engine1 !engine2 query` for engine selection
    engine_prefix = " ".join(f"!{e}" for e in engines[:5])
    full_query = f"{engine_prefix} {query}".strip()

    params = {
        "q": full_query,
        "format": "json",
        "language": lang,
        "categories": category,
        "pageno": 1,
    }

    url = f"{SEARXNG_URL}/search?{urllib.parse.urlencode(params)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "AutolycusBot/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        return {"error": str(e), "results": [], "source": "error"}

    results = data.get("results", [])[:limit]
    output = {
        "query": query,
        "engines_used": engines,
        "result_count": len(results),
        "source": "searxng",
        "results": []
    }

    for r in results:
        output["results"].append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:300],
            "engine": r.get("engine", ""),
            "score": r.get("score", 0),
        })

    set_cache(key, output)
    return output

def format_output(data: dict) -> str:
    """Pretty print search results."""
    lines = []
    source = data.get("source", "?")
    count = data.get("result_count", 0)
    lines.append(f"🔍 {count} результатов [SearXNG:{source}]")
    if data.get("engines_used"):
        lines.append(f"   Движки: {', '.join(data['engines_used'][:5])}")
    lines.append("")

    for i, r in enumerate(data.get("results", []), 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r['content']:
            lines.append(f"   {r['content'][:150]}")
        lines.append(f"   [via {r['engine']}]")
        lines.append("")

    return "\n".join(lines)

def main():
    parser = argparse.ArgumentParser(description="SearXNG cached search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--engines", help=f"Comma-separated engines (default: {','.join(DEFAULT_PRIORITY[:5])})")
    parser.add_argument("--category", default="general", help="Search category")
    parser.add_argument("--lang", default="", help="Language code (ru, en, all)")
    parser.add_argument("--limit", type=int, default=10, help="Result limit")
    parser.add_argument("--no-cache", action="store_true", help="Bypass cache")
    parser.add_argument("--json", action="store_true", help="Output raw JSON")

    args = parser.parse_args()

    engines = [e.strip() for e in args.engines.split(",")] if args.engines else None

    result = search(
        query=args.query,
        engines=engines,
        category=args.category,
        lang=args.lang,
        limit=args.limit,
        no_cache=args.no_cache,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_output(result))

if __name__ == "__main__":
    main()
