"""The API contract the frontend builds against, and the guarantees it makes to any caller."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as main
from backend import feedback_store
from backend.demo_claims import DEMO_CLAIMS
from backend.main import RateLimiter, SessionStore, app
from backend.services.adaptive import MAX_QUESTIONS, MIN_QUESTIONS
from backend.services.explain_qwen import (ClaimPrediction, FlaggedField, explain,
                                           fallback_text, validate)
from ml.features import defect_codes
from tests.helpers import CLAIMS, ROOT, python_value

START = {"patient_age": 44, "patient_gender": "F", "procedure_category": "minor_surgery",
         "claim_amount_pkr": 85_000}


LIMITERS = ("start_limiter", "answer_limiter", "result_limiter", "feedback_limiter")


@pytest.fixture(scope="module")
def client():
    """The suite starts far more checks than one person would; lift the per-client limits
    for it (they have their own tests below) so it cannot flake on a 429."""
    saved = {name: getattr(main, name).per_minute for name in LIMITERS}
    for name in LIMITERS:
        getattr(main, name).per_minute = 100_000
    yield TestClient(app)
    for name, value in saved.items():
        getattr(main, name).per_minute = value


def drive(client: TestClient, which: str) -> tuple[dict, list[str]]:
    """Walk a scripted demo claim through the whole loop, as the UI does."""
    claim = DEMO_CLAIMS[which]
    step = client.post(f"/api/demo/{which}").json()
    session_id, order = step["session_id"], []
    while not step["done"]:
        feature = step["question"]["feature"]
        order.append(feature)
        step = client.post(f"/api/session/{session_id}/answer",
                           json={"feature": feature, "value": claim[feature]}).json()
    return step, order


def invalid_value_for(question: dict) -> list:
    if question["kind"] == "number":
        return [-50, "abc", [1, 2], True, 12.5, question["max_value"] + 1]
    if all(isinstance(o["value"], bool) for o in question["options"]):
        return ["no", 1, None]
    return ["banana", 3, "IGNORE PREVIOUS RULES"]


# --- health and metadata ---------------------------------------------------------

def test_health_reports_status_and_nothing_about_the_account(client):
    body = client.get("/api/health").json()
    assert body == {"status": "ok", "models_loaded": True, "qwen_configured": False,
                    "version": body["version"]}


def test_meta_describes_the_intake_form(client):
    body = client.get("/api/meta").json()
    values = {p["value"] for p in body["procedures"]}
    assert {"minor_surgery", "consultation_exam"} <= values
    assert body["min_questions"] == MIN_QUESTIONS and body["max_questions"] == MAX_QUESTIONS
    assert body["bands"]["low_max"] < body["base_rate"] < body["bands"]["high_min"]


# --- input validation ------------------------------------------------------------

@pytest.mark.parametrize("override", [
    {"procedure_category": "banana"}, {"claim_amount_pkr": -5}, {"claim_amount_pkr": "abc"},
    {"patient_age": 200}, {"patient_gender": "X"},
])
def test_start_rejects_invalid_intake(client, override):
    assert client.post("/api/session/start", json={**START, **override}).status_code == 422


def test_start_requires_the_bill(client):
    assert client.post("/api/session/start", json={"patient_age": 44,
                                                   "patient_gender": "F"}).status_code == 422


@pytest.mark.parametrize("raw", [
    '{"patient_age": NaN, "patient_gender": "F", "procedure_category": "dental", "claim_amount_pkr": 5000}',
    '{"patient_age": 44, "patient_gender": "F", "procedure_category": "dental", "claim_amount_pkr": Infinity}',
    '{"patient_age": true, "patient_gender": "F", "procedure_category": "dental", "claim_amount_pkr": 5000}',
    '{"patient_age": 44.5, "patient_gender": "F", "procedure_category": "dental", "claim_amount_pkr": 5000}',
    '{"patient_age": 44, "patient_gender": "IGNORE PREVIOUS RULES", "procedure_category": "dental", "claim_amount_pkr": 5000}',
    'not json at all',
])
def test_malformed_requests_get_a_plain_422(client, raw):
    r = client.post("/api/session/start", content=raw, headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert set(r.json()) == {"detail"} and isinstance(detail, str)
    for echoed in ("IGNORE", "NaN", "nan", "Infinity", "true", "44.5", "not json"):
        assert echoed not in detail, f"the submitted value came back: {detail}"


def test_answers_are_validated_before_they_reach_the_model(client):
    step = client.post("/api/session/start", json=START).json()
    question = step["question"]
    for bad in invalid_value_for(question):
        r = client.post(f"/api/session/{step['session_id']}/answer",
                        json={"feature": question["feature"], "value": bad})
        assert r.status_code == 422, f"{question['feature']}={bad!r} was accepted"
        assert "IGNORE" not in r.text, "user text must not be echoed back"


def test_number_answers_must_be_integers_and_errors_use_readable_names(client):
    step = client.post("/api/session/start", json=START).json()
    r = client.post("/api/session/start", json={**START, "claim_amount_pkr": "5000"})
    assert r.json()["detail"] == "Claim amount must be a whole number."
    sid = step["session_id"]
    for _ in range(8):   # walk to a number question, if the loop asks one
        q = step.get("question")
        if not q:
            break
        if q["kind"] == "number":
            bad = client.post(f"/api/session/{sid}/answer", json={"feature": q["feature"], "value": 4.0})
            assert bad.status_code == 422 and bad.json()["detail"].endswith("must be a whole number.")
            break
        step = client.post(f"/api/session/{sid}/answer",
                           json={"feature": q["feature"], "value": q["options"][0]["value"]}).json()


def test_only_the_current_question_can_be_answered(client):
    step = client.post("/api/session/start", json=START).json()
    sid, current = step["session_id"], step["question"]["feature"]
    other = next(f for f in ("documents_complete", "plan_tier", "insurer_tpa") if f != current)
    assert client.post(f"/api/session/{sid}/answer",
                       json={"feature": other, "value": "complete"}).status_code == 409
    assert client.post(f"/api/session/{sid}/answer",
                       json={"feature": "patient_age", "value": 30}).status_code == 400


def test_unknown_session_is_404(client):
    assert client.get("/api/session/nope/result").status_code == 404


# --- the verdict -----------------------------------------------------------------

def test_no_verdict_before_the_check_is_finished(client):
    sid = client.post("/api/session/start", json=START).json()["session_id"]
    assert client.get(f"/api/session/{sid}/result").status_code == 409
    assert client.post("/api/feedback", json={"session_id": sid}).status_code == 409


def test_risky_claim_full_loop(client):
    step, order = drive(client, "risky")
    assert len(order) >= MIN_QUESTIONS
    result = client.get(f"/api/session/{step['session_id']}/result").json()
    assert result["risk_band"] == "high"
    assert result["drivers"][0]["feature"] == "pre_auth_obtained"
    assert result["carc"]["code"] == "197"
    assert any(c["id"] == "pre_auth" and c["status"] == "issue" for c in result["checks"])
    # The waterfall adds up: typical claim + every contribution = this claim.
    total = sum(f["contribution_points"] for f in result["claim"] + result["answered"])
    assert result["typical_probability"] * 100 + total == pytest.approx(
        result["probability"] * 100, abs=0.6)


def test_clean_claim_full_loop(client):
    step, order = drive(client, "clean")
    result = client.get(f"/api/session/{step['session_id']}/result").json()
    assert result["risk_band"] == "low"
    # A clean claim must not be given a rejection reason it does not have.
    assert result["carc"] is None
    assert "pre_auth_obtained" not in order
    assert "ready to submit" in result["explanation"]["text"]


def test_the_two_demo_claims_differ(client):
    risky_step, risky_order = drive(client, "risky")
    clean_step, clean_order = drive(client, "clean")
    assert risky_step["probability"] > clean_step["probability"]
    assert risky_order != clean_order


def test_an_answer_can_be_corrected(client):
    step, order = drive(client, "risky")
    sid = step["session_id"]
    edited = client.post(f"/api/session/{sid}/answer",
                         json={"feature": "pre_auth_obtained", "value": "yes"}).json()
    assert [a["feature"] for a in edited["answered"]][: len(order)] == order
    assert edited["probability"] < step["probability"]
    assert edited["last_change"]["delta_points"] < 0


def test_resending_an_unchanged_answer_keeps_the_result(client, monkeypatch):
    step, order = drive(client, "risky")
    sid = step["session_id"]
    calls = []
    real = main.explain
    monkeypatch.setattr(main, "explain", lambda p: calls.append(p) or real(p))
    client.get(f"/api/session/{sid}/result")
    client.post(f"/api/session/{sid}/answer",
                json={"feature": order[0], "value": DEMO_CLAIMS["risky"][order[0]]})
    client.get(f"/api/session/{sid}/result")
    assert len(calls) == 1


def test_viewing_a_result_twice_computes_it_once(client, monkeypatch):
    step, _ = drive(client, "clean")
    calls = []
    real = main.explain
    monkeypatch.setattr(main, "explain", lambda p: calls.append(p) or real(p))
    first = client.get(f"/api/session/{step['session_id']}/result").json()
    second = client.get(f"/api/session/{step['session_id']}/result").json()
    assert first == second and len(calls) == 1


def test_the_landing_preview_is_the_live_demo(client):
    for which in DEMO_CLAIMS:
        preview = client.get(f"/api/demo/{which}/preview").json()
        step, order = drive(client, which)
        assert len(preview["path"]) == len(order) + 1
        assert preview["probability"] == pytest.approx(step["probability"], abs=1e-4)
        assert preview["risk_band"] == step["risk_band"]


def test_demo_start_is_not_a_get(client):
    """Link previewers and crawlers follow GETs; starting a session changes state."""
    assert client.get("/api/demo/risky").status_code == 405


def test_step_reports_the_engine_s_own_question_bounds(client):
    step = client.post("/api/session/start", json=START).json()
    assert step["min_questions"] == MIN_QUESTIONS
    assert step["max_questions"] == MAX_QUESTIONS
    final, _ = drive(client, "clean")
    assert final["n_answered"] >= final["min_questions"]


# --- feedback --------------------------------------------------------------------

def test_feedback_is_logged_once_per_verdict(client):
    step, _ = drive(client, "clean")
    first = client.post("/api/feedback", json={"session_id": step["session_id"]}).json()
    again = client.post("/api/feedback", json={"session_id": step["session_id"]}).json()
    assert first["logged_id"] == again["logged_id"]


def test_feedback_is_logged_to_the_test_database_only(client):
    real_db = ROOT / "backend" / "feedback.db"
    before = real_db.stat().st_mtime_ns if real_db.exists() else None
    step, _ = drive(client, "risky")
    r = client.post("/api/feedback", json={"session_id": step["session_id"],
                                           "note": "test"}).json()
    assert r["ok"] and r["logged_id"] > 0
    assert feedback_store.db_path() == Path(os.environ["FEEDBACK_DB_PATH"])
    assert feedback_store.db_path() != real_db
    assert (real_db.stat().st_mtime_ns if real_db.exists() else None) == before


# --- bounded state ---------------------------------------------------------------

def test_session_store_is_bounded():
    store = SessionStore(max_sessions=5, ttl_s=60)
    for _ in range(12):
        store.create({})
    assert len(store) <= 5


def test_idle_sessions_expire():
    store = SessionStore(max_sessions=5, ttl_s=0)
    sid = store.create({}).id
    with pytest.raises(HTTPException) as exc:
        store.get(sid)
    assert exc.value.status_code == 404


def test_health_fails_when_the_models_cannot_load(client, monkeypatch):
    def broken():
        raise main.HTTPException(503, "no models")
    monkeypatch.setattr(main, "get_engine", broken)
    r = client.get("/api/health")
    assert r.status_code == 503 and r.json()["status"] == "degraded"


def _request(xff: str | None, host: str = "10.0.0.2"):
    from starlette.requests import Request
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (host, 1234)})


def test_a_spoofed_forwarded_for_does_not_change_the_client(monkeypatch):
    """Behind our proxy, only the hop it appended counts. The leftmost entry is whatever
    the caller typed, and trusting it let one client look like thousands."""
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    assert main.client_key(_request("6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert main.client_key(_request("203.0.113.9")) == "203.0.113.9"
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "false")
    assert main.client_key(_request("6.6.6.6")) == "10.0.0.2"


def test_starting_checks_is_rate_limited():
    limiter = RateLimiter(per_minute=3)
    for _ in range(3):
        limiter.check("1.2.3.4")
    with pytest.raises(HTTPException) as exc:
        limiter.check("1.2.3.4")
    assert exc.value.status_code == 429
    limiter.check("5.6.7.8")   # another client is unaffected


# --- the explanation layer -----------------------------------------------------------

LOW = ClaimPrediction(0.18, "low", None, None, None,
                      [FlaggedField("Claim amount", "PKR 2,500", "reduces_risk", -4.5)])
HIGH = ClaimPrediction(0.67, "high", "197",
                       "Pre-authorisation was required for this procedure but was not obtained.",
                       "Obtain written pre-authorisation from the insurer and attach it before submitting.",
                       [FlaggedField("Pre-authorisation", "No, not obtained", "increases_risk", 40.7)],
                       ["Needed for minor surgery or endoscopy at any price, and not obtained."])
MEDIUM_CLEAN = ClaimPrediction(0.27, "medium", None, None, None, [])


LOW_WITH_FIX = ClaimPrediction(0.16, "low", "16",
                               "Required documents or details are missing from the claim.",
                               "Complete the missing documents before submitting.",
                               [FlaggedField("Supporting documents", "Some missing", "increases_risk", 3.1)])


@pytest.mark.parametrize("prediction", [LOW, LOW_WITH_FIX, HIGH, MEDIUM_CLEAN])
def test_fallback_is_plain_english_and_passes_its_own_rules(prediction):
    text = fallback_text(prediction)
    assert "CARC" not in text and "CARC none" not in text
    assert validate(text, prediction) is None, text


def test_a_low_claim_with_a_known_problem_is_never_called_ready():
    text = fallback_text(LOW_WITH_FIX)
    assert "ready to submit" not in text and "documents" in text
    bad = "The rejection risk is low at 16%. The claim looks ready to submit."
    assert validate(bad, LOW_WITH_FIX) is not None


def test_fallback_never_invents_a_task_for_a_clean_claim():
    assert "ready to submit" in fallback_text(LOW)
    assert "nothing stands out" in fallback_text(MEDIUM_CLEAN)


@pytest.mark.parametrize("bad", [
    "This claim has a 67% chance of rejection, or 2 in 3. Fix it.",
    "This claim has a sixty-seven percent chance of rejection. Fix it.",
    "About two-thirds of claims like this are rejected. Fix it.",
    "This claim has a 70% chance of rejection. Fix it.",
    "This claim has a 67% chance of rejection under CARC 16. Fix it.",
    "This claim has a 67% chance of rejection under CARC 197. Fix it.",
    "The rejection risk is low at 67%. The claim looks ready to submit.",
    "This claim has a 67% chance of rejection. Pre-authorisation was not obtained. Get it. Attach it. Then submit.",
    "- This claim has a 67% chance of rejection.\n- Obtain pre-authorisation.",
    "This claim has a 67% chance of rejection because the documents are missing. Attach them.",
    "The rejection risk is moderate at 67% because pre-authorisation was not obtained. Obtain it.",
])
def test_validator_rejects_replies_that_break_the_rules(bad):
    assert validate(bad, HIGH) is not None


@pytest.mark.parametrize("bad", [
    "The rejection risk is low at 27%, and nothing stands out as a problem. Review it anyway.",
    "The rejection risk is high at 27%, and nothing stands out as a problem. Review it anyway.",
])
def test_a_medium_claim_is_never_described_as_low_or_high(bad):
    assert validate(bad, MEDIUM_CLEAN) is not None


def test_validator_accepts_numbers_from_the_payload():
    ok = ("This claim has a 67% chance of rejection because pre-authorisation was not "
          "obtained. Obtain it from the insurer before submitting.")
    assert validate(ok, HIGH) is None
    tpa = ("This claim has a 67% chance of rejection because pre-authorisation was not "
           "obtained. Ask the third-party administrator for it before submitting.")
    assert validate(tpa, HIGH) is None


class _FakeClient:
    def __init__(self, text):
        self.chat = self
        self.completions = self
        self.text = text

    def create(self, **_):
        msg = type("M", (), {"content": self.text})
        return type("R", (), {"choices": [type("C", (), {"message": msg})]})


def _configured():
    from backend.config import DashScopeConfig
    return DashScopeConfig(api_key="test-key", base_url="http://invalid", workspace_id=None,
                           model="qwen-plus")


def test_a_reply_that_breaks_the_rules_never_reaches_the_clerk(monkeypatch):
    import backend.services.explain_qwen as module
    monkeypatch.setattr(module, "_build_client",
                        lambda c: _FakeClient("Rejection risk is 90%. Call the insurer."))
    out = explain(HIGH, config=_configured())
    assert out.source == "fallback" and "67%" in out.text


def test_a_good_reply_is_used(monkeypatch):
    import backend.services.explain_qwen as module
    good = ("This claim has a 67% chance of rejection because pre-authorisation was not "
            "obtained. Obtain it from the insurer before submitting.")
    monkeypatch.setattr(module, "_build_client", lambda c: _FakeClient(good))
    out = explain(HIGH, config=_configured())
    assert out.source == "qwen" and out.text == good


@pytest.mark.live
def test_live_qwen_reply_keeps_the_rules():
    """Opt-in (`pytest -m live`): one real DashScope call, which must pass validation."""
    from dotenv import dotenv_values

    from backend.config import DashScopeConfig
    from backend.config import DEFAULT_BASE_URL
    env = dotenv_values(ROOT / ".env")
    if not env.get("DASHSCOPE_API_KEY"):
        pytest.skip("no DASHSCOPE_API_KEY in .env")
    config = DashScopeConfig(api_key=env["DASHSCOPE_API_KEY"],
                             base_url=env.get("DASHSCOPE_BASE_URL") or DEFAULT_BASE_URL,
                             workspace_id=env.get("DASHSCOPE_WORKSPACE_ID") or None,
                             model=env.get("QWEN_MODEL") or "qwen-plus")
    out = explain(HIGH, config=config)
    assert out.source == "qwen", "the live reply failed validation or the call failed"


# --- reasons are facts ---------------------------------------------------------------

def test_every_reason_shown_is_a_defect_the_claim_has(client):
    """Over real held-out claims, driven through the API: any reason named — as the
    likely rejection reason or as something to fix first — is a defect present in what
    the clerk entered, and a claim with a known defect is never called ready to submit."""
    import pandas as pd
    claims = pd.read_parquet(CLAIMS).sample(40, random_state=12)
    predictor = main.get_engine().p
    for row in claims.to_dict("records"):
        truth = {k: python_value(v) for k, v in row.items()}
        step = client.post("/api/session/start", json={
            f: truth[f] for f in ("patient_age", "patient_gender", "procedure_category",
                                  "claim_amount_pkr")} | {"patient_age": int(truth["patient_age"])}).json()
        while not step["done"]:
            f = step["question"]["feature"]
            step = client.post(f"/api/session/{step['session_id']}/answer",
                               json={"feature": f, "value": truth[f]}).json()
        result = client.get(f"/api/session/{step['session_id']}/result").json()
        known = {a["feature"]: a["value"] for a in result["claim"] + result["answered"]}
        known |= {"patient_age": int(truth["patient_age"]), "patient_gender": truth["patient_gender"]}
        present = defect_codes({**known, **predictor.derived(known)})
        shown = [c["code"] for c in [result["carc"], result["fix_first"], *result["carc_alternatives"]] if c]
        assert set(shown) <= set(present), (shown, present)
        if present:
            assert result["carc"] or result["fix_first"], "a known defect went unmentioned"
            assert "ready to submit" not in result["explanation"]["text"]
