"""
Zero-Dollar Web Search модуль.
SearXNG (через Playwright HTML) → Playwright/Camoufox → trafilatura
"""

import asyncio
from playwright.async_api import async_playwright
from typing import Optional
from urllib.parse import quote_plus

# === Конфигурация ===
SEARXNG_URL = "http://localhost:8080"
DEFAULT_LANG = "en"
DEFAULT_NUM_RESULTS = 10


def search(query: str, language: str = DEFAULT_LANG, num_results: int = DEFAULT_NUM_RESULTS) -> list[dict]:
    """
    Поиск через SearXNG. Парсит HTML-результаты.
    Возвращает: [{url, title, snippet}, ...]
    """
    return asyncio.run(_async_search(query, language, num_results))


async def _async_search(query: str, language: str, num_results: int) -> list[dict]:
    url = f"{SEARXNG_URL}/search?q={quote_plus(query)}&language={language}"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await page.wait_for_timeout(3000)
        results = await page.evaluate("""() => {
            const items = document.querySelectorAll('.result');
            return Array.from(items).map(item => ({
                title: item.querySelector('a')?.innerText?.trim() || '',
                url: item.querySelector('a')?.href || '',
                snippet: item.querySelector('.content')?.innerText?.trim() || ''
            })).filter(r => r.title && r.url);
        }""")
        await browser.close()
    return results[:num_results]


def fetch_static(url: str) -> Optional[str]:
    """
    Получить текст статической HTML-страницы через trafilatura.
    Для блогов, документации, статей — быстро, без рендеринга.
    """
    try:
        import trafilatura
        downloaded = trafilatura.fetch_url(url)
        if downloaded:
            return trafilatura.extract(downloaded, include_comments=False, include_tables=False)
    except Exception:
        return None


async def _fetch_js_impl(url: str, wait_ms: int = 5000) -> Optional[dict]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(wait_ms)
            text = await page.evaluate("() => document.body.innerText")
            imgs = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('img'))
                    .filter(i => i.src && !i.src.includes('avatar') && !i.src.includes('logo') && !i.src.includes('icon'))
                    .map(i => ({src: i.src, alt: i.alt || '', w: i.naturalWidth, h: i.naturalHeight}))
                    .filter(i => i.w > 100 && i.h > 100);
            }""")
            return {"text": text, "images": imgs}
        except Exception as e:
            return None
        finally:
            await browser.close()


def fetch_js(url: str, wait_ms: int = 5000) -> Optional[dict]:
    """
    Получить текст JS-страницы через Playwright.
    Возвращает: {text: str, images: [{src, alt, w, h}]}
    """
    return asyncio.run(_fetch_js_impl(url, wait_ms))


def fetch_antibot(url: str, wait_ms: int = 8000) -> Optional[str]:
    """
    Получить текст через Camoufox (Firefox с антидетектом).
    Использовать если Playwright заблокировали.
    Медленнее — только при необходимости.
    """
    try:
        from camoufox.sync_api import Camoufox
        with Camoufox(headless=True) as browser:
            page = browser.new_page()
            page.goto(url, timeout=60000)
            page.wait_for_timeout(wait_ms)
            return page.evaluate("() => document.body.innerText")
    except Exception:
        return None


def web_search(query: str, deep: bool = False, num_results: int = 5, language: str = "en") -> str:
    """
    Основная функция веб-поиска.
    
    deep=False (умолчание): только SearXNG сниппеты
    deep=True: загрузка каждой страницы для полного текста
    """
    results = search(query, language=language, num_results=num_results)
    
    if not results:
        return f"По запросу '{query}' ничего не найдено через SearXNG."
    
    output = [f"## Результаты поиска: '{query}'\n"]
    for i, r in enumerate(results, 1):
        output.append(f"### {i}. {r['title']}")
        output.append(f"URL: {r['url']}")
        snippet = r.get('snippet', '')
        if snippet:
            output.append(f"Сниппет: {snippet[:300]}")
        if deep:
            text = fetch_static(r['url'])
            if not text:
                js_result = fetch_js(r['url'])
                text = js_result['text'] if js_result else None
            if text:
                output.append(f"Текст: {text[:500]}...")
        output.append("")
    
    return "\n".join(output)


if __name__ == "__main__":
    print(web_search("ford explorer II vin frame rail location", deep=False, num_results=3))
