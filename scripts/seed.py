"""Seed the reference set with pre-approved claims from sample_images/.

Layout: one folder per claim pair, named after the pair. Up to two images per folder, plus an
optional narrative.txt. Example:
    sample_images/pair-01-rear-end/front.jpg
    sample_images/pair-01-rear-end/rear.jpg
    sample_images/pair-01-rear-end/narrative.txt

Each pair goes through the same analysis as a real upload, then is approved. Re-running skips pairs
already seeded (matched by reviewer_note "seed:<folder name>").

Run from the project root:  python -m scripts.seed
Note: with MOCK_MODE=false this makes Claude calls for every pair that is not yet seeded.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import PROJECT_DIR
from app.db import init_db, transaction
from app.services import storage
from app.services.image_utils import InvalidImageError, detect_format
from app.services.pipeline import IncomingImage, run_analysis

SAMPLE_DIR = PROJECT_DIR / "sample_images"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_NARRATIVE = "Pre-approved sample claim seeded from the sample_images folder; no narrative was supplied."


def _already_seeded(marker: str) -> bool:
    with transaction() as conn:
        row = conn.execute("SELECT 1 FROM claims WHERE reviewer_note = ?", (marker,)).fetchone()
    return row is not None


def _load_pair(folder: Path) -> tuple[str, list[IncomingImage]]:
    """Read narrative and up to two images from one pair folder."""
    narrative_file = folder / "narrative.txt"
    narrative = narrative_file.read_text(encoding="utf-8").strip() if narrative_file.exists() else ""
    if not 20 <= len(narrative) <= 3000:
        narrative = DEFAULT_NARRATIVE

    paths = sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)[:2]
    images = []
    for path in paths:
        data = path.read_bytes()
        ext, _mime = detect_format(data)  # raises InvalidImageError for non-image bytes
        images.append(IncomingImage(filename=path.name, ext=ext, data=data))
    return narrative, images


async def seed() -> int:
    """Seed every pair folder. Returns the number of claims approved."""
    init_db()
    if not SAMPLE_DIR.is_dir():
        print(f"No sample folder at {SAMPLE_DIR}; nothing to seed.")
        return 0

    approved = 0
    for folder in sorted(p for p in SAMPLE_DIR.iterdir() if p.is_dir()):
        marker = f"seed:{folder.name}"
        if _already_seeded(marker):
            print(f"skip   {folder.name} (already seeded)")
            continue
        try:
            narrative, images = _load_pair(folder)
        except (InvalidImageError, OSError) as exc:
            print(f"error  {folder.name}: {exc}")
            continue
        if not images:
            print(f"skip   {folder.name} (no images)")
            continue

        result = await run_analysis(narrative, images)
        decision = storage.decide(result.analysis_id, "APPROVED", marker)
        print(f"added  {folder.name} -> {decision.claim_id} ({len(images)} image(s), risk {result.overall.risk_level})")
        approved += 1
    print(f"Done. {approved} claim(s) seeded.")
    return approved


if __name__ == "__main__":
    asyncio.run(seed())
