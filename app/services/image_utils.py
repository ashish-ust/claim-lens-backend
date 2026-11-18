"""Image decoding, format checks and preparation of images for Claude."""

from __future__ import annotations

import base64
import io

from PIL import Image, ImageOps, UnidentifiedImageError

# Pillow format name -> (file extension, MIME type). Only these are accepted.
ALLOWED_FORMATS: dict[str, tuple[str, str]] = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}

CLAUDE_MAX_SIDE = 1568  # Longest side sent to Claude; larger images are downscaled.


class InvalidImageError(ValueError):
    """Raised when bytes are not a readable jpeg/png/webp image."""


def detect_format(data: bytes) -> tuple[str, str]:
    """Return (extension, mime) based on the actual bytes, not the upload's content-type header."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format or ""
    except (UnidentifiedImageError, OSError) as exc:
        raise InvalidImageError("File is not a readable image") from exc
    if fmt not in ALLOWED_FORMATS:
        raise InvalidImageError("Only jpeg, png and webp images are accepted")
    return ALLOWED_FORMATS[fmt]


def open_oriented(data: bytes) -> Image.Image:
    """Decode the image and apply the EXIF orientation tag so pixels are upright."""
    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img)


def prepare_for_claude(data: bytes) -> str:
    """Return base64 JPEG (longest side <= 1568 px) for an Anthropic image block."""
    img = open_oriented(data).convert("RGB")
    img.thumbnail((CLAUDE_MAX_SIDE, CLAUDE_MAX_SIDE))  # keeps aspect ratio, only shrinks
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode("ascii")
