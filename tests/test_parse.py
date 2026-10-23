import base64
import json

from stickersync.parse import load_stickers, stickers_from_json

SAMPLE = {
    "status_code": 0,
    "sticker_list": [
        {
            "id": "7001",
            "display_name": "cat dance",
            "static_url": {"uri": "s/7001", "url_list": ["https://cdn.example/7001.png"]},
            "animate_url": {"uri": "a/7001", "url_list": ["https://cdn.example/7001.webp"]},
        },
        {
            "id_str": "7002",
            "static_url": {"uri": "s/7002", "url_list": ["https://cdn.example/7002.png"]},
        },
    ],
    "author": {
        "uid": "99",
        "avatar_thumb": {"uri": "av", "url_list": ["https://cdn.example/avatar.jpg"]},
    },
}


def test_finds_stickers_and_ignores_avatars():
    stickers = {s.id: s for s in stickers_from_json(SAMPLE)}
    assert set(stickers) == {"7001", "7002"}
    cat = stickers["7001"]
    assert cat.name == "cat dance"
    assert cat.urls[0].endswith(".webp")  # animated variant first
    assert cat.animated_hint is True


def test_falls_back_to_uri_hash_for_id():
    data = {"x": {"img": {"uri": "abc", "url_list": ["https://cdn.example/a.webp"]}}}
    (s,) = stickers_from_json(data)
    assert s.id.startswith("u")


def test_loads_har(tmp_path):
    body = base64.b64encode(json.dumps(SAMPLE).encode()).decode()
    har = {
        "log": {
            "entries": [
                {"response": {"content": {"text": body, "encoding": "base64"}}},
                {"response": {"content": {"text": "<html>not json</html>"}}},
            ]
        }
    }
    path = tmp_path / "capture.har"
    path.write_text(json.dumps(har))
    assert {s.id for s in load_stickers(path)} == {"7001", "7002"}


def test_loads_stickersync_capture(tmp_path):
    cap = {"format": "stickersync-capture", "responses": [{"url": "u", "body": SAMPLE}]}
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cap))
    assert len(load_stickers(path)) == 2


def test_loads_url_list(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("https://cdn.example/a.webp\n# comment\n\nhttps://cdn.example/b.gif?x=1\n")
    stickers = load_stickers(path)
    assert [s.urls[0] for s in stickers] == [
        "https://cdn.example/a.webp",
        "https://cdn.example/b.gif?x=1",
    ]
