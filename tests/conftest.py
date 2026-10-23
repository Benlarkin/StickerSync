from __future__ import annotations

import random
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from stickersync.config import Settings


def animated_webp(
    n: int = 30, size=(400, 300), frame_ms: int = 50, noise: int = 0, seed: int = 0
) -> bytes:
    rng = random.Random(seed)
    frames = []
    for i in range(n):
        im = Image.new("RGBA", size, (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.ellipse((20 + i * 4, 40, 180 + i * 4, 260), fill=(255, 80 + i % 100, 40, 255))
        for _ in range(noise):
            xy = (rng.randrange(size[0]), rng.randrange(size[1]))
            d.point(xy, fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255))
        frames.append(im)
    buf = BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], duration=frame_ms, loop=0)
    return buf.getvalue()


def static_png(size=(600, 600), noise: int = 0) -> bytes:
    rng = random.Random(1)
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((50, 50, size[0] - 50, size[1] - 50), fill=(30, 144, 255, 255))
    for _ in range(noise):
        xy = (rng.randrange(size[0]), rng.randrange(size[1]))
        d.point(xy, fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256), 255))
    buf = BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path / "home",
        discord_token="test-token",
        guild_id="123",
        default_tag="sparkles",
        description="test",
    )
