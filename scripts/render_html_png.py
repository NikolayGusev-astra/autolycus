#!/usr/bin/env python3
"""
Convert HTML file to PNG image using Playwright Chromium.
Usage: render_html_png.py <input.html> [output.png] [--width 850] [--height 1500]

If output.png omitted, replaces .html with .png in same directory.
"""
import asyncio, sys, os
from playwright.async_api import async_playwright

async def main():
    if len(sys.argv) < 2:
        print("Usage: render_html_png.py <input.html> [output.png] [--width N] [--height N]", file=sys.stderr)
        sys.exit(1)

    html_path = sys.argv[1]
    if not os.path.exists(html_path):
        print(f"File not found: {html_path}", file=sys.stderr)
        sys.exit(1)

    # Parse optional args
    width, height = 850, 1500
    png_path = None
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--width':
            width = int(sys.argv[i+1]); i += 2
        elif sys.argv[i] == '--height':
            height = int(sys.argv[i+1]); i += 2
        else:
            png_path = sys.argv[i]; i += 1

    if not png_path:
        png_path = html_path.rsplit('.', 1)[0] + '.png'

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": width, "height": height})
        await page.goto(f"file://{os.path.abspath(html_path)}", wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(2000)
        await page.screenshot(path=png_path, full_page=True)
        await browser.close()

    print(f"Written: {png_path} ({os.path.getsize(png_path)} bytes)")

if __name__ == '__main__':
    asyncio.run(main())
