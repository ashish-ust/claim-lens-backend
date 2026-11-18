"""Check 1: duplicate detection. Deterministic, no LLM.

Each uploaded image is compared against (a) every image of every approved claim and
(b) the other uploaded image in the same submission (each pair is compared once).

Production note: the in-memory linear scan below is fine for a demo-sized reference set.
A real deployment would use a vector index or a BK-tree over the 64-bit hashes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.db import transaction
from app.schemas import DuplicateDetector, DuplicateMatch, DuplicateVerdict, MatchType
from app.services.hashing import Fingerprint, from_stored, hamming

DHASH_NEAR = 8  # dhash must also be this close for a NEAR match

MATCH_CONFIDENCE: dict[str, float] = {"EXACT": 1.0, "NEAR": 0.9, "POSSIBLE": 0.6}


@dataclass(frozen=True)
class ReferenceImage:
    """An image from an approved claim, as stored in SQLite."""

    claim_id: str
    image_index: int
    fingerprint: Fingerprint


@dataclass(frozen=True)
class UploadedImage:
    """An image in the current submission."""

    index: int
    fingerprint: Fingerprint


def load_reference_images() -> list[ReferenceImage]:
    """Load all approved-claim image hashes into memory."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT claim_id, image_index, sha256, phash, dhash FROM claim_images"
        ).fetchall()
    return [
        ReferenceImage(
            claim_id=r["claim_id"],
            image_index=r["image_index"],
            fingerprint=from_stored(r["sha256"], r["phash"], r["dhash"]),
        )
        for r in rows
    ]


def classify_pair(
    a: Fingerprint, b: Fingerprint, near: int, possible: int
) -> tuple[MatchType | None, int]:
    """Return (match_type or None, phash hamming distance) for one pair of images."""
    dist = hamming(a.phash, b.phash)
    if a.sha256 == b.sha256:
        return "EXACT", dist
    if dist <= near and hamming(a.dhash, b.dhash) <= DHASH_NEAR:
        return "NEAR", dist
    if dist <= possible:
        return "POSSIBLE", dist
    return None, dist


def check_duplicates(
    analysis_id: str,
    uploads: list[UploadedImage],
    refs: list[ReferenceImage],
    near: int | None = None,
    possible: int | None = None,
) -> DuplicateDetector:
    """Compare uploads with the reference set and with each other, then build the verdict."""
    settings = get_settings()
    near = settings.dup_phash_near if near is None else near
    possible = settings.dup_phash_possible if possible is None else possible

    matches: list[DuplicateMatch] = []

    for up in uploads:
        # (a) approved claims
        for ref in refs:
            match_type, dist = classify_pair(up.fingerprint, ref.fingerprint, near, possible)
            if match_type:
                matches.append(
                    DuplicateMatch(
                        image_index=up.index,
                        match_type=match_type,
                        hamming_distance=dist,
                        matched_claim_id=ref.claim_id,
                        matched_source="EXISTING_CLAIM",
                        matched_image_url=f"/api/claims/{ref.claim_id}/images/{ref.image_index}",
                    )
                )

    # (b) the other image in this submission; compare each pair once (later vs earlier)
    for i, later in enumerate(uploads):
        for earlier in uploads[:i]:
            match_type, dist = classify_pair(later.fingerprint, earlier.fingerprint, near, possible)
            if match_type:
                matches.append(
                    DuplicateMatch(
                        image_index=later.index,
                        match_type=match_type,
                        hamming_distance=dist,
                        matched_claim_id=None,
                        matched_source="OTHER_UPLOADED_IMAGE",
                        matched_image_url=f"/api/analyses/{analysis_id}/images/{earlier.index}",
                    )
                )

    return _verdict(matches, n_refs=len(refs))


def _verdict(matches: list[DuplicateMatch], n_refs: int) -> DuplicateDetector:
    """DUPLICATE if any EXACT or NEAR match, POSSIBLE_DUPLICATE if only POSSIBLE, else UNIQUE."""
    types = {m.match_type for m in matches}
    verdict: DuplicateVerdict
    if types & {"EXACT", "NEAR"}:
        verdict = "DUPLICATE"
    elif "POSSIBLE" in types:
        verdict = "POSSIBLE_DUPLICATE"
    else:
        verdict = "UNIQUE"

    if matches:
        confidence = max(MATCH_CONFIDENCE[m.match_type] for m in matches)
        strongest = min(matches, key=lambda m: ["EXACT", "NEAR", "POSSIBLE"].index(m.match_type))
        summary = (
            f"{len(matches)} match(es) found; strongest is {strongest.match_type} for image "
            f"{strongest.image_index} (phash distance {strongest.hamming_distance})."
        )
    else:
        confidence = 0.9
        summary = (
            f"No matches against {n_refs} reference image(s) or the other uploaded image."
        )
    return DuplicateDetector(
        verdict=verdict, confidence=confidence, summary=summary, matches=matches
    )
