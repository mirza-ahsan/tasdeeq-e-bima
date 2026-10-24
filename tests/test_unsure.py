"""'Not sure': a question can be left open, but never pre-authorisation, and a claim with
open questions is never called ready to submit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import backend.main as main
from backend.main import app
from backend.services.adaptive import MAX_QUESTIONS
from backend.services.explain_qwen import ClaimPrediction, fallback_text, validate

SURGERY = {"patient_age": 44, "patient_gender": "F", "procedure_category": "minor_surgery",
           "claim_amount_pkr": 85_000}
CONSULT = {"patient_age": 30, "patient_gender": "M", "procedure_category": "consultation_exam",
           "claim_amount_pkr": 2_500}


@pytest.fixture()
def client(monkeypatch):
    for name in ("start_limiter", "answer_limiter", "result_limiter"):
        monkeypatch.setattr(getattr(main, name), "per_minute", 100_000)
    return TestClient(app)


def answer(client, sid, feature, value=None, not_sure=False):
    return client.post(f"/api/session/{sid}/answer",
                       json={"feature": feature, "value": value, "not_sure": not_sure})


def test_pre_authorisation_is_never_skippable(client):
    step = client.post("/api/session/start", json=SURGERY).json()
    assert step["question"]["feature"] == "pre_auth_obtained"
    assert step["question"]["can_skip"] is False
    r = answer(client, step["session_id"], "pre_auth_obtained", not_sure=True)
    assert r.status_code == 422 and "has to be answered" in r.json()["detail"]


def test_a_skipped_question_stays_unknown_and_is_not_asked_again(client):
    step = client.post("/api/session/start", json=SURGERY).json()
    sid = step["session_id"]
    step = answer(client, sid, "pre_auth_obtained", "yes").json()
    skipped = step["question"]["feature"]
    assert step["question"]["can_skip"] is True
    before = step["n_answered"]
    step = answer(client, sid, skipped, not_sure=True).json()
    assert step["n_answered"] == before                       # not an answer
    assert [u["feature"] for u in step["unsure"]] == [skipped]
    assert step["last_change"]["value_label"] == "Not sure"
    seen = []
    while not step["done"]:
        seen.append(step["question"]["feature"])
        f = step["question"]["feature"]
        step = answer(client, sid, f, step["question"]["options"][0]["value"]
                      if step["question"]["kind"] == "choice" else 5).json()
    assert skipped not in seen


def test_only_the_current_question_can_be_left_open(client):
    step = client.post("/api/session/start", json=SURGERY).json()
    r = answer(client, step["session_id"], "plan_tier", not_sure=True)
    assert r.status_code == 409


def test_skipping_everything_still_ends_within_the_cap(client):
    step = client.post("/api/session/start", json=CONSULT).json()
    sid, turns = step["session_id"], 0
    while not step["done"]:
        step = answer(client, sid, step["question"]["feature"], not_sure=True).json()
        turns += 1
        assert turns <= MAX_QUESTIONS
    result = client.get(f"/api/session/{sid}/result").json()
    assert len(result["unsure"]) == turns
    # Not a verdict: no band is claimed from zero answers, and Qwen is not asked to word one.
    assert result["stop_reason"] == "too_few_answers"
    assert result["explanation"]["text"].startswith("Too few questions were answered")
    assert result["n_asked"] == turns


def test_a_question_left_open_can_be_answered_later(client):
    step = client.post("/api/session/start", json=CONSULT).json()
    sid = step["session_id"]
    first = step["question"]
    step = answer(client, sid, first["feature"], not_sure=True).json()
    value = first["options"][0]["value"] if first["kind"] == "choice" else 5
    step = answer(client, sid, first["feature"], value).json()
    assert step["unsure"] == []
    assert first["feature"] in [a["feature"] for a in step["answered"]]


def test_an_open_question_keeps_a_low_risk_claim_from_being_called_ready():
    p = ClaimPrediction(rejection_probability=0.12, risk_band="low", carc_code=None,
                        carc_reason=None, staff_action=None, unconfirmed=["Plan tier"])
    text = fallback_text(p)
    assert "plan tier is not confirmed" in text and "ready to submit" not in text
    assert validate(text, p) is None
    assert validate("The rejection risk is low at 12%. The claim looks ready to submit.", p) \
        == "calls a claim with unconfirmed details ready to submit"
    clear = ClaimPrediction(rejection_probability=0.12, risk_band="low", carc_code=None,
                            carc_reason=None, staff_action=None)
    assert "ready to submit" in fallback_text(clear)


def test_every_way_of_saying_send_it_is_caught_while_something_is_open():
    p = ClaimPrediction(rejection_probability=0.12, risk_band="low", carc_code=None,
                        carc_reason=None, staff_action=None, unconfirmed=["Plan tier"])
    for phrase in ("The claim is ready for submission.", "It can be submitted now.",
                   "You are good to go.", "It is safe to submit."):
        assert validate(f"The rejection risk is low at 12%. {phrase}", p) is not None, phrase
