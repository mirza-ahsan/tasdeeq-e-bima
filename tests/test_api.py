"""Phase 5 tests — the API contract the frontend will build against."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.demo_claims import DEMO_CLAIMS
from backend.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def drive(client: TestClient, which: str) -> tuple[dict, list[str]]:
    """Walk a scripted demo claim through the whole loop, as the UI will."""
    claim = DEMO_CLAIMS[which]
    step = client.get(f"/api/demo/{which}").json()
    session_id, order = step["session_id"], []
    while not step["done"]:
        feature = step["question"]["feature"]
        order.append(feature)
        step = client.post(f"/api/session/{session_id}/answer",
                           json={"feature": feature, "value": claim[feature]}).json()
    return step, order


def test_health_reports_status_without_leaking_the_key(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok" and body["models_loaded"]
    assert body["qwen"]["DASHSCOPE_API_KEY"] in {"set", "MISSING"}
    # The key itself must never appear anywhere in the payload.
    assert "sk-" not in str(body)


def test_start_returns_a_first_question(client):
    body = client.post("/api/session/start", json={"patient_age": 44, "patient_gender": "F"}).json()
    assert not body["done"]
    assert body["question"]["feature"]
    assert body["question"]["text"]
    assert 0.0 <= body["probability"] <= 1.0


def test_unknown_session_is_404(client):
    assert client.get("/api/session/nope/result").status_code == 404


def test_rejects_unanswerable_field(client):
    session_id = client.post("/api/session/start", json={}).json()["session_id"]
    r = client.post(f"/api/session/{session_id}/answer",
                    json={"feature": "patient_age", "value": 30})
    assert r.status_code == 400


def test_risky_claim_full_loop(client):
    step, order = drive(client, "risky")
    assert step["done"]
    assert len(order) >= 3, "MIN_QUESTIONS should force at least three questions"

    result = client.get(f"/api/session/{step['session_id']}/result").json()
    assert result["risk_band"] in {"medium", "high"}
    assert result["flagged_fields"], "a risky claim must name the fields driving it"
    assert result["carc"] is not None
    assert result["carc"]["official_description"]
    assert result["explanation"]["text"]


def test_clean_claim_full_loop(client):
    step, order = drive(client, "clean")
    result = client.get(f"/api/session/{step['session_id']}/result").json()
    assert result["risk_band"] == "low"
    # A clean claim must not be given a rejection reason it does not have.
    assert result["carc"] is None


def test_the_two_demo_claims_differ(client):
    risky_step, risky_order = drive(client, "risky")
    clean_step, clean_order = drive(client, "clean")
    assert risky_step["probability"] > clean_step["probability"]
    assert risky_order != clean_order


def test_explanation_never_invents_a_number(client):
    """Qwen must restate the computed probability, not one of its own."""
    step, _ = drive(client, "risky")
    result = client.get(f"/api/session/{step['session_id']}/result").json()
    text = result["explanation"]["text"]
    stated = result["probability_percent"]
    import re
    percentages = {int(m) for m in re.findall(r"(\d{1,3})\s*%", text)}
    assert not (percentages - {stated}), (
        f"explanation mentions {percentages} but the model computed {stated}%: {text}")


def test_feedback_is_logged(client):
    step, _ = drive(client, "risky")
    r = client.post("/api/feedback", json={"session_id": step["session_id"],
                                          "verdict": "wrong", "note": "test"}).json()
    assert r["ok"] and r["logged_id"] > 0
