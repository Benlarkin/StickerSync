"""Minimal Discord REST client for guild stickers (no discord.py needed).

The bot needs the "Manage Expressions" permission in the target server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

API = "https://discord.com/api/v10"
USER_AGENT = "DiscordBot (https://github.com/Benlarkin/StickerSync, 0.1)"

# Sticker slots per server boost tier.
SLOTS_BY_TIER = {0: 5, 1: 15, 2: 30, 3: 60}

MAX_STICKERS_ERROR = 30039  # "Maximum number of stickers reached"


class DiscordError(RuntimeError):
    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        self.code = payload.get("code") if isinstance(payload, dict) else None
        message = payload.get("message") if isinstance(payload, dict) else str(payload)
        super().__init__(f"Discord API {status}: {message}")


@dataclass
class GuildSticker:
    id: str
    name: str
    tags: str
    format_type: int
    created_by_bot: bool


class DiscordClient:
    def __init__(self, token: str, session: requests.Session | None = None, max_retries: int = 5):
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bot {token}", "User-Agent": USER_AGENT})
        self.max_retries = max_retries
        self._me: str | None = None

    # ---------------------------------------------------------------- plumbing

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        for _ in range(self.max_retries):
            resp = self.session.request(method, f"{API}{path}", timeout=30, **kwargs)
            if resp.status_code == 429:
                # Sticker creation has a tight rate limit; wait as long as Discord says.
                retry = float(_json(resp).get("retry_after", 5))
                time.sleep(retry + 0.25)
                continue
            if resp.status_code >= 400:
                raise DiscordError(resp.status_code, _json(resp))
            return None if resp.status_code == 204 else _json(resp)
        raise DiscordError(429, {"message": "rate limited too many times"})

    def bot_user_id(self) -> str:
        if self._me is None:
            self._me = str(self._request("GET", "/users/@me")["id"])
        return self._me

    # ---------------------------------------------------------------- stickers

    def guild_info(self, guild_id: str) -> dict:
        return self._request("GET", f"/guilds/{guild_id}")

    def slot_limit(self, guild_id: str) -> int:
        tier = int(self.guild_info(guild_id).get("premium_tier", 0))
        return SLOTS_BY_TIER.get(tier, 5)

    def list_stickers(self, guild_id: str) -> list[GuildSticker]:
        me = self.bot_user_id()
        out = []
        for s in self._request("GET", f"/guilds/{guild_id}/stickers"):
            out.append(
                GuildSticker(
                    id=str(s["id"]),
                    name=s.get("name", ""),
                    tags=s.get("tags", ""),
                    format_type=int(s.get("format_type", 0)),
                    created_by_bot=str((s.get("user") or {}).get("id")) == me,
                )
            )
        return out

    def create_sticker(
        self,
        guild_id: str,
        *,
        name: str,
        tags: str,
        description: str,
        data: bytes,
        filename: str,
        content_type: str,
    ) -> GuildSticker:
        payload = self._request(
            "POST",
            f"/guilds/{guild_id}/stickers",
            data={"name": name, "tags": tags, "description": description},
            files={"file": (filename, data, content_type)},
        )
        return GuildSticker(
            id=str(payload["id"]),
            name=payload.get("name", name),
            tags=payload.get("tags", tags),
            format_type=int(payload.get("format_type", 0)),
            created_by_bot=True,
        )

    def delete_sticker(self, guild_id: str, sticker_id: str) -> None:
        self._request("DELETE", f"/guilds/{guild_id}/stickers/{sticker_id}")


def _json(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return {"message": resp.text[:200]}
