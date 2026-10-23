from __future__ import annotations

import http.client
import json
import time
from urllib.parse import urlparse

import pytest

from stickersync.discord_api import GuildSticker
from stickersync.gui import server as gui_server
from stickersync.gui.server import GuiApp, serve

from .conftest import animated_webp

SAMPLE = {
    "sticker_list": [
        {
            "id": "7001",
            "display_name": "cat dance",
            "animate_url": {"uri": "a1", "url_list": ["https://cdn.example/7001.webp"]},
        },
        {
            "id": "7002",
            "display_name": "side eye",
            "animate_url": {"uri": "a2", "url_list": ["https://cdn.example/7002.webp"]},
        },
    ]
}


class FakeClient:
    def __init__(self, token):
        self.token = token

    def guild_info(self, guild_id):
        return {"name": "Sticker Lounge", "premium_tier": 2}

    def list_stickers(self, guild_id):
        return [GuildSticker("1", "old", "x", 1, True)]

    def slot_limit(self, guild_id):
        return 30


@pytest.fixture
def running(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("STICKERSYNC_HOME", str(home))
    # Set (not delete) so monkeypatch restores whatever save_settings writes.
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "")
    monkeypatch.setenv("DISCORD_GUILD_ID", "")
    payload = animated_webp(n=8)
    monkeypatch.setattr(gui_server, "download", lambda sticker, cache_dir: payload)
    monkeypatch.setattr("stickersync.sync.download", lambda sticker, cache_dir: payload)

    source = tmp_path / "capture.json"
    source.write_text(json.dumps(SAMPLE))
    app = GuiApp(source=source, client_factory=FakeClient)
    srv, url = serve(app)
    parsed = urlparse(url)
    token = parsed.fragment.split("=", 1)[1]
    yield {"port": parsed.port, "token": token, "app": app, "home": home}
    srv.shutdown()


def call(r, method, path, body=None, token=True, host=None, header=True):
    conn = http.client.HTTPConnection("127.0.0.1", r["port"], timeout=10)
    headers = {"Host": host or f"127.0.0.1:{r['port']}"}
    if token and header:
        headers["X-StickerSync-Token"] = r["token"]
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=data, headers=headers)
    resp = conn.getresponse()
    raw = resp.read()
    ctype = resp.getheader("Content-Type", "")
    conn.close()
    return resp.status, (json.loads(raw) if "json" in ctype else raw), ctype


def wait_job(r):
    for _ in range(200):
        _, data, _ = call(r, "GET", "/api/job")
        if data["job"]["status"] in ("done", "error"):
            return data["job"]
        time.sleep(0.05)
    raise AssertionError("job never finished")


def test_serves_page_and_static_files(running):
    status, body, ctype = call(running, "GET", "/", token=False)
    assert status == 200 and b"StickerSync" in body and ctype.startswith("text/html")
    status, _, ctype = call(running, "GET", "/static/app.js", token=False)
    assert status == 200 and "javascript" in ctype
    assert call(running, "GET", "/static/../server.py", token=False)[0] == 404


def test_api_requires_token_and_local_host(running):
    assert call(running, "GET", "/api/status", token=False)[0] == 403
    assert call(running, "GET", "/api/status", host="evil.example")[0] == 403
    assert call(running, "GET", "/api/status")[0] == 200
    # POSTs need the header; a ?t= query (what a cross-site form could send) isn't enough.
    path = f"/api/sync?t={running['token']}"
    assert call(running, "POST", path, body={}, header=False)[0] == 403


def test_lists_stickers_and_serves_thumbnails(running):
    _, data, _ = call(running, "GET", "/api/stickers")
    names = [s["name"] for s in data["stickers"]]
    assert names == ["cat dance", "side eye"]
    assert all(s["status"] == "new" for s in data["stickers"])
    status, body, ctype = call(
        running, "GET", f"/api/original/7001?t={running['token']}", header=False
    )
    assert status == 200 and ctype == "image/webp" and body[:4] == b"RIFF"
    assert call(running, "GET", "/api/original/nope")[0] == 404


def test_dry_run_job_produces_previews(running):
    status, _, _ = call(
        running, "POST", "/api/sync", body={"ids": ["7001"], "format": "gif", "dry_run": True}
    )
    assert status == 200
    job = wait_job(running)
    assert job["status"] == "done", job
    (prepared,) = job["result"]["prepared"]
    assert prepared["ext"] == "gif" and prepared["kb"] <= 512
    status, body, ctype = call(running, "GET", f"/api/converted/{prepared['short']}")
    assert status == 200 and ctype == "image/gif" and body[:3] == b"GIF"


def test_sync_rejects_empty_selection(running):
    status, data, _ = call(running, "POST", "/api/sync", body={"ids": []})
    assert status == 400 and "Pick" in data["error"]


def test_settings_are_saved_privately_and_used(running):
    _, before, _ = call(running, "GET", "/api/status")
    assert before["discord"]["configured"] is False
    status, data, _ = call(
        running, "POST", "/api/settings", body={"discord_token": "abc", "guild_id": "12345"}
    )
    assert status == 200
    assert data["discord"]["guild_name"] == "Sticker Lounge"
    assert data["discord"]["slots_used"] == 1 and data["discord"]["slot_limit"] == 30
    cfg = running["home"] / "config.env"
    assert "DISCORD_GUILD_ID=12345" in cfg.read_text()
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"
    bad = call(running, "POST", "/api/settings", body={"guild_id": "not a number"})
    assert bad[0] == 400


def test_waiting_job_resumes():
    from stickersync.gui.server import Job

    job = Job(1, "capture")
    import threading

    t = threading.Thread(
        target=job.wait_for_user, args=("Do the thing.\nThen press Enter here... ",)
    )
    t.start()
    for _ in range(100):
        if job.status == "waiting":
            break
        time.sleep(0.01)
    assert job.status == "waiting" and job.prompt == "Do the thing."
    job._resume.set()
    t.join(2)
    assert job.status == "running" and job.prompt is None
