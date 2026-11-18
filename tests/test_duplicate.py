"""Duplicate detection tests on synthetic images (no database, no network)."""

from __future__ import annotations

import io
import random

import pytest
from PIL import Image, ImageDraw, ImageEnhance

from app.services.duplicate import ReferenceImage, UploadedImage, check_duplicates
from app.services.hashing import fingerprint

# Use explicit thresholds so the tests do not depend on the developer's .env.
NEAR, POSSIBLE = 6, 12


def make_scene(seed: int, size: tuple[int, int] = (640, 480)) -> Image.Image:
    """A synthetic 'photo': gradient sky and road plus random coloured blocks. Deterministic per seed."""
    rng = random.Random(seed)
    w, h = size
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        draw.line([(0, y), (w, y)], fill=(int(90 + 80 * t), int(140 + 60 * t), int(220 - 90 * t)))
    for _ in range(14):
        x0, y0 = rng.randint(0, w - 120), rng.randint(0, h - 120)
        x1, y1 = x0 + rng.randint(40, 220), y0 + rng.randint(30, 160)
        colour = tuple(rng.randint(0, 255) for _ in range(3))
        if rng.random() < 0.5:
            draw.rectangle([x0, y0, x1, y1], fill=colour)
        else:
            draw.ellipse([x0, y0, x1, y1], fill=colour)
    return img


def to_jpeg_bytes(img: Image.Image, quality: int = 92) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def from_bytes(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


@pytest.fixture
def original() -> bytes:
    return to_jpeg_bytes(make_scene(seed=1))


def _fp(data: bytes):
    return fingerprint(data)


def test_exact_duplicate_is_detected(original: bytes) -> None:
    """Byte-identical file: EXACT match against the reference set, verdict DUPLICATE."""
    refs = [ReferenceImage(claim_id="CLM-2026-0001", image_index=1, fingerprint=_fp(original))]
    result = check_duplicates("a1", [UploadedImage(1, _fp(original))], refs, near=NEAR, possible=POSSIBLE)

    assert result.verdict == "DUPLICATE"
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.match_type == "EXACT"
    assert match.hamming_distance == 0
    assert match.matched_claim_id == "CLM-2026-0001"
    assert match.matched_source == "EXISTING_CLAIM"
    assert match.matched_image_url == "/api/claims/CLM-2026-0001/images/1"


def test_resized_and_recompressed_duplicate_is_detected(original: bytes) -> None:
    """Smaller, lower-quality JPEG of the same scene should still be flagged (NEAR or POSSIBLE)."""
    img = from_bytes(original).resize((400, 300))
    variant = to_jpeg_bytes(img, quality=55)
    result = check_duplicates(
        "a2", [UploadedImage(1, _fp(variant))],
        [], near=NEAR, possible=POSSIBLE,
    )
    assert result.verdict == "UNIQUE"  # no refs yet: sanity check that the empty set is UNIQUE

    refs = [ReferenceImage("CLM-2026-0001", 1, _fp(original))]
    result = check_duplicates("a2", [UploadedImage(1, _fp(variant))], refs, near=NEAR, possible=POSSIBLE)

    # Measured: phash distance 0, dhash distance 1 -> NEAR -> DUPLICATE.
    assert result.verdict == "DUPLICATE"
    assert result.matches[0].match_type == "NEAR"
    assert result.matches[0].hamming_distance <= NEAR


def test_brightness_adjusted_duplicate_is_detected(original: bytes) -> None:
    """Brighter copy of the same scene should be flagged: the perceptual hashes are robust to brightness."""
    brighter = to_jpeg_bytes(ImageEnhance.Brightness(from_bytes(original)).enhance(1.25))
    refs = [ReferenceImage("CLM-2026-0001", 1, _fp(original))]
    result = check_duplicates("a3", [UploadedImage(1, _fp(brighter))], refs, near=NEAR, possible=POSSIBLE)

    # Measured: phash distance 8 (above NEAR=6) -> POSSIBLE band only -> POSSIBLE_DUPLICATE.
    assert result.verdict == "POSSIBLE_DUPLICATE"
    assert result.matches[0].match_type == "POSSIBLE"


def test_unrelated_image_is_unique(original: bytes) -> None:
    """A different scene should not match anything."""
    unrelated = to_jpeg_bytes(make_scene(seed=999))
    refs = [ReferenceImage("CLM-2026-0001", 1, _fp(original))]
    result = check_duplicates("a4", [UploadedImage(1, _fp(unrelated))], refs, near=NEAR, possible=POSSIBLE)

    assert result.verdict == "UNIQUE"
    assert result.matches == []


def test_two_images_in_one_submission_are_compared(original: bytes) -> None:
    """Image 2 being a copy of image 1 in the same submission is flagged as OTHER_UPLOADED_IMAGE."""
    copy = to_jpeg_bytes(from_bytes(original).resize((320, 240)), quality=70)
    uploads = [UploadedImage(1, _fp(original)), UploadedImage(2, _fp(copy))]
    result = check_duplicates("a5", uploads, [], near=NEAR, possible=POSSIBLE)

    assert result.verdict in {"DUPLICATE", "POSSIBLE_DUPLICATE"}
    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.image_index == 2
    assert match.matched_source == "OTHER_UPLOADED_IMAGE"
    assert match.matched_claim_id is None
    assert match.matched_image_url == "/api/analyses/a5/images/1"
