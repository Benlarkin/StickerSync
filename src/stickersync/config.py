"""Settings loaded from the environment (and a local .env file)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Raised when a required setting is missing."""


@dataclass(frozen=True)
class Settings:
    home: Path
    discord_token: str | None
    guild_id: str | None
    default_tag: str
    description: str

    # Everything StickerSync writes lives under `home`, outside the repo.
    @property
    def browser_profile(self) -> Path:
        return self.home / "browser-profile"

    @property
    def state_file(self) -> Path:
        return self.home / "state.json"

    @property
    def captures_dir(self) -> Path:
        return self.home / "captures"

    @property
    def originals_dir(self) -> Path:
        return self.home / "cache" / "originals"

    @property
    def converted_dir(self) -> Path:
        return self.home / "cache" / "converted"

    def require_discord(self) -> tuple[str, str]:
        if not self.discord_token or not self.guild_id:
            raise ConfigError(
                "DISCORD_BOT_TOKEN and DISCORD_GUILD_ID must be set "
                "(copy .env.example to .env and fill them in)."
            )
        return self.discord_token, self.guild_id


def load_settings() -> Settings:
    load_dotenv()
    home = Path(os.environ.get("STICKERSYNC_HOME", Path.home() / ".stickersync")).expanduser()
    return Settings(
        home=home,
        discord_token=os.environ.get("DISCORD_BOT_TOKEN") or None,
        guild_id=os.environ.get("DISCORD_GUILD_ID") or None,
        default_tag=os.environ.get("STICKERSYNC_DEFAULT_TAG", "sparkles"),
        description=os.environ.get("STICKERSYNC_DESCRIPTION", "Synced from TikTok by StickerSync"),
    )
