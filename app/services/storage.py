"""Persistence: pending uploads on disk, approved claims in claims/, and metadata in SQLite.

Lifecycle:
  analyze  -> DATA_DIR/pending/{analysis_id}/ + analyses row (PENDING_DECISION)
  APPROVED -> files move to DATA_DIR/claims/{claim_id}/, claims + claim_images rows, reference set grows
  REJECTED -> pending folder deleted, only analyses/decisions rows remain; nothing joins the reference set
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException

from app.config import get_settings
from app.db import transaction
from app.schemas import AnalysisResult, ClaimSummary, DecisionResponse
from app.services.hashing import fingerprint
from app.services.image_utils import ALLOWED_FORMATS

RESULT_FILE = "result.json"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def pending_dir(analysis_id: str) -> Path:
    return get_settings().data_dir / "pending" / analysis_id


def claim_dir(claim_id: str) -> Path:
    return get_settings().data_dir / "claims" / claim_id


def save_pending(analysis_id: str, files: list[tuple[int, str, bytes]]) -> Path:
    """Write uploaded originals (byte-for-byte) to the pending folder. files = [(index, ext, data)]."""
    folder = pending_dir(analysis_id)
    folder.mkdir(parents=True, exist_ok=True)
    for index, ext, data in files:
        (folder / f"image{index}{ext}").write_bytes(data)
    return folder


def write_result_json(folder: Path, result: AnalysisResult) -> None:
    (folder / RESULT_FILE).write_text(result.model_dump_json(indent=2), encoding="utf-8")


def insert_analysis(result: AnalysisResult) -> None:
    """Store a new analysis row. The full result is kept in SQLite so GET works after files move or get deleted."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO analyses (analysis_id, created_at, status, narrative, risk_level, risk_score, result_json, claim_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                result.analysis_id,
                result.created_at,
                result.status,
                result.narrative,
                result.overall.risk_level,
                result.overall.risk_score,
                result.model_dump_json(),
            ),
        )


def get_analysis(analysis_id: str) -> AnalysisResult:
    with transaction() as conn:
        row = conn.execute(
            "SELECT result_json FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    return AnalysisResult.model_validate_json(row["result_json"])


def analysis_image_path(analysis_id: str, index: int) -> Path:
    """Locate an analysis image on disk: pending folder while undecided, claim folder once approved."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT status, claim_id FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Analysis not found")

    if row["status"] == "PENDING_DECISION":
        base = pending_dir(analysis_id)
    elif row["status"] == "APPROVED":
        base = claim_dir(row["claim_id"])
    else:
        raise HTTPException(status_code=404, detail="Images were deleted after the claim was rejected")

    matches = sorted(base.glob(f"image{index}.*")) if base.exists() else []
    if not matches:
        raise HTTPException(status_code=404, detail="Image not found")
    return matches[0]


def next_claim_id(conn, year: int) -> str:
    """CLM-YYYY-NNNN with NNNN = highest existing number for that year + 1."""
    prefix = f"CLM-{year}-"
    row = conn.execute(
        "SELECT claim_id FROM claims WHERE claim_id LIKE ? ORDER BY claim_id DESC LIMIT 1",
        (prefix + "%",),
    ).fetchone()
    last = int(row["claim_id"][len(prefix):]) if row else 0
    return f"{prefix}{last + 1:04d}"


def decide(analysis_id: str, decision: str, note: str | None) -> DecisionResponse:
    """Apply APPROVED or REJECTED. Only the first decision succeeds; later ones get 409."""
    if decision == "APPROVED":
        return _approve(analysis_id, note)
    return _reject(analysis_id, note)


def _claim_status_update(conn, analysis_id: str, new_status: str) -> None:
    """Flip PENDING_DECISION to new_status atomically. The WHERE clause makes a second decision fail."""
    cur = conn.execute(
        "UPDATE analyses SET status = ? WHERE analysis_id = ? AND status = 'PENDING_DECISION'",
        (new_status, analysis_id),
    )
    if cur.rowcount == 1:
        return
    exists = conn.execute(
        "SELECT status FROM analyses WHERE analysis_id = ?", (analysis_id,)
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="Analysis not found")
    raise HTTPException(status_code=409, detail=f"Analysis already decided ({exists['status']})")


def _approve(analysis_id: str, note: str | None) -> DecisionResponse:
    now = utc_now_iso()
    year = datetime.now(timezone.utc).year
    src = pending_dir(analysis_id)
    moved: list[tuple[Path, Path]] = []  # (new_location, original_location) for rollback

    with transaction() as conn:
        _claim_status_update(conn, analysis_id, "APPROVED")  # takes the write lock first
        row = conn.execute(
            "SELECT result_json FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
        result = AnalysisResult.model_validate_json(row["result_json"])
        claim_id = next_claim_id(conn, year)
        dest = claim_dir(claim_id)

        try:
            dest.mkdir(parents=True, exist_ok=True)
            claim_images = []
            for image in result.images:
                originals = sorted(src.glob(f"image{image.index}.*"))
                if not originals:
                    raise HTTPException(status_code=500, detail="Pending image files are missing")
                original = originals[0]
                target = dest / original.name
                shutil.move(str(original), str(target))
                moved.append((target, original))
                data = target.read_bytes()
                fp = fingerprint(data)
                claim_images.append(
                    (claim_id, image.index, target.relative_to(get_settings().data_dir).as_posix(),
                     fp.sha256, fp.phash_hex, fp.dhash_hex)
                )

            approved = result.model_copy(update={"status": "APPROVED"})
            write_result_json(dest, approved)
            conn.execute(
                "INSERT INTO claims (claim_id, created_at, narrative, risk_level, risk_score, result_json, reviewer_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (claim_id, now, result.narrative, result.overall.risk_level, result.overall.risk_score,
                 approved.model_dump_json(), note),
            )
            conn.executemany(
                "INSERT INTO claim_images (claim_id, image_index, file_path, sha256, phash, dhash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                claim_images,
            )
            conn.execute(
                "UPDATE analyses SET claim_id = ? WHERE analysis_id = ?", (claim_id, analysis_id)
            )
            conn.execute(
                "INSERT INTO decisions (analysis_id, decision, reviewer_note, claim_id, decided_at) "
                "VALUES (?, 'APPROVED', ?, ?, ?)",
                (analysis_id, note, claim_id, now),
            )
        except Exception:
            # Put files back so the pending analysis stays intact; the DB transaction rolls back too.
            for new_loc, original in reversed(moved):
                shutil.move(str(new_loc), str(original))
            shutil.rmtree(dest, ignore_errors=True)
            raise

    shutil.rmtree(src, ignore_errors=True)  # only result.json (already copied) remains
    return DecisionResponse(
        analysis_id=analysis_id,
        decision="APPROVED",
        claim_id=claim_id,
        message=f"Claim approved and saved as {claim_id}; its photos now count as reference data.",
    )


def _reject(analysis_id: str, note: str | None) -> DecisionResponse:
    now = utc_now_iso()
    with transaction() as conn:
        _claim_status_update(conn, analysis_id, "REJECTED")
        conn.execute(
            "INSERT INTO decisions (analysis_id, decision, reviewer_note, claim_id, decided_at) "
            "VALUES (?, 'REJECTED', ?, NULL, ?)",
            (analysis_id, note, now),
        )
    # Delete only after the decision is committed; rejected photos never enter the reference set.
    shutil.rmtree(pending_dir(analysis_id), ignore_errors=True)
    return DecisionResponse(
        analysis_id=analysis_id,
        decision="REJECTED",
        claim_id=None,
        message="Claim rejected; uploaded photos were deleted and not added to the reference set.",
    )


def list_claims() -> list[ClaimSummary]:
    """All approved claims, newest first, with image URLs."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT claim_id, created_at, narrative, risk_level FROM claims ORDER BY created_at DESC, claim_id DESC"
        ).fetchall()
        images = conn.execute(
            "SELECT claim_id, image_index FROM claim_images ORDER BY claim_id, image_index"
        ).fetchall()
    urls: dict[str, list[str]] = {}
    for img in images:
        urls.setdefault(img["claim_id"], []).append(
            f"/api/claims/{img['claim_id']}/images/{img['image_index']}"
        )
    return [
        ClaimSummary(
            claim_id=r["claim_id"],
            created_at=r["created_at"],
            narrative=r["narrative"],
            risk_level=r["risk_level"],
            image_urls=urls.get(r["claim_id"], []),
        )
        for r in rows
    ]


def claim_image_path(claim_id: str, index: int) -> Path:
    with transaction() as conn:
        row = conn.execute(
            "SELECT file_path FROM claim_images WHERE claim_id = ? AND image_index = ?",
            (claim_id, index),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Claim image not found")
    return get_settings().data_dir / row["file_path"]


def media_type_for(path: Path) -> str:
    """MIME type from the file extension (only jpeg, png and webp are ever stored)."""
    for _fmt, (ext, mime) in ALLOWED_FORMATS.items():
        if path.suffix.lower() == ext:
            return mime
    return "application/octet-stream"
