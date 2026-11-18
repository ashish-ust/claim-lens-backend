# CLAUDE.md

Project context for Claude Code. Read this before making changes.

## What this is

ClaimLens backend: a fraud-screening API for motor insurance claims, built for a hackathon demo.
A reviewer uploads one or two claim photos plus a text narrative. The backend runs three checks,
combines them into a risk summary, and the reviewer approves or rejects.

- Approved claims are saved permanently and their photos become reference data for future duplicate checks.
- Rejected claims are deleted and add nothing to the reference set.
- Every output is a probabilistic screening signal for a human reviewer. Nothing may claim or imply fraud is confirmed.

The React frontend lives in a separate repo. This repo is backend only. The API contract in `app/schemas.py`
is what the UI depends on, so field names and enum values must not change.

## Stack

Python 3.13, FastAPI, uvicorn, Pydantic v2, python-multipart, Pillow, imagehash, anthropic (AsyncAnthropic),
python-dotenv, pytest, httpx (for TestClient). Storage: SQLite via stdlib `sqlite3` for metadata, image files on
local disk under `DATA_DIR`. No other databases.

## Layout

```
app/
  main.py              FastAPI app, CORS, lifespan (init_db), routers, /api/health, 422 error shape
  config.py            Settings from .env / env; get_settings() is lru_cached (tests call cache_clear())
  db.py                SQLite schema, connect(), transaction() context manager (commits, rolls back, closes)
  schemas.py           API contract models + Claude reply models (AIImageLLM, ConsistencyLLM, BriefLLM)
  routers/analyses.py  POST/GET analyses, image serving, decision endpoint
  routers/claims.py    GET claims, claim image serving
  services/
    image_utils.py     format detection from bytes, EXIF transpose, resize to JPEG for Claude
    hashing.py         SHA-256, pHash, dHash fingerprints; hamming distance
    duplicate.py       Check 1, deterministic (no LLM)
    ai_image.py        Check 2: EXIF signals + one Claude vision call per photo, run in parallel
    consistency.py     Check 3: one Claude call with all photos and the narrative
    synthesizer.py     deterministic score and risk level; Claude writes brief + follow-ups
    claude_client.py   AsyncAnthropic wrapper: JSON-only replies, fence stripping, one retry, Pydantic validation, 60 s timeout
    mock_responses.py  canned replies for MOCK_MODE
    pipeline.py        runs one analysis end to end (added beyond the original spec)
    storage.py         pending/claims files and SQLite writes; approve/reject logic (added beyond the original spec)
scripts/seed.py        pre-approves folder pairs in sample_images/ as reference claims
tests/                 test_duplicate.py (synthetic images), test_api.py (full flow in mock mode)
sample_images/         claim-pair folders for seeding (no photos committed)
```

Data on disk under `DATA_DIR` (default `./data`, resolved against the project root):

```
claimlens.db                  analyses, claims, claim_images, decisions
pending/{analysis_id}/        originals + result.json while awaiting a decision
claims/{claim_id}/            approved originals + result.json
```

## Commands

Run everything from the project root (the folder containing `app/`).

```
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt     # Windows; use .venv/bin/python on macOS/Linux
copy .env.example .env                                          # then set ANTHROPIC_API_KEY or MOCK_MODE=true
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m scripts.seed
```

Development machine is Windows with PowerShell and Git Bash. Use `curl.exe` in PowerShell, not the `curl` alias.

## Configuration (.env, never committed)

`ANTHROPIC_API_KEY`, `CLAUDE_MODEL` (default `claude-sonnet-5-5`), `MOCK_MODE` (default false), `DATA_DIR`,
`CORS_ORIGINS`, `DUP_PHASH_NEAR` (6), `DUP_PHASH_POSSIBLE` (12), `MAX_UPLOAD_MB` (10).
The key must never be hardcoded or logged. Image bytes must never be logged.

## Business rules (keep these exact)

- Uploads: `image1` required, `image2` optional. Format is judged from the bytes with Pillow, not the Content-Type header.
  Accepted: jpeg, png, webp. Size limit `MAX_UPLOAD_MB`.
- Narrative: trimmed, 20 to 3000 characters.
- Duplicate match rules: same SHA-256 means EXACT. Else pHash <= DUP_PHASH_NEAR and dHash <= 8 means NEAR.
  Else pHash <= DUP_PHASH_POSSIBLE means POSSIBLE. Verdict: DUPLICATE if any EXACT or NEAR, POSSIBLE_DUPLICATE if only POSSIBLE, else UNIQUE.
  Each uploaded image is compared with every approved-claim image and with the other uploaded image (each pair once).
- AI check: EXIF missing is only a weak signal and must be described as one. An editing-tool or AI-tool EXIF tag raises
  LIKELY_AUTHENTIC to POSSIBLY_EDITED. Overall verdict is the most severe per-image verdict.
- Score (capped at 100): duplicate DUPLICATE +45, POSSIBLE_DUPLICATE +25; AI LIKELY_AI_GENERATED +40, POSSIBLY_EDITED +20;
  consistency INCONSISTENT +35, PARTIALLY_CONSISTENT +15. Risk: <25 LOW, 25-59 MEDIUM, >=60 HIGH.
  Recommendation: LOW to PROCEED, MEDIUM to REVIEW, HIGH to ESCALATE. Advisory only.
- Decision: one decision per analysis. A second decision returns 409. Approve gives CLM-YYYY-NNNN (per-year sequence, UTC year).
  Reject deletes pending photos and writes only an audit row.
- Claude failure: that detector returns verdict UNAVAILABLE and scores 0. The request still succeeds. The brief falls back to rule-based text.

## Error shape

All errors are JSON `{"detail": "..."}`. Status codes: 413 too large, 415 not a valid jpeg/png/webp, 422 validation,
404 not found, 409 already decided. Keep the 422 handler in `main.py` that flattens FastAPI validation errors into one string.

## Conventions

- Type hints everywhere. Short docstrings. Simple and readable over clever.
- Pydantic models for every API and Claude reply. Clamp confidence to 0..1 in the validator.
- DB access goes through `db.transaction()`. Do not use sqlite3's own `with conn:`, which does not close the connection.
- Approve and reject use a conditional `UPDATE ... WHERE status='PENDING_DECISION'` so decisions can't race. Keep that.
- Keep file moves and DB writes together in `storage.py`, with rollback of files on failure.
- Imports inside the `app` package are absolute from the project root (e.g. `from app.services.hashing import ...`).
- Do not add a real Claude call to tests. Tests run in mock mode or with no key (the degradation test).
- Duplicate search is a linear scan by design. Mention a vector index or BK-tree in comments only.

## Testing

`python -m pytest` must pass before you finish a change (currently 10 tests). `tests/test_duplicate.py` uses
synthetic images from `make_scene()` and `to_jpeg_bytes()`. Measured distances: resize + recompress gives NEAR,
+25% brightness gives POSSIBLE (pHash 8). These assertions are deliberate. If you change thresholds or hashing,
update them and say why.

## Things to watch

- Not yet verified against the live Claude API (no key during build). Mock mode and the no-key path are tested.
  Say so if you change any Claude prompt or parser.
- Mock demo control: a narrative containing `mock:edited`, `mock:ai`, `mock:partial` or `mock:inconsistent` selects the canned outcome.
- The README documents curl smoke tests. Use `--form-string` for the narrative, because curl reads `;` in `-F` values as parameter separators.
- `sample_images/` is for seeding only. Never commit real claimant photos.
- Sensitive data: never request, store, process or display SSN, Aadhaar, PAN, passport or driver's licence numbers,
  credit card numbers or patient records. Don't add fields that collect them.
- Pins in `requirements.txt` are deliberately older versions known to install cleanly on Python 3.13. Bump them only with a reason.
- Not a git repository yet. Commit only when asked.

## Known limitations (documented in README)

No auth, no rate limiting, no pagination on `/api/claims`, no virus scanning. Files and DB writes are not one atomic step.
Duplicate thresholds were tuned only on synthetic images.
