"""Fingerprints for duplicate detection: SHA-256 of the file bytes plus perceptual hashes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import imagehash

from app.services.image_utils import open_oriented


@dataclass(frozen=True)
class Fingerprint:
    sha256: str
    phash: imagehash.ImageHash  # 64-bit perceptual hash
    dhash: imagehash.ImageHash  # 64-bit difference hash

    @property
    def phash_hex(self) -> str:
        return str(self.phash)

    @property
    def dhash_hex(self) -> str:
        return str(self.dhash)


def fingerprint(data: bytes) -> Fingerprint:
    """Compute the fingerprint of raw image bytes. The hashes use the EXIF-corrected pixels."""
    sha = hashlib.sha256(data).hexdigest()
    img = open_oriented(data)
    return Fingerprint(sha256=sha, phash=imagehash.phash(img), dhash=imagehash.dhash(img))


def from_stored(sha256: str, phash_hex: str, dhash_hex: str) -> Fingerprint:
    """Rebuild a Fingerprint from the hex values stored in SQLite."""
    return Fingerprint(
        sha256=sha256,
        phash=imagehash.hex_to_hash(phash_hex),
        dhash=imagehash.hex_to_hash(dhash_hex),
    )


def hamming(a: imagehash.ImageHash, b: imagehash.ImageHash) -> int:
    """Hamming distance between two 64-bit hashes (number of differing bits)."""
    return a - b
