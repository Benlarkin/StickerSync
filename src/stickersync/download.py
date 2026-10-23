"""Download sticker images from TikTok's CDN, with a local cache."""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from .models import TikTokSticker

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}


class DownloadError(RuntimeError):
    pass


def _cache_path(cache_dir: Path, sticker: TikTokSticker) -> Path:
    safe = hashlib.sha1(sticker.id.encode()).hexdigest()[:20]
    return cache_dir / f"{safe}.bin"


def download(
    sticker: TikTokSticker,
    cache_dir: Path,
    session: requests.Session | None = None,
) -> bytes:
    """Return the sticker's image bytes, trying each URL until one works."""
    cached = _cache_path(cache_dir, sticker)
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()
    session = session or requests.Session()
    errors = []
    for url in sticker.urls:
        try:
            resp = session.get(url, headers=HEADERS, timeout=30)
        except requests.RequestException as exc:
            errors.append(f"{exc.__class__.__name__}")
            continue
        if resp.ok and resp.content:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(resp.content)
            return resp.content
        errors.append(str(resp.status_code))
    raise DownloadError(f"all {len(sticker.urls)} URL(s) failed ({', '.join(errors) or 'none'})")
