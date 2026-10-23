"""Capture your favorited stickers from TikTok using a real browser.

Instead of reverse-engineering TikTok's signed API, StickerSync opens a normal
Chromium window with a persistent profile (so you log in once) and records the
API responses the page itself makes while you open the sticker picker.

Requires the optional browser extra:
    pip install "stickersync[browser]" && playwright install chromium
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Settings

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext, Response

DEFAULT_MATCH = r"sticker|emoji|emote|gif"
START_URL = "https://www.tiktok.com/foryou"
LOGIN_URL = "https://www.tiktok.com/login"


class BrowserUnavailable(RuntimeError):
    pass


def _launch(settings: Settings, headless: bool = False) -> tuple[Any, BrowserContext]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise BrowserUnavailable(
            'Playwright is not installed. Run: pip install "stickersync[browser]" '
            "&& playwright install chromium"
        ) from exc
    settings.browser_profile.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(settings.browser_profile),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    return pw, context


def login(settings: Settings, wait=input) -> None:
    """Open TikTok so you can log in; the session is saved to the profile folder."""
    pw, context = _launch(settings)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(LOGIN_URL)
        wait("Log in to TikTok in the browser window, then press Enter here... ")
    finally:
        context.close()
        pw.stop()


def capture(
    settings: Settings,
    *,
    start_url: str = START_URL,
    match: str = DEFAULT_MATCH,
    save_all: bool = False,
    wait=input,
) -> Path:
    """Record sticker API responses while you open your Favorites sticker tab.

    Returns the path of the capture file (readable by `parse.load_stickers`).
    """
    pattern = re.compile(match, re.IGNORECASE)
    seen: list[Response] = []
    pw, context = _launch(settings)
    try:
        # Only collect Response objects here; reading bodies inside the event
        # handler can deadlock Playwright's sync API.
        context.on("response", lambda r: seen.append(r))
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(start_url)
        wait(
            "\nIn the browser window:\n"
            "  1. Open any video's comments\n"
            "  2. Click the sticker/emoji button in the comment box\n"
            "  3. Open the Favorites tab and scroll to the bottom so everything loads\n"
            "Then press Enter here... "
        )
        responses, urls = [], []
        for r in seen:
            ctype = (r.headers or {}).get("content-type", "")
            if "json" not in ctype:
                continue
            urls.append(r.url)
            if not (save_all or pattern.search(r.url)):
                continue
            try:
                responses.append({"url": r.url, "body": r.json()})
            except Exception:  # body evicted or not valid JSON
                continue
    finally:
        context.close()
        pw.stop()

    settings.captures_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = settings.captures_dir / f"capture-{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "format": "stickersync-capture",
                "captured_at": stamp,
                "match": match,
                "json_urls_seen": urls,
                "responses": responses,
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return out
