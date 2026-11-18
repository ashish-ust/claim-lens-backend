"""Orchestrates one analysis: save uploads, run the three checks concurrently, synthesise, persist as pending."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from dataclasses import dataclass

from app.schemas import AnalysisResult, Detectors, ImageInfo
from app.services import storage
from app.services.ai_image import check_ai_generated
from app.services.consistency import check_consistency
from app.services.duplicate import UploadedImage, check_duplicates, load_reference_images
from app.services.hashing import fingerprint
from app.services.synthesizer import synthesize


@dataclass(frozen=True)
class IncomingImage:
    """A validated upload: original bytes, extension from the detected format, and the client's filename."""

    filename: str
    ext: str
    data: bytes


async def run_analysis(narrative: str, images: list[IncomingImage]) -> AnalysisResult:
    """Run the full analysis for one claim and store it as PENDING_DECISION."""
    analysis_id = uuid.uuid4().hex
    created_at = storage.utc_now_iso()
    datas = [img.data for img in images]

    # Persist originals first so the reviewer can see them and a decision can be made later.
    files = [(i, img.ext, img.data) for i, img in enumerate(images, start=1)]
    folder = storage.save_pending(analysis_id, files)

    try:
        uploads = [
            UploadedImage(index=i, fingerprint=fingerprint(data))
            for i, data in enumerate(datas, start=1)
        ]
        refs = load_reference_images()

        # Check 1 is CPU-only and runs in a thread; checks 2 (per photo) and 3 call Claude.
        duplicate, ai_generated, consistency = await asyncio.gather(
            asyncio.to_thread(check_duplicates, analysis_id, uploads, refs),
            check_ai_generated(datas, narrative),
            check_consistency(datas, narrative),
        )
        detectors = Detectors(duplicate=duplicate, ai_generated=ai_generated, consistency=consistency)
        overall = await synthesize(detectors)

        result = AnalysisResult(
            analysis_id=analysis_id,
            created_at=created_at,
            status="PENDING_DECISION",
            narrative=narrative,
            images=[
                ImageInfo(
                    index=img_index,
                    filename=img.filename,
                    url=f"/api/analyses/{analysis_id}/images/{img_index}",
                )
                for img_index, img in enumerate(images, start=1)
            ],
            detectors=detectors,
            overall=overall,
        )
        storage.write_result_json(folder, result)
        storage.insert_analysis(result)
        return result
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)  # do not leave half-analysed uploads behind
        raise
