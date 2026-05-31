#!/usr/bin/env python3
"""
JS page renderer + content extractor.
Solves: Яндекс.Маркет, Avito, JS-heavy SPAs.

Usage:
    python3 js_fetch.py "https://market.yandex.ru/product--ford-explorer/12345"
    python3 js_fetch.py "https://www.avito.ru/..." --wait-for ".iva-item-titleStep"
    python3 js_fetch.py "URL" --camoufox  # использовать Camoufox вместо Chromium

Requirements:
    pip install playwright trafilatura
    playwright install chromium
    # Для Camoufox: pip install camoufox[geoip]
"""

import argparse, sys, json, time

def render_chromium(url: str, wait_selector: str = None, timeout: int = 30) -> str:
    """Render JS page with Playwright Chromium."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-gpu',
            ]
        )
        page = browser.new_page(
            user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'
        )
        page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)

        # Дополнительное ожидание если указан селектор
        if wait_selector:
            try:
                page.wait_for_selector(wait_selector, timeout=10000)
            except Exception:
                pass  # Таймаут — продолжаем

        # Ждём полной загрузки
        try:
            page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass

        content = page.content()
        title = page.title()
        browser.close()
        return content, title


def render_camoufox(url: str, timeout: int = 30) -> str:
    """Render JS page with Camoufox (anti-detect)."""
    try:
        from camoufox.sync_api import Camoufox
    except ImportError:
        print("❌ Camoufox не установлен: pip install camoufox[geoip]", file=sys.stderr)
        sys.exit(1)

    with Camoufox(headless=True) as browser:
        page = browser.new_page()
        page.goto(url, wait_until='domcontentloaded', timeout=timeout * 1000)
        try:
            page.wait_for_load_state('networkidle', timeout=5000)
        except Exception:
            pass
        content = page.content()
        title = page.title()
        return content, title


def extract_text(html: str) -> str:
    """Extract clean text via trafilatura."""
    import trafilatura
    result = trafilatura.extract(
        html,
        output_format='txt',
        include_comments=False,
        include_tables=True,
        deduplicate=True,
    )
    return result or ""


def yandex_market_api(search_query: str) -> list:
    """Яндекс.Маркет API (без браузера)."""
    import urllib.request, urllib.parse

    # Яндекс.Маркет search API (публичный)
    url = f"https://market.yandex.ru/search?text={urllib.parse.quote(search_query)}"
    # Нужен browser рендер — API требует авторизации
    # Fallback: используем SearXNG
    return []


def main():
    parser = argparse.ArgumentParser(description="JS page renderer + extractor")
    parser.add_argument("url", help="URL to fetch")
    parser.add_argument("--camoufox", action="store_true", help="Use Camoufox instead of Chromium")
    parser.add_argument("--wait-for", help="CSS selector to wait for")
    parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")
    parser.add_argument("--format", choices=['text', 'html', 'json'], default='text')
    parser.add_argument("--output", help="Output file path")

    args = parser.parse_args()

    print(f"🔄 Rendering: {args.url}")
    t0 = time.time()

    try:
        if args.camoufox:
            html, title = render_camoufox(args.url, args.timeout)
        else:
            html, title = render_chromium(args.url, args.wait_for, args.timeout)

        elapsed = time.time() - t0

        if args.format == 'html':
            result = html
        elif args.format == 'json':
            text = extract_text(html)
            result = json.dumps({
                "url": args.url,
                "title": title,
                "render_time": f"{elapsed:.1f}s",
                "text": text,
                "html_length": len(html),
            }, ensure_ascii=False, indent=2)
        else:  # text
            result = extract_text(html)

        print(f"✅ Done in {elapsed:.1f}s | Title: {title[:60]}")

        if args.output:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(result)
            print(f"📁 Saved to: {args.output}")
        else:
            print()
            print(result[:3000])  # Limit output
            if len(result) > 3000:
                print(f"\n... (truncated, total {len(result)} chars)")

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
