"""Turn any TikTok sticker image into something Discord will accept.

Discord's sticker rules (API): PNG, APNG or GIF; exactly 320x320; at most 512 KB;
animations at most 5 seconds. TikTok stickers are usually (animated) WebP, which
Pillow decodes natively, so no ffmpeg is needed.

Animated stickers go through a "compression ladder": each rung is a cheaper
encoding (fewer colors, then fewer frames) until the file fits under the limit.
Smoothness is preserved for as long as possible -- colors are reduced before
frames are dropped.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, ImageChops, ImageSequence

SIZE = 320
MAX_BYTES = 512_000  # Discord says 512 KB; stay on the safe side of 512 * 1000.
MAX_DURATION_MS = 5_000
MIN_FRAME_MS = 20  # Browsers treat GIF delays under 20 ms as 100 ms.
DEFAULT_FRAME_MS = 100

Format = Literal["auto", "apng", "gif"]
Frame = tuple[Image.Image, int]  # (RGBA image, duration in ms)


class ConvertError(RuntimeError):
    """Raised when a sticker can't be made to fit Discord's limits."""


@dataclass
class ConvertResult:
    data: bytes
    ext: Literal["png", "gif"]
    animated: bool
    frames: int
    duration_ms: int
    strategy: str

    @property
    def content_type(self) -> str:
        return "image/gif" if self.ext == "gif" else "image/png"


# --------------------------------------------------------------------------- frames


def load_frames(data: bytes) -> list[Frame]:
    """Decode every frame of an image as full-canvas RGBA."""
    try:
        im = Image.open(BytesIO(data))
        im.load()
    except Exception as exc:  # Pillow raises a zoo of exception types
        raise ConvertError(f"not a readable image: {exc}") from exc
    frames: list[Frame] = []
    for frame in ImageSequence.Iterator(im):
        duration = int(frame.info.get("duration") or im.info.get("duration") or DEFAULT_FRAME_MS)
        frames.append((frame.convert("RGBA").copy(), max(duration, 1)))
    if not frames:
        raise ConvertError("image has no frames")
    return frames


def fit_frames(frames: list[Frame], size: int = SIZE) -> list[Frame]:
    """Trim transparent borders, scale to fit size x size, and center.

    All frames share one crop box so the animation doesn't jitter.
    """
    boxes = [f.getbbox() for f, _ in frames]
    boxes = [b for b in boxes if b]
    if not boxes:
        return [(Image.new("RGBA", (size, size), (0, 0, 0, 0)), d) for _, d in frames]
    union = (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )
    out = []
    for img, dur in frames:
        cropped = img.crop(union)
        w, h = cropped.size
        scale = size / max(w, h)
        new = (max(1, round(w * scale)), max(1, round(h * scale)))
        resized = cropped.resize(new, Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.paste(resized, ((size - new[0]) // 2, (size - new[1]) // 2))
        out.append((canvas, dur))
    return out


def trim_duration(frames: list[Frame], max_ms: int = MAX_DURATION_MS) -> list[Frame]:
    out: list[Frame] = []
    total = 0
    for img, dur in frames:
        if total >= max_ms:
            break
        dur = min(dur, max_ms - total)
        out.append((img, dur))
        total += dur
    return out


def decimate(frames: list[Frame], step: int) -> list[Frame]:
    """Keep every `step`-th frame, folding dropped frames' time into the kept one."""
    if step <= 1:
        return list(frames)
    out: list[Frame] = []
    for i in range(0, len(frames), step):
        chunk = frames[i : i + step]
        out.append((chunk[0][0], sum(d for _, d in chunk)))
    return out


def merge_short_frames(frames: list[Frame], min_ms: int = MIN_FRAME_MS) -> list[Frame]:
    out: list[Frame] = []
    for img, dur in frames:
        if out and out[-1][1] < min_ms:
            prev_img, prev_dur = out[-1]
            out[-1] = (prev_img, prev_dur + dur)
        else:
            out.append((img, dur))
    if len(out) > 1 and out[-1][1] < min_ms:
        img, dur = out.pop()
        out[-1] = (out[-1][0], out[-1][1] + dur)
    return out


def drop_duplicate_frames(frames: list[Frame]) -> list[Frame]:
    out: list[Frame] = []
    for img, dur in frames:
        if out and ImageChops.difference(out[-1][0], img).getbbox() is None:
            out[-1] = (out[-1][0], out[-1][1] + dur)
        else:
            out.append((img, dur))
    return out


# --------------------------------------------------------------------------- encoders


def _shared_palette(imgs: list[Image.Image], colors: int) -> list[Image.Image]:
    """Quantize all frames to ONE palette (with alpha).

    APNG stores a single palette for the whole animation, so quantizing frames
    one at a time would scramble colors. Stacking the frames into one tall image,
    quantizing once, and slicing it back apart guarantees they share a palette.
    """
    w, h = imgs[0].size
    montage = Image.new("RGBA", (w, h * len(imgs)))
    for i, img in enumerate(imgs):
        montage.paste(img, (0, h * i))
    quantized = montage.quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
    return [quantized.crop((0, h * i, w, h * (i + 1))) for i in range(len(imgs))]


def encode_png(img: Image.Image, colors: int | None = None) -> bytes:
    buf = BytesIO()
    out = img if colors is None else img.quantize(colors, method=Image.Quantize.FASTOCTREE)
    out.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def encode_apng(frames: list[Frame], colors: int | None = None) -> bytes:
    imgs = [img for img, _ in frames]
    if colors is not None:
        imgs = _shared_palette(imgs, colors)
    buf = BytesIO()
    imgs[0].save(
        buf,
        "PNG",
        save_all=True,
        append_images=imgs[1:],
        duration=[d for _, d in frames],
        loop=0,
        disposal=0,  # APNG_DISPOSE_OP_NONE
        blend=0,  # APNG_BLEND_OP_SOURCE: each frame fully replaces its region
        optimize=True,
    )
    return buf.getvalue()


def _to_gif_palette(img: Image.Image, colors: int) -> tuple[Image.Image, int]:
    """RGBA -> P with one reserved fully transparent index (GIF has 1-bit alpha)."""
    colors = max(2, min(colors, 256))
    transparent_index = colors - 1
    alpha = img.getchannel("A")
    rgb = Image.new("RGB", img.size, (0, 0, 0))
    rgb.paste(img, mask=alpha)
    pal_img = rgb.quantize(colors=colors - 1, method=Image.Quantize.FASTOCTREE)
    palette = (pal_img.getpalette() or [])[: 3 * (colors - 1)]
    palette += [0] * (3 * (colors - 1) - len(palette))
    palette += [255, 0, 255]  # the transparent slot; color never shows
    pal_img.putpalette(palette)
    mask = alpha.point(lambda a: 255 if a < 128 else 0)
    pal_img.paste(transparent_index, mask=mask)
    return pal_img, transparent_index


def encode_gif(frames: list[Frame], colors: int = 256) -> bytes:
    converted = [_to_gif_palette(img, colors) for img, _ in frames]
    imgs = [c[0] for c in converted]
    buf = BytesIO()
    imgs[0].save(
        buf,
        "GIF",
        save_all=True,
        append_images=imgs[1:],
        duration=[max(d, MIN_FRAME_MS) for _, d in frames],
        loop=0,
        disposal=2,  # restore to background so transparent areas don't smear
        transparency=converted[0][1],
        optimize=False,  # optimizing remaps palettes and can break the transparent index
    )
    return buf.getvalue()


# --------------------------------------------------------------------------- ladder


FRAME_STEPS = (1, 2, 3, 4, 6, 8)
# Fewer colors rarely shrinks a file by more than ~40%, so if a rung is this far
# over the limit, skip straight to dropping frames instead of trying more palettes.
HOPELESS_RATIO = 1.6


def _rungs(fmt: Format) -> Iterator[tuple[str, int | None]]:
    """Yield (format, colors) for one frame step, best quality first."""
    if fmt in ("auto", "apng"):
        for colors in (None, 256, 128, 64):
            yield "apng", colors
    if fmt in ("auto", "gif"):
        for colors in (256, 128, 64):
            yield "gif", colors


def convert(data: bytes, fmt: Format = "auto", max_bytes: int = MAX_BYTES) -> ConvertResult:
    """Convert raw image bytes into a Discord-ready sticker."""
    frames = load_frames(data)
    frames = fit_frames(frames)
    frames = drop_duplicate_frames(frames)

    if len(frames) == 1:
        img = frames[0][0]
        for colors in (None, 256, 128, 64):
            out = encode_png(img, colors)
            if len(out) <= max_bytes:
                label = "png" if colors is None else f"png/{colors}c"
                return ConvertResult(out, "png", False, 1, 0, label)
        raise ConvertError("static image still over the size limit at 64 colors")

    frames = merge_short_frames(trim_duration(frames))
    for step in FRAME_STEPS:
        candidate = merge_short_frames(decimate(frames, step))
        if len(candidate) < 2:
            break
        hopeless: set[str] = set()
        for kind, colors in _rungs(fmt):
            if kind in hopeless:
                continue
            if kind == "apng":
                out = encode_apng(candidate, colors)
            else:
                out = encode_gif(candidate, colors or 256)
            if len(out) <= max_bytes:
                label = f"{kind}/step{step}/{colors or 'full'}c"
                ext: Literal["png", "gif"] = "png" if kind == "apng" else "gif"
                total = sum(d for _, d in candidate)
                return ConvertResult(out, ext, True, len(candidate), total, label)
            if len(out) > max_bytes * HOPELESS_RATIO:
                hopeless.add(kind)
    raise ConvertError("animation still over the size limit after every compression step")
