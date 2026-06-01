"""
Jarvis V2 — Browser Tools
Web search via DuckDuckGo Lite, page visits via Playwright, URL opening.

Uses a PERSISTENT Chromium profile at _jarvis/browser_profile/ so login
sessions, cookies, and local storage survive across Jarvis restarts. Log
into GoHighLevel / Gmail / GitHub / etc. once, and Jarvis stays logged in
on every subsequent browser tool call.
"""

import os
import re
import webbrowser
import subprocess
from urllib.parse import unquote, parse_qs, urlparse
import httpx
from playwright.async_api import async_playwright

_pw = None
_context = None  # BrowserContext (persistent)

# Profile dir — survives restarts, holds cookies + localStorage + indexedDB
_PROFILE_DIR = os.path.join(os.path.dirname(__file__), "browser_profile")


def _bring_chromium_to_front():
    """Bring the Playwright Chromium window to the foreground on Windows."""
    try:
        subprocess.run([
            "powershell", "-Command",
            '(Get-Process -Name "chromium","chrome" -ErrorAction SilentlyContinue | '
            'Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -Last 1).MainWindowHandle | '
            'ForEach-Object { Add-Type "using System; using System.Runtime.InteropServices; '
            'public class W { [DllImport(\\\"user32.dll\\\")] public static extern bool SetForegroundWindow(IntPtr h); }"; '
            '[W]::SetForegroundWindow($_) }'
        ], capture_output=True, timeout=3)
    except Exception:
        pass


async def _get_browser():
    """Return a persistent BrowserContext. Cookies + auth sessions survive
    restarts because we use launch_persistent_context with an on-disk profile
    dir, instead of the in-memory new_context() pattern."""
    global _pw, _context
    if _context is None:
        os.makedirs(_PROFILE_DIR, exist_ok=True)
        _pw = await async_playwright().start()
        _context = await _pw.chromium.launch_persistent_context(
            user_data_dir=_PROFILE_DIR,
            headless=False,
            args=["--start-maximized"],
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
            no_viewport=True,
            accept_downloads=True,
        )
    return _context


async def open_for_login(url: str, prompt: str = "") -> dict:
    """Open a URL in Jarvis's controlled browser so the user can log in
    manually. Cookies + session save into the persistent profile, so future
    browser tool calls are already authenticated. Used for GHL, Gmail, etc.
    where API auth doesn't cover the UI builder."""
    ctx = await _get_browser()
    pages = ctx.pages
    page = pages[-1] if pages else await ctx.new_page()
    try:
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        _bring_chromium_to_front()
        return {
            "ok": True,
            "url": page.url,
            "title": await page.title(),
            "instruction": prompt or (
                "Log in in the Chromium window. The session is saved into the "
                "persistent profile, so Jarvis will stay logged in next time."
            ),
        }
    except Exception as e:
        return {"error": str(e), "url": url}


async def search_and_read(query: str) -> dict:
    """Search DuckDuckGo in visible browser, click first result, read the page."""
    ctx = await _get_browser()
    page = await ctx.new_page()
    try:
        # DuckDuckGo search (no cookie banner, no reCAPTCHA)
        search_url = f"https://duckduckgo.com/?q={query}"
        await page.goto(search_url, timeout=15000)
        _bring_chromium_to_front()
        await page.wait_for_timeout(2000)

        # Click first organic result
        first_link = page.locator('[data-testid="result-title-a"]').first
        if await first_link.count() > 0:
            await first_link.click()
            await page.wait_for_timeout(3000)

            # Read page content
            title = await page.title()
            url = page.url
            text = await page.evaluate("""
                () => {
                    const selectors = ['main', 'article', '[role="main"]', '.content', '#content', 'body'];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText.trim().length > 100) {
                            return el.innerText.trim();
                        }
                    }
                    return document.body?.innerText?.trim() || '';
                }
            """)
            return {"title": title, "url": url, "content": text[:3000]}
        else:
            return {"title": "No results", "url": search_url, "content": "No results found."}
    except Exception as e:
        return {"error": str(e), "url": query}
    finally:
        pass


async def visit(url: str, max_chars: int = 5000) -> dict:
    """Visit a URL and extract main text content."""
    ctx = await _get_browser()
    page = await ctx.new_page()
    try:
        await page.goto(url, timeout=15000, wait_until="domcontentloaded")
        text = await page.evaluate("""
            () => {
                const selectors = ['main', 'article', '[role="main"]', '.content', '#content', 'body'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 100) {
                        return el.innerText.trim();
                    }
                }
                return document.body?.innerText?.trim() || '';
            }
        """)
        title = await page.title()
        return {"title": title, "url": url, "content": text[:max_chars]}
    except Exception as e:
        return {"error": str(e), "url": url}
    finally:
        await page.close()


async def fetch_news() -> str:
    """Fetch current world news from worldmonitor.app in visible browser."""
    ctx = await _get_browser()
    page = await ctx.new_page()
    try:
        await page.goto("https://www.worldmonitor.app/", timeout=20000)
        _bring_chromium_to_front()
        await page.wait_for_timeout(6000)  # Wait for JS to render
        text = await page.evaluate("() => document.body.innerText")
        # Extract the news sections
        content = text[:4000]
        return f"World Monitor headlines:\n{content}"
    except Exception as e:
        return f"News could not be loaded: {e}"
    finally:
        pass  # Keep page open so user can see it


async def open_url(url: str):
    """Open URL in user's default browser (non-blocking)."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, webbrowser.open, url)
    return {"success": True, "url": url}


async def close():
    global _context, _pw
    if _context:
        try:
            await _context.close()
        except Exception:
            pass
        _context = None
    if _pw:
        try:
            await _pw.stop()
        except Exception:
            pass
        _pw = None


# ============================================================================
#   Interactive browser control — so Jarvis can actually operate the browser
# ============================================================================

async def _current_page():
    """Return the most-recently-opened page in the shared browser context, or None."""
    ctx = await _get_browser()
    pages = ctx.pages
    return pages[-1] if pages else None


async def navigate(url: str) -> dict:
    """Navigate the current page to a URL (opens a new tab if none exists)."""
    ctx = await _get_browser()
    page = await _current_page()
    if page is None:
        page = await ctx.new_page()
    try:
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        _bring_chromium_to_front()
        return {"title": await page.title(), "url": page.url}
    except Exception as e:
        return {"error": str(e), "url": url}


async def get_page_info(max_chars: int = 3000) -> dict:
    """Return current page title, URL, and visible text."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page. Call navigate() or search_and_read() first."}
    try:
        title = await page.title()
        url = page.url
        text = await page.evaluate("""
            () => {
                const selectors = ['main', 'article', '[role=\"main\"]', '.content', '#content', 'body'];
                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el && el.innerText.trim().length > 100) return el.innerText.trim();
                }
                return document.body?.innerText?.trim() || '';
            }
        """)
        return {"title": title, "url": url, "content": text[:max_chars]}
    except Exception as e:
        return {"error": str(e)}


async def click_text(text: str, exact: bool = False) -> dict:
    """Click the first element whose visible text contains/equals `text`."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        if exact:
            loc = page.get_by_text(text, exact=True).first
        else:
            # role-agnostic: match any element with that text
            loc = page.locator(f"text={text}").first
        await loc.click(timeout=8000)
        await page.wait_for_timeout(1200)
        return {"ok": True, "clicked": text, "url": page.url}
    except Exception as e:
        return {"error": str(e), "text": text}


async def click_selector(selector: str) -> dict:
    """Click the element matching a CSS selector."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        await page.locator(selector).first.click(timeout=8000)
        await page.wait_for_timeout(1200)
        return {"ok": True, "clicked": selector, "url": page.url}
    except Exception as e:
        return {"error": str(e), "selector": selector}


async def fill_input(selector: str, value: str, submit: bool = False) -> dict:
    """Fill an input matched by CSS selector. If submit=True, press Enter after."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        el = page.locator(selector).first
        await el.fill(value, timeout=8000)
        if submit:
            await el.press("Enter")
            await page.wait_for_timeout(1500)
        return {"ok": True, "selector": selector, "value": value[:60]}
    except Exception as e:
        return {"error": str(e), "selector": selector}


async def press_key(key: str) -> dict:
    """Press a keyboard key on the current page (e.g. 'Enter', 'Escape', 'Tab')."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        await page.keyboard.press(key)
        await page.wait_for_timeout(500)
        return {"ok": True, "key": key}
    except Exception as e:
        return {"error": str(e)}


async def go_back() -> dict:
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        await page.go_back(timeout=10000)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"error": str(e)}


async def go_forward() -> dict:
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        await page.go_forward(timeout=10000)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"error": str(e)}


async def evaluate_js(code: str) -> dict:
    """Evaluate arbitrary JS in the current page. Returns the result (stringified)."""
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        result = await page.evaluate(code)
        return {"ok": True, "result": str(result)[:2000]}
    except Exception as e:
        return {"error": str(e)}


async def screenshot() -> dict:
    """Capture a screenshot of the CURRENT controlled-browser tab (NOT the
    user's monitor). Returns base64 PNG. Used for driving heavy SPA UIs like
    the GoHighLevel form/funnel builders where DOM scraping returns nothing
    useful."""
    import base64
    page = await _current_page()
    if page is None:
        return {"error": "No open page"}
    try:
        png = await page.screenshot(type="png", full_page=False)
        return {"ok": True, "image_b64": base64.b64encode(png).decode("ascii"), "url": page.url}
    except Exception as e:
        return {"error": str(e)}
