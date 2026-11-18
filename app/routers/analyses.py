"""Analysis endpoints: submit a claim, read a result, serve its photos, and record the reviewer's decision."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import get_settings
from app.schemas import AnalysisResult, DecisionRequest, DecisionResponse
from app.services import storage
from app.services.image_utils import InvalidImageError, detect_format, open_oriented
from app.services.pipeline import IncomingImage, run_analysis

router = APIRouter(prefix="/api/analyses", tags=["analyses"])

NARRATIVE_MIN = 20
NARRATIVE_MAX = 3000


async def _read_image(upload: UploadFile, label: str) -> IncomingImage:
    """Read one upload with size and format checks. Format is judged from the bytes, not the header."""
    max_bytes = get_settings().max_upload_bytes
    data = await upload.read(max_bytes + 1)  # never read more than the limit plus one byte
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label} is larger than {get_settings().max_upload_mb} MB",
        )
    try:
        ext, _mime = detect_format(data)
        open_oriented(data)  # make sure it fully decodes, so later steps cannot fail on it
    except Exception as exc:  # Pillow raises OSError, SyntaxError, DecompressionBombError, etc.
        detail = str(exc) if isinstance(exc, InvalidImageError) else "File is not a valid image"
        raise HTTPException(status_code=415, detail=f"{label}: {detail}") from exc
    return IncomingImage(filename=Path(upload.filename or label).name, ext=ext, data=data)


@router.post("", response_model=AnalysisResult)
async def create_analysis(
    image1: UploadFile = File(...),
    image2: UploadFile | None = File(None),
    narrative: str = Form(...),
) -> AnalysisResult:
    """Upload one or two photos plus a narrative. Runs the three checks and returns the risk summary."""
    text = narrative.strip()
    if not NARRATIVE_MIN <= len(text) <= NARRATIVE_MAX:
        raise HTTPException(
            status_code=422,
            detail=f"narrative must be {NARRATIVE_MIN}-{NARRATIVE_MAX} characters (got {len(text)})",
        )

    images = [await _read_image(image1, "image1")]
    # A second file part with no filename or no bytes means the browser sent an empty field.
    if image2 is not None and (image2.filename or ""):
        images.append(await _read_image(image2, "image2"))

    return await run_analysis(text, images)


@router.get("/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: str) -> AnalysisResult:
    """Return the stored result for an analysis, whatever its status."""
    return storage.get_analysis(analysis_id)


@router.get("/{analysis_id}/images/{index}")
def get_analysis_image(analysis_id: str, index: int) -> FileResponse:
    """Serve photo 1 or 2 of an analysis. Photos are removed when the claim is rejected."""
    if index not in (1, 2):
        raise HTTPException(status_code=404, detail="Image index must be 1 or 2")
    path = storage.analysis_image_path(analysis_id, index)
    return FileResponse(path, media_type=storage.media_type_for(path))


@router.post("/{analysis_id}/decision", response_model=DecisionResponse)
def decide(analysis_id: str, body: DecisionRequest) -> DecisionResponse:
    """Approve (saves photos as reference data) or reject (deletes photos). Only the first decision counts."""
    note = body.reviewer_note.strip() if body.reviewer_note else None
    return storage.decide(analysis_id, body.decision, note or None)
