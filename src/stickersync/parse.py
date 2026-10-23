"""Pull sticker records out of captured TikTok responses.

TikTok's internal API changes without notice, so instead of hard-coding one
response shape this walks any JSON and looks for TikTok's usual image struct:

    {"uri": "...", "url_list": ["https://...", "https://..."]}

A dict that *contains* one or more of those (e.g. ``{"id": ..., "animate_url": {...}}``)
is treated as a sticker. Keys that are obviously not stickers (avatars, video
covers, music art) are ignored.

Accepted inputs: StickerSync capture files, browser HAR exports, raw JSON
responses, and plain text files with one image URL per line.
"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import deque
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .models import TikTokSticker

ID_KEYS = ("sticker_id_str", "sticker_id", "id_str", "id", "emoji_id")
NAME_KEYS = ("display_name", "name", "title", "keyword", "text")
IGNORED_IMAGE_KEYS = ("avatar", "cover", "banner", "music", "play_addr", "video", "author")
ANIMATED_KEY_HINTS = ("anim", "dynamic", "gif", "motion")


def _is_image_struct(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("url_list"), list)
        and any(isinstance(u, str) and u.startswith("http") for u in value["url_list"])
    )


def _walk(obj: Any) -> Iterator[dict]:
    """Breadth-first, so stickers come out in the order TikTok listed them."""
    queue = deque([obj])
    while queue:
        cur = queue.popleft()
        if isinstance(cur, dict):
            yield cur
            queue.extend(cur.values())
        elif isinstance(cur, list):
            queue.extend(cur)


def _pick_id(d: dict, images: dict[str, dict]) -> str:
    for key in ID_KEYS:
        val = d.get(key)
        if isinstance(val, str | int) and str(val).strip() and str(val) != "0":
            return str(val)
    # Fall back to the image uri, which is stable for a given sticker.
    first = next(iter(images.values()))
    seed = first.get("uri") or first["url_list"][0]
    return "u" + hashlib.sha1(str(seed).encode()).hexdigest()[:16]


def _pick_name(d: dict) -> str | None:
    for key in NAME_KEYS:
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def stickers_from_json(data: Any) -> list[TikTokSticker]:
    found: dict[str, TikTokSticker] = {}
    for d in _walk(data):
        images = {
            k: v
            for k, v in d.items()
            if _is_image_struct(v) and not any(bad in k.lower() for bad in IGNORED_IMAGE_KEYS)
        }
        if not images:
            continue
        # Prefer animated variants first so we download the moving version.
        ordered = sorted(
            images.items(),
            key=lambda kv: not any(h in kv[0].lower() for h in ANIMATED_KEY_HINTS),
        )
        urls: list[str] = []
        for _, img in ordered:
            urls.extend(u for u in img["url_list"] if isinstance(u, str) and u not in urls)
        animated = any(any(h in k.lower() for h in ANIMATED_KEY_HINTS) for k in images)
        sticker = TikTokSticker(
            id=_pick_id(d, images),
            urls=urls,
            name=_pick_name(d),
            animated_hint=animated or None,
        )
        if sticker.id not in found:
            found[sticker.id] = sticker
    return list(found.values())


def _har_bodies(har: dict) -> Iterator[Any]:
    for entry in har.get("log", {}).get("entries", []):
        content = entry.get("response", {}).get("content", {})
        text = content.get("text")
        if not text:
            continue
        if content.get("encoding") == "base64":
            try:
                text = base64.b64decode(text).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                continue
        try:
            yield json.loads(text)
        except json.JSONDecodeError:
            continue


def _bodies_from_file(path: Path) -> Iterable[Any]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict) and "log" in data and "entries" in data.get("log", {}):
        return list(_har_bodies(data))
    if isinstance(data, dict) and data.get("format") == "stickersync-capture":
        return [r["body"] for r in data.get("responses", []) if "body" in r]
    return [data]


def _stickers_from_url_list(path: Path) -> list[TikTokSticker]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if url.startswith("http"):
            sid = "u" + hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16]
            out.append(TikTokSticker(id=sid, urls=[url]))
    return out


def load_stickers(path: Path) -> list[TikTokSticker]:
    """Read stickers from a capture, HAR, raw JSON, or URL-list file."""
    bodies = list(_bodies_from_file(path))
    if not bodies:
        return _stickers_from_url_list(path)
    merged: dict[str, TikTokSticker] = {}
    for body in bodies:
        for s in stickers_from_json(body):
            merged.setdefault(s.id, s)
    return list(merged.values())
