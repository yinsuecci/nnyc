"""Local privacy artwork only. Editing this file never changes display or audio state."""
import base64
import io
import json
import math
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

PAPER = (29, 41, 60)
MAX_FILE = 12 * 1024 * 1024


def read_photo(stream):
    data = stream.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError("图片超过 12 MiB")
    with Image.open(io.BytesIO(data), formats=["JPEG", "PNG"]) as image:
        if image.width * image.height > 20_000_000 or getattr(image, "n_frames", 1) != 1:
            raise ValueError("仅支持不超过 2000 万像素的静态图片")
        oriented = ImageOps.exif_transpose(image)
        oriented.thumbnail((2560, 2560), Image.Resampling.LANCZOS)
        rgba = oriented.convert("RGBA")
        clean = Image.new("RGB", rgba.size, PAPER)
        clean.paste(rgba, mask=rgba.getchannel("A"))
        return clean


def framing(mode, x, y, zoom):
    if mode not in ("fill", "fit") or not all(type(v) in (int, float) and math.isfinite(v)
            for v in (x, y, zoom)) or not (0 <= x <= 1 and 0 <= y <= 1 and 1 <= zoom <= 3):
        raise ValueError("无效的图片位置或缩放")
    return mode, x, y, zoom


def render(image, size, mode="fill", x=.5, y=.5, zoom=1):
    framing(mode, x, y, zoom)
    if image is None:
        return Image.new("RGB", size, PAPER)
    if mode == "fit":
        return ImageOps.pad(image, size, method=Image.Resampling.LANCZOS, color=PAPER)
    w, h = image.size
    ratio = min(w / size[0], h / size[1]) / zoom
    cw, ch = size[0] * ratio, size[1] * ratio
    left, top = (w - cw) * x, (h - ch) * y
    return image.resize(size, Image.Resampling.LANCZOS, box=(
        max(0, left), max(0, top), min(w, left + cw), min(h, top + ch)))


def save(path, image, mode="fill", x=.5, y=.5, zoom=1):
    values = framing(mode, x, y, zoom)
    if image is None:
        path.unlink(missing_ok=True)
        return
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    payload = {"version": 1, "framing": values, "jpeg": base64.b64encode(buffer.getvalue()).decode("ascii")}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="ascii") as out:
            tmp = Path(out.name)
            json.dump(payload, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def load(path):
    try:
        with path.open("rb") as f:
            raw = f.read(MAX_FILE + 1)
    except FileNotFoundError:
        return None, ("fill", .5, .5, 1)
    if len(raw) > MAX_FILE:
        raise ValueError("图片设置过大")
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("无法读取图片设置")
    values = framing(*data["framing"])
    image = read_photo(io.BytesIO(base64.b64decode(data["jpeg"], validate=True)))
    return image, values
