"""Approved-claim endpoints: the reference set of saved claims and their photos."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.schemas import ClaimSummary
from app.services import storage

router = APIRouter(prefix="/api/claims", tags=["claims"])


@router.get("", response_model=list[ClaimSummary])
def list_claims() -> list[ClaimSummary]:
    """All approved claims, newest first."""
    return storage.list_claims()


@router.get("/{claim_id}/images/{index}")
def get_claim_image(claim_id: str, index: int) -> FileResponse:
    """Serve one saved photo of an approved claim."""
    if index not in (1, 2):
        raise HTTPException(status_code=404, detail="Image index must be 1 or 2")
    path = storage.claim_image_path(claim_id, index)
    return FileResponse(path, media_type=storage.media_type_for(path))
