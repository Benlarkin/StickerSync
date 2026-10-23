"""The pipeline: new stickers -> download -> convert -> (make room) -> upload."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Settings
from .convert import ConvertError, ConvertResult, Format, convert
from .discord_api import MAX_STICKERS_ERROR, DiscordClient, DiscordError
from .download import DownloadError, download
from .models import TikTokSticker
from .state import State

Log = Callable[[str], None]
Selector = Callable[[list[TikTokSticker]], list[TikTokSticker]]


@dataclass
class Prepared:
    sticker: TikTokSticker
    name: str
    result: ConvertResult


@dataclass
class SyncReport:
    found: int = 0
    new: int = 0
    prepared: list[Prepared] = field(default_factory=list)
    uploaded: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    waiting_for_slots: list[str] = field(default_factory=list)


def sticker_name(sticker: TikTokSticker, taken: set[str]) -> str:
    """A Discord-valid (2-30 chars) name that doesn't clash with existing ones."""
    base = re.sub(r"\s+", " ", (sticker.name or "").strip())
    base = re.sub(r"[^\w \-]", "", base).strip()
    if len(base) < 2:
        base = f"tt_{sticker.short_id}"
    base = base[:30]
    name, n = base, 2
    while name.lower() in taken:
        suffix = f" {n}"
        name = base[: 30 - len(suffix)] + suffix
        n += 1
    taken.add(name.lower())
    return name


def prepare(
    stickers: list[TikTokSticker],
    settings: Settings,
    state: State,
    *,
    fmt: Format,
    taken_names: set[str],
    log: Log,
    record: bool = True,
) -> tuple[list[Prepared], list[tuple[str, str]]]:
    """Download and convert. With record=False (dry runs), state is left untouched."""
    mark = state.mark if record else (lambda *a, **k: None)
    prepared, failed = [], []
    settings.converted_dir.mkdir(parents=True, exist_ok=True)
    for s in stickers:
        name = sticker_name(s, taken_names)
        try:
            raw = download(s, settings.originals_dir)
        except DownloadError as exc:
            failed.append((name, str(exc)))
            mark(s.id, "failed", name, detail=f"download: {exc}")
            log(f"[red]x[/] {name}: download failed ({exc})")
            continue
        try:
            result = convert(raw, fmt)
        except ConvertError as exc:
            # Won't fix itself on a retry, so don't try again every week.
            failed.append((name, str(exc)))
            mark(s.id, "skipped", name, detail=f"convert: {exc}")
            log(f"[yellow]-[/] {name}: can't convert ({exc}); skipping for good")
            continue
        (settings.converted_dir / f"{s.short_id}.{result.ext}").write_bytes(result.data)
        kb = len(result.data) / 1000
        log(f"[green]ok[/] {name}: {result.strategy}, {result.frames} frame(s), {kb:.0f} KB")
        prepared.append(Prepared(s, name, result))
    return prepared, failed


def run_sync(
    stickers: list[TikTokSticker],
    settings: Settings,
    *,
    fmt: Format = "auto",
    dry_run: bool = False,
    limit: int | None = None,
    rotate: bool = False,
    selector: Selector | None = None,
    client: DiscordClient | None = None,
    log: Log = print,
) -> SyncReport:
    state = State(settings.state_file)
    report = SyncReport(found=len(stickers))

    new = [s for s in stickers if not state.is_done(s.id)]
    report.new = len(new)
    if selector and new:
        new = selector(new)
    if limit is not None:
        new = new[:limit]
    if not new:
        log("Nothing new to sync.")
        return report

    guild_id = None
    existing = []
    if not dry_run:
        token, guild_id = settings.require_discord()
        client = client or DiscordClient(token)
        existing = client.list_stickers(guild_id)

    taken = {g.name.lower() for g in existing}
    report.prepared, report.failed = prepare(
        new, settings, state, fmt=fmt, taken_names=taken, log=log, record=not dry_run
    )
    if dry_run or not report.prepared:
        if dry_run:
            log(f"Dry run: converted files are in {settings.converted_dir}")
        return report

    assert client is not None and guild_id is not None
    free = client.slot_limit(guild_id) - len(existing)
    live_ids = {g.id for g in existing if g.created_by_bot}
    # Oldest stickers this tool uploaded that are still on the server.
    rotation = [(tid, r) for tid, r in state.uploaded().items() if r.discord_id in live_ids]

    for item in report.prepared:
        if free <= 0 and rotate and rotation:
            old_tid, old = rotation.pop(0)
            try:
                client.delete_sticker(guild_id, old.discord_id)  # type: ignore[arg-type]
                state.mark(old_tid, "removed", old.name, discord_id=old.discord_id)
                report.removed.append(old.name)
                log(f"[blue]~[/] removed {old.name} to make room")
                free += 1
            except DiscordError as exc:
                log(f"[red]x[/] couldn't remove {old.name}: {exc}")
        if free <= 0:
            report.waiting_for_slots.append(item.name)
            continue
        try:
            created = client.create_sticker(
                guild_id,
                name=item.name,
                tags=settings.default_tag,
                description=settings.description,
                data=item.result.data,
                filename=f"{item.sticker.short_id}.{item.result.ext}",
                content_type=item.result.content_type,
            )
        except DiscordError as exc:
            if exc.code == MAX_STICKERS_ERROR:
                free = 0
                report.waiting_for_slots.append(item.name)
                continue
            report.failed.append((item.name, str(exc)))
            state.mark(item.sticker.id, "failed", item.name, detail=f"upload: {exc}")
            log(f"[red]x[/] {item.name}: upload failed ({exc})")
            continue
        state.mark(item.sticker.id, "uploaded", item.name, discord_id=created.id)
        report.uploaded.append(item.name)
        free -= 1
        log(f"[green]^[/] uploaded {item.name}")

    if report.waiting_for_slots:
        log(
            f"[yellow]{len(report.waiting_for_slots)} sticker(s) waiting for free slots.[/] "
            "Run again with --rotate to replace the oldest ones, or free up slots."
        )
    return report
