from __future__ import annotations

import pytest

from stickersync import sync as sync_mod
from stickersync.cli import parse_selection
from stickersync.discord_api import MAX_STICKERS_ERROR, DiscordError, GuildSticker
from stickersync.models import TikTokSticker
from stickersync.state import State
from stickersync.sync import run_sync, sticker_name

from .conftest import animated_webp


class FakeDiscord:
    """In-memory stand-in for DiscordClient."""

    def __init__(self, slots: int = 5, manual: int = 0):
        self.slots = slots
        self.stickers: dict[str, GuildSticker] = {}
        self.next_id = 1000
        for i in range(manual):
            self._add(f"manual {i}", by_bot=False)

    def _add(self, name: str, by_bot: bool = True) -> GuildSticker:
        self.next_id += 1
        s = GuildSticker(str(self.next_id), name, "sparkles", 1, by_bot)
        self.stickers[s.id] = s
        return s

    def list_stickers(self, guild_id):
        return list(self.stickers.values())

    def slot_limit(self, guild_id):
        return self.slots

    def create_sticker(self, guild_id, *, name, data, **_):
        assert len(data) <= 512_000
        if len(self.stickers) >= self.slots:
            raise DiscordError(400, {"code": MAX_STICKERS_ERROR, "message": "full"})
        return self._add(name)

    def delete_sticker(self, guild_id, sticker_id):
        del self.stickers[sticker_id]


@pytest.fixture(autouse=True)
def fake_download(monkeypatch):
    payload = animated_webp(n=10)
    monkeypatch.setattr(sync_mod, "download", lambda sticker, cache_dir: payload)


def stickers(n: int, start: int = 0) -> list[TikTokSticker]:
    return [
        TikTokSticker(id=f"id{i}", urls=["https://x"], name=f"s{i}")
        for i in range(start, start + n)
    ]


def test_uploads_new_and_skips_already_synced(settings):
    discord = FakeDiscord(slots=10)
    first = run_sync(stickers(3), settings, client=discord, log=lambda _: None)
    assert len(first.uploaded) == 3
    second = run_sync(stickers(4), settings, client=discord, log=lambda _: None)
    assert second.new == 1 and second.uploaded == ["s3"]


def test_full_server_leaves_stickers_for_next_time(settings):
    discord = FakeDiscord(slots=5, manual=4)
    report = run_sync(stickers(3), settings, client=discord, log=lambda _: None)
    assert len(report.uploaded) == 1
    assert len(report.waiting_for_slots) == 2
    state = State(settings.state_file)
    assert not state.is_done("id1")  # will be retried next run


def test_rotate_replaces_only_our_oldest_stickers(settings):
    discord = FakeDiscord(slots=5, manual=2)
    run_sync(stickers(3), settings, client=discord, log=lambda _: None)
    report = run_sync(stickers(2, start=3), settings, client=discord, rotate=True, log=print)
    assert report.removed == ["s0", "s1"]
    names = {s.name for s in discord.stickers.values()}
    assert names == {"manual 0", "manual 1", "s2", "s3", "s4"}


def test_dry_run_writes_files_without_discord(settings):
    settings = type(settings)(**{**settings.__dict__, "discord_token": None, "guild_id": None})
    report = run_sync(stickers(2), settings, dry_run=True, log=lambda _: None)
    assert len(report.prepared) == 2
    assert len(list(settings.converted_dir.iterdir())) == 2
    assert not settings.state_file.exists()  # dry runs leave no history


def test_sticker_names_are_valid_and_unique():
    taken = {"cat"}
    a = sticker_name(TikTokSticker("1", [], name="cat"), taken)
    b = sticker_name(TikTokSticker("123456789", [], name="!"), taken)
    c = sticker_name(TikTokSticker("2", [], name="x" * 50), taken)
    assert a == "cat 2"
    assert b == "tt_23456789"
    assert len(c) == 30


def test_parse_selection():
    assert parse_selection("all", 3) == [0, 1, 2]
    assert parse_selection("1,3", 3) == [0, 2]
    assert parse_selection("2-9", 4) == [1, 2, 3]
    assert parse_selection("none", 3) == []
