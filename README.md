# ClaimLens backend

Fraud-screening backend for motor insurance claims (hackathon demo). A reviewer uploads one or two claim photos
and a narrative. The backend runs three checks, combines them into a risk summary, and the reviewer approves or
rejects. Approved claims become reference data for future duplicate checks. Rejected claims add nothing.

Every output is a **probabilistic screening signal for a human reviewer**. Nothing here establishes fraud.

## Run it

All commands run from the project root (the folder containing `app/`).

```bash
python -m venv .venv
# Windows (PowerShell): .venv\Scripts\Activate.ps1      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # Windows: copy .env.example .env, then add your key
# Edit .env: set ANTHROPIC_API_KEY, or set MOCK_MODE=true to run without a key
uvicorn app.main:app --reload --port 8000
```

Interactive docs: http://localhost:8000/docs

Tests (no network, no API key needed):

```bash
python -m pytest
```

Seed the reference set from `sample_images/` (see below):

```bash
python -m scripts.seed
```

### Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | (empty) | Required unless `MOCK_MODE=true`. Never committed, never logged. |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | Model for the vision, consistency and brief calls. |
| `MOCK_MODE` | `false` | `true` skips all Claude calls and returns canned replies. The duplicate check stays real. |
| `DATA_DIR` | `./data` | Images and SQLite database. Relative paths resolve against the project root. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated list. |
| `DUP_PHASH_NEAR` | `6` | Max pHash distance for a NEAR match (also needs dHash distance <= 8). |
| `DUP_PHASH_POSSIBLE` | `12` | Max pHash distance for a POSSIBLE match. |
| `MAX_UPLOAD_MB` | `10` | Per-image upload limit. |

`MOCK_MODE` demo control: add a tag to the narrative to choose the canned outcome of the Claude checks.
`mock:edited`, `mock:ai`, `mock:partial`, `mock:inconsistent`. With no tag the result is LIKELY_AUTHENTIC and CONSISTENT.

## Layout

```
claim-lens-backend/
  app/
    main.py              app, CORS, routers, health, 422 error shape
    config.py            settings from .env / environment
    db.py                SQLite schema and helpers (stdlib sqlite3)
    schemas.py           Pydantic models (API contract, plus Claude reply models)
    routers/analyses.py  submit, get, image, decision
    routers/claims.py    list approved claims, claim images
    services/
      image_utils.py     format check, EXIF orientation, resize for Claude
      hashing.py         SHA-256, pHash, dHash
      duplicate.py       Check 1 (no LLM)
      ai_image.py        Check 2: EXIF analysis + Claude vision per photo
      consistency.py     Check 3: Claude, photos vs narrative
      synthesizer.py     deterministic score and risk, Claude brief
      claude_client.py   AsyncAnthropic wrapper: JSON-only, parse, retry once, validate, 60 s timeout
      mock_responses.py  canned replies for MOCK_MODE
      pipeline.py        runs one analysis end to end (added; not in the original file list)
      storage.py         files and SQLite for pending, approved and rejected (added)
  scripts/seed.py        pre-approve sample pairs as reference claims
  tests/                 test_duplicate.py (synthetic images), test_api.py (full flow in mock mode)
  sample_images/         drop claim-pair folders here for seeding
  requirements.txt  .env.example  pytest.ini
```

Data on disk (`DATA_DIR`):

```
claimlens.db                      SQLite: analyses, claims, claim_images, decisions
pending/{analysis_id}/            uploads + result.json while awaiting a decision
claims/{claim_id}/                approved photos + result.json (permanent)
```

## Pipeline

1. **Validate and save.** `image1` is required. `image2` is optional. Each image is checked by its bytes (Pillow),
   not its Content-Type header. The narrative is trimmed and must be 20 to 3000 characters. Originals are saved to `pending/`.
2. **Checks run concurrently** (`asyncio.gather`):
   - **Duplicate** (no LLM). SHA-256 for exact matches. pHash and dHash (64-bit) for near matches, compared against
     every approved-claim image and against the other uploaded image.
   - **AI-generated or edited**, per photo. EXIF signals (Pillow), plus one Claude vision call per photo, run in parallel.
   - **Narrative consistency**. One Claude call with all photos and the narrative.
3. **Synthesis.** Deterministic score (capped at 100): duplicate DUPLICATE +45, POSSIBLE_DUPLICATE +25;
   AI LIKELY_AI_GENERATED +40, POSSIBLY_EDITED +20; consistency INCONSISTENT +35, PARTIALLY_CONSISTENT +15.
   Risk: under 25 LOW, 25 to 59 MEDIUM, 60 or more HIGH. Recommendation: LOW to PROCEED, MEDIUM to REVIEW, HIGH to ESCALATE.
   Claude then writes a 3 to 5 sentence brief and 3 follow-up questions, grounded only in the three findings.
4. **Decision.** `APPROVED` moves photos to `claims/{claim_id}/`, writes the claim and its image hashes to SQLite, and adds an audit row.
   `REJECTED` writes only an audit row and deletes the pending photos. Rejected photos never enter the reference set.

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health` | `{"status":"ok","mock_mode":bool}` |
| POST | `/api/analyses` | multipart: `image1` (required), `image2` (optional), `narrative`. Returns `AnalysisResult`. |
| GET | `/api/analyses/{analysis_id}` | `AnalysisResult` |
| GET | `/api/analyses/{analysis_id}/images/{1\|2}` | The photo. 404 after rejection. |
| POST | `/api/analyses/{analysis_id}/decision` | `{"decision":"APPROVED"\|"REJECTED","reviewer_note":string\|null}`. 409 if already decided. |
| GET | `/api/claims` | Approved claims, newest first. |
| GET | `/api/claims/{claim_id}/images/{1\|2}` | Saved photo of an approved claim. |

Errors are JSON with a `detail` string: 413 file too large, 415 not a jpeg/png/webp image, 422 validation
(narrative length, missing field), 404 not found, 409 already decided.

## Smoke tests (curl)

Start the server first (`uvicorn app.main:app --port 8000`). These use `--form-string` for text fields so that
`;` and other characters in the narrative are not read as curl options. Replace the file names with your own photos.

```bash
# Health
curl -s http://localhost:8000/api/health

# Submit a claim with two photos
curl -s -X POST http://localhost:8000/api/analyses \
  -F "image1=@photos/front.jpg;type=image/jpeg" \
  -F "image2=@photos/rear.jpg;type=image/jpeg" \
  --form-string "narrative=Rear-ended at a junction on a wet morning; bumper and boot damaged."
# -> copy analysis_id from the response, e.g. 321b8cd6...
export AID=321b8cd6...

# Fetch the stored result
curl -s http://localhost:8000/api/analyses/$AID

# Fetch photo 1 (writes the image to a file)
curl -s -o photo1.jpg http://localhost:8000/api/analyses/$AID/images/1

# Approve (creates CLM-YYYY-NNNN and adds the photos to the reference set)
curl -s -X POST http://localhost:8000/api/analyses/$AID/decision \
  -H "Content-Type: application/json" \
  -d '{"decision":"APPROVED","reviewer_note":"Checked by hand"}'

# List approved claims
curl -s http://localhost:8000/api/claims

# Fetch a saved claim photo
curl -s -o saved1.jpg http://localhost:8000/api/claims/CLM-2026-0001/images/1

# Submit a claim reusing an approved photo: expect duplicate.verdict DUPLICATE
curl -s -X POST http://localhost:8000/api/analyses \
  -F "image1=@photos/front.jpg;type=image/jpeg" \
  --form-string "narrative=Second claim re-using an earlier photo of the same damage."

# Reject a claim (its photos are deleted; nothing is added to the reference set)
curl -s -X POST http://localhost:8000/api/analyses/$AID2/decision \
  -H "Content-Type: application/json" -d '{"decision":"REJECTED"}'

# Expect 409 on a repeated decision
curl -s -w "\n%{http_code}\n" -X POST http://localhost:8000/api/analyses/$AID/decision \
  -H "Content-Type: application/json" -d '{"decision":"REJECTED"}'
```

Windows PowerShell: use `curl.exe` (not the `curl` alias), and set variables with `$env:AID = "..."`.

## Seeding the reference set

Put one folder per pre-approved claim in `sample_images/`. Up to two images per folder, plus an optional `narrative.txt`:

```
sample_images/pair-01-rear-end/front.jpg
sample_images/pair-01-rear-end/rear.jpg
sample_images/pair-01-rear-end/narrative.txt
```

`python -m scripts.seed` runs each pair through the normal analysis, then approves it. Re-running skips pairs already seeded
(matched by the reviewer note `seed:<folder name>`). With `MOCK_MODE=false` each new pair costs Claude calls.

No sample photos are included in the repo. Add your own (do not commit real claimant photos).

## Assumptions

1. **Scope.** One claim has one or two photos. `image1` is required, `image2` is optional.
2. **Formats.** Only jpeg, png and webp. The format is read from the bytes with Pillow. The Content-Type header is ignored.
   Originals are stored byte-for-byte. Claude receives a JPEG, resized so the longest side is at most 1568 px.
3. **Narrative.** Trimmed, 20 to 3000 characters.
4. **No auth.** A demo. Anyone who can reach the API can submit and decide. Add authentication before real use.
5. **Claim IDs.** `CLM-YYYY-NNNN`, where YYYY is the UTC year and NNNN is a per-year sequence starting at 0001.
6. **Rejected analyses.** The analysis record, its result and the decision audit row are kept in SQLite. Photos are deleted,
   so `GET .../images/{n}` returns 404 afterwards.
7. **Missing EXIF is weak.** It is reported as a weak signal and never raises a verdict by itself. An editing-tool or AI-tool
   EXIF Software tag (for example Photoshop, Canva, Midjourney, Firefly) raises a LIKELY_AUTHENTIC verdict to POSSIBLY_EDITED.
   This is a deterministic floor applied after Claude's answer. It is noted in the photo's `indicators`.
8. **Photo check is probabilistic.** Prompts forbid claims of certainty. A failed call reports UNAVAILABLE for that photo.
9. **Failures degrade, they do not fail the request.** If a Claude call fails or returns invalid JSON twice, that detector
   returns UNAVAILABLE and scores 0 points. The brief then falls back to rule-generated text.
10. **Duplicate confidence values are fixed.** EXACT 1.0, NEAR 0.9, POSSIBLE 0.6, UNIQUE 0.9. Confidence in the detector is the strongest match.
    The NEAR dHash limit of 8 is fixed in code, not configurable.
11. **Thresholds, measured on synthetic images in `tests/test_duplicate.py`.** A resized and re-compressed copy gives pHash
    distance 0 and dHash distance 1 (NEAR). A +25% brightness copy gives pHash distance 8, which is above NEAR (6) and
    inside POSSIBLE (12), so it is reported as POSSIBLE_DUPLICATE. Tune `DUP_PHASH_NEAR` and `DUP_PHASH_POSSIBLE` on real photos.
12. **Duplicate search is a linear scan.** All reference hashes load into memory on each analysis. Production would use a
    vector index or BK-tree.
13. **Storage.** SQLite plus local files under `DATA_DIR`. A file move and a DB write are not one atomic step. The approve path
    moves files back if the DB write fails. Good enough for a demo, not for production.
14. **Concurrency on decisions.** The status change is a conditional `UPDATE ... WHERE status='PENDING_DECISION'`, so two
    simultaneous decisions cannot both succeed.
15. **Timestamps.** UTC, ISO 8601.
16. **Seeding.** Seed pairs run the full analysis and are then approved, so they can match later uploads.
17. **Brief and follow-ups.** Written by Claude from the three findings only. They are advisory and never state that fraud is confirmed.
18. **Recommendations are advisory.** PROCEED, REVIEW and ESCALATE are suggestions. The reviewer decides.

## Known limitations

- Not run against the live Claude API in this environment (no key was available). All Claude-dependent paths are covered by
  mock mode and by the no-key degradation test. Run one real analysis with a key before the demo.
- No rate limiting, no upload virus scanning, no pagination on `/api/claims`.
