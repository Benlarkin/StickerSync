"""`stickersync` command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import ConfigError, load_settings
from .discord_api import DiscordClient, DiscordError
from .models import TikTokSticker
from .parse import load_stickers
from .state import State
from .sync import run_sync

app = typer.Typer(
    help="Sync your favorited TikTok stickers to a Discord server.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def parse_selection(text: str, count: int) -> list[int]:
    """'1,3,5-7' / 'all' / '' -> zero-based indexes."""
    text = text.strip().lower()
    if text in ("", "all", "a"):
        return list(range(count))
    if text in ("none", "n", "0"):
        return []
    picked: list[int] = []
    for part in text.replace(" ", "").split(","):
        if "-" in part:
            lo, hi = part.split("-", 1)
            nums = range(int(lo), int(hi) + 1)
        else:
            nums = [int(part)]
        for n in nums:
            if 1 <= n <= count and n - 1 not in picked:
                picked.append(n - 1)
    return picked


def _interactive_pick(stickers: list[TikTokSticker]) -> list[TikTokSticker]:
    table = Table("#", "name", "id", "animated?", "url")
    for i, s in enumerate(stickers, 1):
        anim = "yes" if s.animated_hint else "?"
        table.add_row(str(i), s.name or "-", s.short_id, anim, s.urls[0][:60])
    console.print(table)
    answer = typer.prompt("Which to sync? (e.g. 1,3,5-8 / all / none)", default="all")
    return [stickers[i] for i in parse_selection(answer, len(stickers))]


def _fail(msg: str) -> None:
    console.print(f"[red]error:[/] {msg}")
    raise typer.Exit(1)


@app.command()
def login() -> None:
    """Open a browser to log in to TikTok (saved for future runs)."""
    from .fetch import BrowserUnavailable
    from .fetch import login as do_login

    try:
        do_login(load_settings())
    except BrowserUnavailable as exc:
        _fail(str(exc))
    console.print("[green]Session saved.[/]")


@app.command()
def capture(
    start_url: Annotated[str, typer.Option(help="Page to open first.")] = (
        "https://www.tiktok.com/foryou"
    ),
    match: Annotated[str, typer.Option(help="Regex for API URLs to keep.")] = (
        r"sticker|emoji|emote|gif"
    ),
    save_all: Annotated[
        bool, typer.Option("--all", help="Keep every JSON response (for debugging).")
    ] = False,
) -> None:
    """Record your Favorites sticker list from TikTok without uploading anything."""
    from .fetch import BrowserUnavailable
    from .fetch import capture as do_capture

    try:
        path = do_capture(load_settings(), start_url=start_url, match=match, save_all=save_all)
    except BrowserUnavailable as exc:
        _fail(str(exc))
    found = load_stickers(path)
    console.print(f"Saved [bold]{path}[/] with {len(found)} sticker(s).")
    if not found:
        console.print(
            "No stickers recognized. Try [bold]stickersync capture --all[/] and check "
            "json_urls_seen in the capture file for the sticker endpoint."
        )


@app.command()
def inspect(path: Path) -> None:
    """Show which stickers StickerSync finds in a capture, HAR, JSON or URL-list file."""
    stickers = load_stickers(path)
    state = State(load_settings().state_file)
    table = Table("id", "name", "animated?", "status", "urls")
    for s in stickers:
        rec = state.records.get(s.id)
        table.add_row(
            s.short_id,
            s.name or "-",
            "yes" if s.animated_hint else "?",
            rec.status if rec else "new",
            str(len(s.urls)),
        )
    console.print(table)
    console.print(f"{len(stickers)} sticker(s) found.")


@app.command()
def run(
    from_file: Annotated[
        Path | None,
        typer.Option("--from", help="Use a capture/HAR/JSON/URL-list file instead of TikTok."),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Download and convert only; no uploads.")
    ] = False,
    limit: Annotated[int | None, typer.Option(help="Process at most N new stickers.")] = None,
    rotate: Annotated[
        bool, typer.Option(help="Delete the oldest StickerSync uploads when slots run out.")
    ] = False,
    pick: Annotated[bool, typer.Option(help="Choose which new stickers to sync.")] = False,
    fmt: Annotated[
        str, typer.Option("--format", help="auto (APNG, falls back to GIF), apng, or gif.")
    ] = "auto",
) -> None:
    """Capture new favorites from TikTok, convert them, and upload to Discord."""
    if fmt not in ("auto", "apng", "gif"):
        _fail("--format must be auto, apng or gif")
    settings = load_settings()
    if from_file is None:
        from .fetch import BrowserUnavailable
        from .fetch import capture as do_capture

        try:
            from_file = do_capture(settings)
        except BrowserUnavailable as exc:
            _fail(str(exc))
    assert from_file is not None
    stickers = load_stickers(from_file)
    console.print(f"Found {len(stickers)} favorited sticker(s).")
    try:
        report = run_sync(
            stickers,
            settings,
            fmt=fmt,  # type: ignore[arg-type]
            dry_run=dry_run,
            limit=limit,
            rotate=rotate,
            selector=_interactive_pick if pick else None,
            log=console.print,
        )
    except (ConfigError, DiscordError) as exc:
        _fail(str(exc))
    console.print(
        f"\n[bold]Done.[/] new: {report.new}, converted: {len(report.prepared)}, "
        f"uploaded: {len(report.uploaded)}, removed: {len(report.removed)}, "
        f"failed: {len(report.failed)}, waiting for slots: {len(report.waiting_for_slots)}"
    )


@app.command()
def status() -> None:
    """Show what's been synced and how many sticker slots are free."""
    settings = load_settings()
    state = State(settings.state_file)
    counts: dict[str, int] = {}
    for rec in state.records.values():
        counts[rec.status] = counts.get(rec.status, 0) + 1
    console.print(f"Data folder: {settings.home}")
    console.print("Local history: " + (", ".join(f"{k} {v}" for k, v in counts.items()) or "empty"))
    try:
        token, guild = settings.require_discord()
        client = DiscordClient(token)
        stickers = client.list_stickers(guild)
        limit = client.slot_limit(guild)
        mine = sum(1 for s in stickers if s.created_by_bot)
        console.print(
            f"Server stickers: {len(stickers)}/{limit} slots used ({mine} by StickerSync), "
            f"{max(limit - len(stickers), 0)} free"
        )
    except ConfigError:
        console.print("[dim]Discord not configured; skipping server check.[/]")
    except DiscordError as exc:
        _fail(str(exc))


@app.command()
def prune(
    keep: Annotated[int, typer.Option(help="How many StickerSync stickers to keep.")] = 0,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Don't ask for confirmation.")] = False,
) -> None:
    """Delete the oldest stickers StickerSync uploaded (never ones added by hand)."""
    settings = load_settings()
    try:
        token, guild = settings.require_discord()
    except ConfigError as exc:
        _fail(str(exc))
    client = DiscordClient(token)
    state = State(settings.state_file)
    live = {s.id for s in client.list_stickers(guild) if s.created_by_bot}
    tracked = [(tid, r) for tid, r in state.uploaded().items() if r.discord_id in live]
    doomed = tracked[: max(len(tracked) - keep, 0)]
    if not doomed:
        console.print("Nothing to remove.")
        return
    console.print("Will delete: " + ", ".join(r.name for _, r in doomed))
    if not yes and not typer.confirm(f"Delete {len(doomed)} sticker(s) from the server?"):
        raise typer.Exit()
    for tid, rec in doomed:
        client.delete_sticker(guild, rec.discord_id)  # type: ignore[arg-type]
        state.mark(tid, "removed", rec.name, discord_id=rec.discord_id)
        console.print(f"removed {rec.name}")


@app.command()
def version() -> None:
    """Print the version."""
    console.print(__version__)


if __name__ == "__main__":
    app()
