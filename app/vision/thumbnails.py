"""Small face-crop thumbnails (data URIs) for the UI."""

from __future__ import annotations

import base64
import io

from PIL import Image


def face_thumbnail(image: Image.Image, x: int, y: int, w: int, h: int, size: int = 96) -> str:
    margin = int(max(w, h) * 0.25)
    box = (
        max(0, x - margin),
        max(0, y - margin),
        min(image.width, x + w + margin),
        min(image.height, y + h + margin),
    )
    crop = image.crop(box) if box[2] > box[0] and box[3] > box[1] else image.copy()
    crop.thumbnail((size, size), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    crop.convert("RGB").save(buf, format="JPEG", quality=78)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
