"""A tiny local web server that powers the StickerSync GUI.

Everything runs on 127.0.0.1 only. Each launch gets a random token that every
API call must carry, and requests with a foreign Host header are refused, so
other websites open in your browser can't talk to it.

Long tasks (log in, capture, sync) run as background "jobs" in a worker thread;
the page polls /api/job for progress and log lines.
"""

from __future__ import annotations

import contextlib
import json
import mimetypes
import os
import re
import secrets
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from rich.text import Text

from .. import __version__
from ..config import Settings, load_settings
from ..discord_api import DiscordClient, DiscordError
from ..download import DownloadError, download
from ..models import TikTokSticker
from ..parse import load_stickers
from ..state import State
from ..sync import run_sync

STATIC = resources.files("stickersync.gui") / "static"


# --------------------------------------------------------------------------- jobs


@dataclass
class Job:
    id: int
    kind: str
    status: str = "running"  # running | waiting | done | error
    log: list[str] = field(default_factory=list)
    prompt: str | None = None
    result: dict | None = None
    error: str | None = None
    started: float = field(default_factory=time.time)
    _resume: threading.Event = field(default_factory=threading.Event, repr=False)

    def say(self, msg: str) -> None:
        self.log.append(Text.from_markup(msg).plain if "[" in msg else msg)

    def wait_for_user(self, prompt: str) -> str:
        """Used as the `wait` callback of fetch.login/capture: pause until 'Continue'."""
        lines = [ln.strip() for ln in prompt.strip().splitlines() if ln.strip()]
        text = "\n".join(lines)
        self.prompt = re.sub(r",?\s*(then )?press Enter here\.*", "", text, flags=re.I).strip()
        self.status = "waiting"
        self._resume.wait()
        self._resume.clear()
        self.prompt = None
        self.status = "running"
        return ""

    def public(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "log": self.log[-200:],
            "prompt": self.prompt,
            "result": self.result,
            "error": self.error,
        }


class JobRunner:
    def __init__(self) -> None:
        self.current: Job | None = None
        self._lock = threading.Lock()
        self._next = 1

    def busy(self) -> bool:
        return self.current is not None and self.current.status in ("running", "waiting")

    def start(self, kind: str, fn: Callable[[Job], dict | None]) -> Job:
        with self._lock:
            if self.busy():
                raise RuntimeError("Another task is still running.")
            job = Job(self._next, kind)
            self._next += 1
            self.current = job

        def run() -> None:
            try:
                job.result = fn(job) or {}
                job.status = "done"
            except Exception as exc:  # report anything to the page
                job.error = str(exc) or exc.__class__.__name__
                job.say(f"Error: {job.error}")
                job.say(traceback.format_exc(limit=3).strip().splitlines()[-1])
                job.status = "error"

        threading.Thread(target=run, name=f"stickersync-{kind}", daemon=True).start()
        return job


# --------------------------------------------------------------------------- app


class GuiApp:
    """All GUI behaviour, independent of HTTP so it can be tested directly."""

    def __init__(self, source: Path | None = None, client_factory=DiscordClient):
        self.source = source
        self.jobs = JobRunner()
        self.client_factory = client_factory
        self._discord_cache: tuple[float, dict] | None = None
        self._stickers_cache: tuple[Path, float, list[TikTokSticker]] | None = None

    # -- data ---------------------------------------------------------------

    @property
    def settings(self) -> Settings:
        return load_settings()

    def source_file(self) -> Path | None:
        if self.source:
            return self.source
        captures = sorted(self.settings.captures_dir.glob("capture-*.json"))
        return captures[-1] if captures else None

    def stickers(self) -> list[TikTokSticker]:
        path = self.source_file()
        if path is None or not path.exists():
            return []
        mtime = path.stat().st_mtime
        if self._stickers_cache and self._stickers_cache[:2] == (path, mtime):
            return self._stickers_cache[2]
        found = load_stickers(path)
        self._stickers_cache = (path, mtime, found)
        return found

    def sticker(self, sticker_id: str) -> TikTokSticker | None:
        return next((s for s in self.stickers() if s.id == sticker_id), None)

    def discord_status(self, refresh: bool = False) -> dict:
        s = self.settings
        if not (s.discord_token and s.guild_id):
            return {"configured": False, "guild_id": s.guild_id or ""}
        if not refresh and self._discord_cache and time.time() - self._discord_cache[0] < 30:
            return self._discord_cache[1]
        try:
            client = self.client_factory(s.discord_token)
            info = client.guild_info(s.guild_id)
            stickers = client.list_stickers(s.guild_id)
            limit = client.slot_limit(s.guild_id)
            data = {
                "configured": True,
                "guild_id": s.guild_id,
                "guild_name": info.get("name") or "your server",
                "slots_used": len(stickers),
                "slot_limit": limit,
                "ours": sum(1 for x in stickers if x.created_by_bot),
            }
        except DiscordError as exc:
            data = {"configured": True, "guild_id": s.guild_id, "error": str(exc)}
        except Exception as exc:  # network down etc.
            data = {
                "configured": True,
                "guild_id": s.guild_id,
                "error": f"Couldn't reach Discord ({exc.__class__.__name__})",
            }
        self._discord_cache = (time.time(), data)
        return data

    def status(self, refresh: bool = False) -> dict:
        s = self.settings
        state = State(s.state_file)
        history = sorted(state.records.values(), key=lambda r: r.updated_at, reverse=True)
        src = self.source_file()
        profile = s.browser_profile
        return {
            "version": __version__,
            "tiktok": {"logged_in": profile.exists() and any(profile.iterdir())},
            "discord": self.discord_status(refresh),
            "source": {
                "name": src.name if src else None,
                "updated": src.stat().st_mtime if src and src.exists() else None,
            },
            "history": [
                {"name": r.name, "status": r.status, "at": r.updated_at, "detail": r.detail}
                for r in history[:6]
            ],
            "busy": self.jobs.busy(),
        }

    def sticker_list(self) -> list[dict]:
        state = State(self.settings.state_file)
        out = []
        for s in self.stickers():
            rec = state.records.get(s.id)
            out.append(
                {
                    "id": s.id,
                    "name": s.name or f"tt_{s.short_id}",
                    "animated": bool(s.animated_hint),
                    "status": rec.status if rec else "new",
                    "detail": rec.detail if rec else None,
                }
            )
        return out

    def original_image(self, sticker_id: str) -> tuple[bytes, str] | None:
        sticker = self.sticker(sticker_id)
        if sticker is None:
            return None
        try:
            data = download(sticker, self.settings.originals_dir)
        except DownloadError:
            return None
        return data, sniff_type(data)

    def converted_image(self, short_id: str) -> tuple[bytes, str] | None:
        folder = self.settings.converted_dir
        for ext, ctype in (("png", "image/png"), ("gif", "image/gif")):
            path = folder / f"{Path(short_id).name}.{ext}"
            if path.exists():
                return path.read_bytes(), ctype
        return None

    def save_settings(self, token: str | None, guild_id: str | None) -> dict:
        s = self.settings
        values: dict[str, str] = {}
        if s.config_file.exists():
            for line in s.config_file.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.lstrip().startswith("#"):
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip()
        if token:
            values["DISCORD_BOT_TOKEN"] = token.strip()
        if guild_id is not None:
            gid = guild_id.strip()
            if gid and not gid.isdigit():
                raise ValueError("Server ID should be a number (right-click server > Copy ID).")
            values["DISCORD_GUILD_ID"] = gid
        s.home.mkdir(parents=True, exist_ok=True)
        body = "".join(f"{k}={v}\n" for k, v in values.items())
        s.config_file.write_text("# Saved by the StickerSync GUI\n" + body, encoding="utf-8")
        with contextlib.suppress(OSError):  # holds a bot token
            os.chmod(s.config_file, 0o600)
        # Already-loaded env vars would otherwise shadow the new file.
        for k, v in values.items():
            os.environ[k] = v
        self._discord_cache = None
        return self.discord_status(refresh=True)

    # -- jobs ---------------------------------------------------------------

    def start_login(self) -> Job:
        from ..fetch import login

        def work(job: Job) -> dict:
            job.say("Opening TikTok in a browser window...")
            login(self.settings, wait=job.wait_for_user)
            job.say("Session saved.")
            return {}

        return self.jobs.start("login", work)

    def start_capture(self) -> Job:
        from ..fetch import capture

        def work(job: Job) -> dict:
            job.say("Opening TikTok in a browser window...")
            path = capture(self.settings, wait=job.wait_for_user)
            found = load_stickers(path)
            job.say(f"Captured {len(found)} favorited sticker(s).")
            if self.source is not None:
                self.source = path  # a fresh capture replaces a --from file
            return {"found": len(found), "file": path.name}

        return self.jobs.start("capture", work)

    def start_sync(self, ids: list[str], fmt: str, rotate: bool, dry_run: bool) -> Job:
        if fmt not in ("auto", "apng", "gif"):
            raise ValueError("format must be auto, apng or gif")
        wanted = set(ids)
        chosen = [s for s in self.stickers() if s.id in wanted]
        if not chosen:
            raise ValueError("Pick at least one sticker.")

        def work(job: Job) -> dict:
            report = run_sync(
                chosen,
                self.settings,
                fmt=fmt,  # type: ignore[arg-type]
                dry_run=dry_run,
                rotate=rotate,
                log=job.say,
            )
            self._discord_cache = None
            job.say(
                f"Done: {len(report.uploaded)} uploaded, {len(report.failed)} failed, "
                f"{len(report.waiting_for_slots)} waiting for slots."
            )
            return {
                "dry_run": dry_run,
                "uploaded": report.uploaded,
                "removed": report.removed,
                "failed": [{"name": n, "reason": r} for n, r in report.failed],
                "waiting": report.waiting_for_slots,
                "prepared": [
                    {
                        "id": p.sticker.id,
                        "short": p.sticker.short_id,
                        "name": p.name,
                        "ext": p.result.ext,
                        "kb": round(len(p.result.data) / 1000),
                        "frames": p.result.frames,
                        "strategy": p.result.strategy,
                        "animated": p.result.animated,
                    }
                    for p in report.prepared
                ],
            }

        return self.jobs.start("sync", work)

    def resume(self) -> None:
        job = self.jobs.current
        if job and job.status == "waiting":
            job._resume.set()


def sniff_type(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    try:  # anything else Pillow can read gets re-encoded as PNG for the browser
        from PIL import Image

        im = Image.open(BytesIO(data))
        return Image.MIME.get(im.format or "", "application/octet-stream")
    except Exception:
        return "application/octet-stream"


# --------------------------------------------------------------------------- HTTP


def make_handler(app: GuiApp, token: str, port_ref: list[int]):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"StickerSync/{__version__}"

        def log_message(self, *args: Any) -> None:  # keep the terminal quiet
            pass

        # -- helpers --------------------------------------------------------

        def _host_ok(self) -> bool:
            port = port_ref[0]
            return self.headers.get("Host", "") in (f"127.0.0.1:{port}", f"localhost:{port}")

        def _token_ok(self, query: dict) -> bool:
            given = self.headers.get("X-StickerSync-Token") or (query.get("t") or [""])[0]
            return secrets.compare_digest(given, token)

        def _send(self, status: int, body: bytes, ctype: str, cache: bool = False) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, status: int = 200) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 1_000_000:
                raise ValueError("request too large")
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw or b"{}")
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data

        # -- routes ---------------------------------------------------------

        def do_GET(self) -> None:
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "bad host")
            url = urlparse(self.path)
            query = parse_qs(url.query)
            path = url.path

            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path.removeprefix("/static/"))

            if not path.startswith("/api/") or not self._token_ok(query):
                return self._error(HTTPStatus.FORBIDDEN, "missing or bad token")
            try:
                if path == "/api/status":
                    return self._json(app.status(refresh="refresh" in query))
                if path == "/api/stickers":
                    return self._json({"stickers": app.sticker_list()})
                if path == "/api/job":
                    job = app.jobs.current
                    return self._json({"job": job.public() if job else None})
                if path.startswith("/api/original/"):
                    found = app.original_image(path.removeprefix("/api/original/"))
                    if not found:
                        return self._error(HTTPStatus.NOT_FOUND, "image unavailable")
                    return self._send(200, found[0], found[1], cache=True)
                if path.startswith("/api/converted/"):
                    found = app.converted_image(path.removeprefix("/api/converted/"))
                    if not found:
                        return self._error(HTTPStatus.NOT_FOUND, "not converted yet")
                    return self._send(200, found[0], found[1])
            except Exception as exc:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return self._error(HTTPStatus.NOT_FOUND, "no such endpoint")

        def do_POST(self) -> None:
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "bad host")
            url = urlparse(self.path)
            # POSTs must carry the header (not just ?t=), which a cross-site form can't set.
            if not secrets.compare_digest(self.headers.get("X-StickerSync-Token") or "", token):
                return self._error(HTTPStatus.FORBIDDEN, "missing or bad token")
            try:
                body = self._body()
                if url.path == "/api/login":
                    return self._json({"job": app.start_login().public()})
                if url.path == "/api/capture":
                    return self._json({"job": app.start_capture().public()})
                if url.path == "/api/sync":
                    job = app.start_sync(
                        ids=[str(i) for i in body.get("ids", [])],
                        fmt=str(body.get("format", "auto")),
                        rotate=bool(body.get("rotate")),
                        dry_run=bool(body.get("dry_run")),
                    )
                    return self._json({"job": job.public()})
                if url.path == "/api/job/continue":
                    app.resume()
                    return self._json({"ok": True})
                if url.path == "/api/settings":
                    discord = app.save_settings(body.get("discord_token"), body.get("guild_id"))
                    return self._json({"discord": discord})
            except (ValueError, RuntimeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
            return self._error(HTTPStatus.NOT_FOUND, "no such endpoint")

        def _static(self, name: str) -> None:
            name = name.strip("/")
            if not name or ".." in name or "\\" in name:
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            target = STATIC / name
            if not target.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in ("application/javascript",):
                ctype += "; charset=utf-8"
            self._send(200, target.read_bytes(), ctype)

    return Handler


def serve(app: GuiApp, port: int = 0) -> tuple[ThreadingHTTPServer, str]:
    """Start the server in a background thread; returns (server, launch URL)."""
    token = secrets.token_urlsafe(24)
    port_ref = [port]
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, token, port_ref))
    port_ref[0] = server.server_address[1]
    threading.Thread(target=server.serve_forever, name="stickersync-gui", daemon=True).start()
    return server, f"http://127.0.0.1:{port_ref[0]}/#t={token}"


def run_gui(source: Path | None = None, port: int = 0, window: bool = True) -> None:
    app = GuiApp(source)
    server, url = serve(app, port)
    if window:
        try:
            import webview  # pywebview: optional, gives a real app window

            webview.create_window("StickerSync", url, width=1240, height=880, min_size=(420, 600))
            webview.start()
            server.shutdown()
            return
        except ImportError:
            pass
    import webbrowser

    print(f"StickerSync is running at {url}\nPress Ctrl+C to quit.")
    webbrowser.open(url)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
