from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from stickersync.convert import (
    MAX_BYTES,
    MAX_DURATION_MS,
    ConvertError,
    convert,
    decimate,
    encode_apng,
    load_frames,
    merge_short_frames,
    trim_duration,
)

from .conftest import animated_webp, static_png


def _open(data: bytes) -> Image.Image:
    return Image.open(BytesIO(data))


def test_static_becomes_320_png():
    result = convert(static_png())
    im = _open(result.data)
    assert result.ext == "png" and not result.animated
    assert im.format == "PNG" and im.size == (320, 320)
    assert len(result.data) <= MAX_BYTES


def test_animated_webp_becomes_animated_sticker():
    result = convert(animated_webp())
    im = _open(result.data)
    assert result.animated
    assert im.size == (320, 320)
    assert getattr(im, "n_frames", 1) > 1
    assert len(result.data) <= MAX_BYTES


def test_noisy_animation_is_squeezed_under_limit():
    result = convert(animated_webp(n=60, noise=3000, seed=3))
    assert len(result.data) <= MAX_BYTES
    assert result.animated


def test_long_animation_trimmed_to_five_seconds():
    result = convert(animated_webp(n=100, frame_ms=100))  # 10 s source
    assert result.duration_ms <= MAX_DURATION_MS


@pytest.mark.parametrize("fmt, expected", [("gif", "GIF"), ("apng", "PNG")])
def test_format_choice(fmt, expected):
    result = convert(animated_webp(), fmt)
    assert _open(result.data).format == expected


def test_gif_keeps_transparency():
    result = convert(animated_webp(), "gif")
    frames = load_frames(result.data)
    # The corner is transparent in the source, so it must stay transparent.
    assert frames[0][0].getpixel((0, 0))[3] == 0
    assert frames[-1][0].getpixel((0, 0))[3] == 0


def test_apng_shared_palette_keeps_each_frames_colors():
    colors = [(255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255)]
    frames = []
    for c in colors:
        im = Image.new("RGBA", (320, 320), (0, 0, 0, 0))
        ImageDraw.Draw(im).rectangle((20, 20, 300, 300), fill=c)
        frames.append((im, 100))
    decoded = load_frames(encode_apng(frames, colors=64))
    assert [f.getpixel((50, 50)) for f, _ in decoded] == colors


def test_identical_frames_collapse_to_static():
    im = Image.new("RGBA", (200, 200), (255, 0, 0, 255))
    buf = BytesIO()
    im.save(buf, "GIF", save_all=True, append_images=[im.copy(), im.copy()], duration=100)
    result = convert(buf.getvalue())
    assert not result.animated and result.ext == "png"


def test_garbage_input_raises():
    with pytest.raises(ConvertError):
        convert(b"definitely not an image")


def test_frame_helpers():
    img = Image.new("RGBA", (4, 4))
    frames = [(img, 10)] * 10
    assert sum(d for _, d in decimate(frames, 3)) == 100
    assert len(decimate(frames, 3)) == 4
    assert all(d >= 20 for _, d in merge_short_frames(frames))
    assert sum(d for _, d in trim_duration([(img, 3000)] * 3)) == MAX_DURATION_MS
