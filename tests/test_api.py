"""End-to-end API tests in MOCK_MODE (no Claude calls), using a temporary DATA_DIR."""

from __future__ import annotations


import pytest
from fastapi.testclient import TestClient

from tests.test_duplicate import make_scene, to_jpeg_bytes

NARRATIVE = "Rear-ended at a junction on a wet morning; bumper and boot damaged."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    get_settings.cache_clear()


def _submit(client: TestClient, image1: bytes, narrative: str = NARRATIVE, image2: bytes | None = None):
    files = {"image1": ("front.jpg", image1, "image/jpeg")}
    if image2 is not None:
        files["image2"] = ("rear.jpg", image2, "image/jpeg")
    return client.post("/api/analyses", files=files, data={"narrative": narrative})


def test_health_reports_mock_mode(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"status": "ok", "mock_mode": True}


def test_full_flow_approve_then_duplicate_then_reject(client: TestClient) -> None:
    photo_a = to_jpeg_bytes(make_scene(seed=1))
    photo_b = to_jpeg_bytes(make_scene(seed=2))

    # 1. Analyse a claim with two photos.
    resp = _submit(client, photo_a, image2=photo_b)
    assert resp.status_code == 200, resp.text
    analysis = resp.json()
    aid = analysis["analysis_id"]
    assert analysis["status"] == "PENDING_DECISION"
    assert analysis["detectors"]["duplicate"]["verdict"] == "UNIQUE"
    assert analysis["detectors"]["ai_generated"]["verdict"] == "LIKELY_AUTHENTIC"
    assert analysis["detectors"]["consistency"]["verdict"] == "CONSISTENT"
    assert analysis["overall"]["risk_level"] == "LOW"
    assert analysis["overall"]["recommendation"] == "PROCEED"
    assert len(analysis["overall"]["follow_up_questions"]) == 3

    # 2. Fetch it back and its photos.
    assert client.get(f"/api/analyses/{aid}").json()["analysis_id"] == aid
    img = client.get(f"/api/analyses/{aid}/images/1")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"

    # 3. Approve: a claim is created and the photos join the reference set.
    decision = client.post(f"/api/analyses/{aid}/decision", json={"decision": "APPROVED", "reviewer_note": "ok"})
    assert decision.status_code == 200, decision.text
    claim_id = decision.json()["claim_id"]
    assert claim_id.startswith("CLM-") and claim_id.endswith("-0001")

    claims = client.get("/api/claims").json()
    assert [c["claim_id"] for c in claims] == [claim_id]
    assert claims[0]["image_urls"] == [f"/api/claims/{claim_id}/images/1", f"/api/claims/{claim_id}/images/2"]
    assert client.get(claims[0]["image_urls"][0]).status_code == 200

    # 4. A new claim with the same photo is flagged as a duplicate of the approved claim.
    again = _submit(client, photo_a).json()
    dup = again["detectors"]["duplicate"]
    assert dup["verdict"] == "DUPLICATE"
    assert dup["matches"][0]["match_type"] == "EXACT"
    assert dup["matches"][0]["matched_claim_id"] == claim_id

    # 5. Reject it: photos are deleted, nothing is added to the reference set.
    reject = client.post(f"/api/analyses/{again['analysis_id']}/decision", json={"decision": "REJECTED"})
    assert reject.status_code == 200 and reject.json()["claim_id"] is None
    assert client.get(f"/api/analyses/{again['analysis_id']}/images/1").status_code == 404
    assert len(client.get("/api/claims").json()) == 1

    # 6. A second decision on the same analysis is a conflict.
    repeat = client.post(f"/api/analyses/{aid}/decision", json={"decision": "REJECTED"})
    assert repeat.status_code == 409


def test_validation_errors_are_json_detail(client: TestClient) -> None:
    photo = to_jpeg_bytes(make_scene(seed=3))

    short = _submit(client, photo, narrative="too short")
    assert short.status_code == 422 and "narrative" in short.json()["detail"]

    not_image = _submit(client, b"this is plain text, not an image")
    assert not_image.status_code == 415 and "image1" in not_image.json()["detail"]

    missing = client.post("/api/analyses", data={"narrative": NARRATIVE})
    assert missing.status_code == 422 and "detail" in missing.json()

    unknown = client.get("/api/analyses/does-not-exist")
    assert unknown.status_code == 404 and unknown.json()["detail"]


def test_claude_failure_degrades_to_unavailable(client: TestClient, monkeypatch) -> None:
    """With MOCK_MODE off and no API key, the AI checks report UNAVAILABLE but the request still succeeds."""
    from app.config import get_settings

    monkeypatch.setenv("MOCK_MODE", "false")
    get_settings.cache_clear()
    resp = _submit(client, to_jpeg_bytes(make_scene(seed=5)))
    assert resp.status_code == 200, resp.text
    detectors = resp.json()["detectors"]
    assert detectors["ai_generated"]["verdict"] == "UNAVAILABLE"
    assert detectors["consistency"]["verdict"] == "UNAVAILABLE"
    assert detectors["duplicate"]["verdict"] == "UNIQUE"  # the real check still runs
    assert resp.json()["overall"]["brief"]  # fallback brief is still produced


def test_oversized_upload_is_rejected(client: TestClient, monkeypatch) -> None:
    from app.config import get_settings

    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    # Valid JPEG followed by padding: the size check runs before decoding, so this exceeds 1 MB.
    big = to_jpeg_bytes(make_scene(seed=4)) + b"\x00" * (2 * 1024 * 1024)
    resp = _submit(client, big)
    assert resp.status_code == 413
