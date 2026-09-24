"""Strict validation + normalisation of untrusted image bytes.

Used for uploaded reference photos *and* downloaded candidate images.
The detected format comes from the file contents (Pillow's decoder), never
from the filename or a client-supplied Content-Type.
"""

from __future__ import annotations

import hashlib
import io
import warnings
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class ImageValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ValidatedImage:
    image: Image.Image  # RGB, EXIF orientation applied
    mime: str
    original_sha256: str
    size_bytes: int
    width: int
    height: int

    def to_jpeg(self, max_side: int | None = None, quality: int = 92) -> bytes:
        """Re-encode as JPEG. Drops all metadata (EXIF/GPS) by construction."""
        img = self.image
        if max_side and max(img.size) > max_side:
            img = img.copy()
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


def validate_image_bytes(
    data: bytes,
    *,
    max_bytes: int,
    max_pixels: int = 40_000_000,
    min_dimension: int = 64,
    max_dimension: int = 12_000,
) -> ValidatedImage:
    if not data:
        raise ImageValidationError("empty", "The file is empty.")
    if len(data) > max_bytes:
        raise ImageValidationError("too_large", f"Image exceeds the {max_bytes // (1024 * 1024)} MB limit.")

    with warnings.catch_warnings():
        # Treat decompression-bomb warnings as hard errors.
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        previous_limit = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = max_pixels
        try:
            try:
                probe = Image.open(io.BytesIO(data))
                fmt = probe.format
                width, height = probe.size
                probe.verify()  # structural check, invalidates `probe`
            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise ImageValidationError("decompression_bomb", "Image dimensions are too large.") from exc
            except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
                raise ImageValidationError("invalid", "The file is not a readable image.") from exc

            if fmt not in ALLOWED_FORMATS:
                raise ImageValidationError("unsupported_format", "Only JPEG, PNG and WebP images are supported.")
            if width * height > max_pixels:
                raise ImageValidationError("decompression_bomb", "Image dimensions are too large.")
            if min(width, height) < min_dimension:
                raise ImageValidationError("too_small", f"Image must be at least {min_dimension}px on each side.")
            if max(width, height) > max_dimension:
                raise ImageValidationError("too_big_dimensions", f"Image sides must be ≤ {max_dimension}px.")

            try:
                opened = Image.open(io.BytesIO(data))
                if getattr(opened, "n_frames", 1) > 1:
                    opened.seek(0)
                opened.load()
                img: Image.Image = _to_rgb(ImageOps.exif_transpose(opened) or opened)
            except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                raise ImageValidationError("decompression_bomb", "Image dimensions are too large.") from exc
            except (OSError, SyntaxError, ValueError) as exc:
                raise ImageValidationError("corrupted", "The image data is corrupted or truncated.") from exc
        finally:
            Image.MAX_IMAGE_PIXELS = previous_limit

    return ValidatedImage(
        image=img,
        mime=ALLOWED_FORMATS[fmt],
        original_sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        width=img.width,
        height=img.height,
    )


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode == "RGB":
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")
