from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TikTokSticker:
    """A sticker found in a TikTok API response (or an imported file)."""

    id: str
    urls: list[str]
    name: str | None = None
    animated_hint: bool | None = None
    extra: dict = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[-8:]
