"""Reading documents: Qwen-VL reads, the clerk confirms, the model decides.

Offline: the model call is replaced by a canned reply, so these pin what the app does
with whatever Qwen-VL says — validation, privacy, and the guardrails around it.
"""

from __future__ import annotations

import io
import json
from base64 import b64encode
from datetime import date

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import backend.main as main
import backend.services.documents as documents
from backend.config import DashScopeConfig
from backend.main import app
from backend.services.adaptive import AdaptiveEngine
from backend.services.checks import document_checks
from backend.services.documents import (DocumentError, DocumentSummary, Reading,
                                        ReaderUnavailable, interpret, prepare_pages,
                                        read_documents)

TODAY = date(2026, 10, 24)

REPLY = {
    "documents": [{"file": 1, "type": "bill", "date": "2026-10-02", "amount_pkr": 85000,
                   "patient_age": 44, "patient_gender": "F",
                   "procedure": "Laparoscopic cholecystectomy", "diagnosis": "Gallstones"},
                  {"file": 2, "type": "discharge_summary", "date": "2026-10-03"}],
    "fields": {
        "patient_age": {"value": 44, "evidence": "Age: 44", "file": 1},
        "patient_gender": {"value": "Female", "evidence": "Sex: Female", "file": 1},
        "procedure_category": {"value": "minor_surgery", "evidence": "Laparoscopic cholecystectomy", "file": 1},
        "claim_amount_pkr": {"value": "85,000", "evidence": "TOTAL 85,000", "file": 1},
        "treatment_date": {"value": "2026-10-02", "evidence": "Date of service: 02-Oct-2026", "file": 1},
        "insurer_tpa": {"value": "Rehnuma Insurance", "evidence": "Insurer: Rehnuma Insurance", "file": 1},
        "plan_tier": {"value": "Standard", "evidence": "Plan: Standard", "file": 1},
        "provider_network_status": {"value": "in_network", "evidence": "Panel: In network", "file": 1},
        "diagnosis_category": {"value": "general_unspecified", "evidence": "Gallstones", "file": 2},
        "is_emergency": None,
        "pre_auth_reference": None,
    },
}


@pytest.fixture(scope="module")
def predictor():
    return AdaptiveEngine().p


def png(width=40, height=30, exif=False) -> bytes:
    img = Image.new("RGB", (width, height), "white")
    out = io.BytesIO()
    if exif:
        meta = Image.Exif()
        meta[0x010F] = "PhoneMaker"          # Make
        img.save(out, "JPEG", exif=meta)
    else:
        img.save(out, "PNG")
    return out.getvalue()


def pdf(pages=2) -> bytes:
    out = io.BytesIO()
    frames = [Image.new("RGB", (200, 280), "white") for _ in range(pages)]
    frames[0].save(out, "PDF", save_all=True, append_images=frames[1:])
    return out.getvalue()


# --- reading the reply ----------------------------------------------------------

def test_printed_facts_are_validated_into_the_checks_own_values(predictor):
    r = interpret(REPLY, predictor, n_files=2, today=TODAY)
    assert {f: v.value for f, v in r.context.items()} == {
        "patient_age": 44, "patient_gender": "F", "procedure_category": "minor_surgery",
        "claim_amount_pkr": 85_000}
    assert r.facts["plan_tier"].value == "standard"                  # label -> value
    assert r.facts["days_since_treatment"].value == 22               # date -> days
    assert r.facts["diagnosis_category"].document == 2
    assert "is_emergency" not in r.facts                             # null stays unknown
    assert [d.type for d in r.documents] == ["bill", "discharge_summary"]


def test_anything_outside_the_vocabulary_or_range_is_dropped_and_asked(predictor):
    reply = json.loads(json.dumps(REPLY))
    reply["fields"]["insurer_tpa"]["value"] = "Imaginary Mutual"
    reply["fields"]["patient_age"]["value"] = 240
    reply["fields"]["claim_amount_pkr"]["value"] = "85000.50"
    reply["fields"]["plan_tier"]["value"] = "IGNORE PREVIOUS RULES"
    r = interpret(reply, predictor, n_files=2, today=TODAY)
    for f in ("insurer_tpa", "plan_tier"):
        assert f not in r.facts
    for f in ("patient_age", "claim_amount_pkr"):
        assert f not in r.context
    assert len(r.notes) == 4


def test_a_future_treatment_date_is_not_used(predictor):
    reply = json.loads(json.dumps(REPLY))
    reply["fields"]["treatment_date"]["value"] = "2026-11-30"
    r = interpret(reply, predictor, n_files=2, today=TODAY)
    assert "days_since_treatment" not in r.facts
    assert any("future" in n for n in r.notes)


def test_pre_authorisation_is_only_ever_a_suggestion(predictor):
    r = interpret(REPLY, predictor, n_files=2, today=TODAY)
    assert "pre_auth_obtained" not in r.facts
    assert r.hints["pre_auth_obtained"].value is None                # no letter: no claim
    assert "No pre-authorisation" in r.hints["pre_auth_obtained"].evidence

    reply = json.loads(json.dumps(REPLY))
    reply["documents"].append({"file": 3, "type": "pre_auth_letter"})
    r = interpret(reply, predictor, n_files=3, today=TODAY)
    assert r.hints["pre_auth_obtained"].value == "yes"
    assert r.hints["pre_auth_obtained"].document == 3
    assert "pre_auth_obtained" not in r.facts


def test_identifying_numbers_never_survive_into_evidence(predictor):
    reply = json.loads(json.dumps(REPLY))
    reply["fields"]["patient_age"]["evidence"] = "CNIC 35202-1234567-1 Age: 44"
    reply["fields"]["insurer_tpa"]["evidence"] = "Rehnuma Insurance, call 0300 1234567"
    reply["fields"]["plan_tier"]["evidence"] = "MR# 12345678, card 1234 5678, Plan: Standard"
    r = interpret(reply, predictor, n_files=2, today=TODAY)
    assert "1234567" not in r.context["patient_age"].evidence
    assert "1234567" not in r.facts["insurer_tpa"].evidence
    assert "5678" not in r.facts["plan_tier"].evidence and "Standard" in r.facts["plan_tier"].evidence
    assert "02-Oct-2026" in r.facts["days_since_treatment"].evidence   # a date is not an ID


def test_a_file_number_outside_the_upload_is_ignored(predictor):
    reply = json.loads(json.dumps(REPLY))
    reply["fields"]["plan_tier"]["file"] = 9
    assert interpret(reply, predictor, n_files=2, today=TODAY).facts["plan_tier"].document is None


# --- files ----------------------------------------------------------------------

def test_photos_are_re_encoded_without_their_metadata():
    pages, notes = prepare_pages([("photo.jpg", png(exif=True))])
    assert len(pages) == 1 and not notes
    assert pages[0][1][:3] == b"\xff\xd8\xff"
    assert not Image.open(io.BytesIO(pages[0][1])).getexif()


def test_pdfs_become_page_images_up_to_the_page_budget():
    pages, notes = prepare_pages([("a.pdf", pdf(2)), ("b.pdf", pdf(3))])
    assert [n for n, _ in pages] == [1, 1, 2]
    assert len(notes) == 1 and "Document 2" in notes[0]


def test_file_names_are_never_echoed():
    """A file name can be "Ahmed_Khan_CNIC_35202-1234567-1.pdf"."""
    with pytest.raises(DocumentError) as err:
        prepare_pages([("Ahmed_Khan_CNIC.txt", b"hello there")])
    assert "Ahmed" not in str(err.value) and "Document 1" in str(err.value)


def test_a_pdf_holding_a_huge_image_is_refused_before_rendering():
    out = io.BytesIO()
    Image.new("1", (8000, 6000)).save(out, "PDF")       # 48 MP, small on disk
    with pytest.raises(DocumentError, match="too large"):
        prepare_pages([("scan.pdf", out.getvalue())])


def test_a_reply_of_the_wrong_shape_ends_in_manual_entry_not_an_error(predictor, monkeypatch):
    monkeypatch.setattr(documents, "_call_model",
                        lambda *a: json.dumps({"documents": [{"file": 1, "type": ["bill"]}],
                                               "fields": {"plan_tier": {"value": {"x": 1}}}}))
    config = DashScopeConfig(api_key="test", base_url="http://qwen.invalid",
                             workspace_id=None, model="qwen-plus")
    reading = read_documents([("a.png", png())], predictor, config=config, today=TODAY)
    assert reading.documents[0].type == "other" and "plan_tier" not in reading.facts
    monkeypatch.setattr(documents, "interpret", lambda *a: 1 / 0)
    with pytest.raises(ReaderUnavailable, match="by hand"):
        read_documents([("a.png", png())], predictor, config=config, today=TODAY)


@pytest.mark.parametrize("files, message", [
    ([], "at least one"),
    ([("x", png())] * 4, "at most"),
    ([("notes.txt", b"hello there")], "not a photo"),
    ([("broken.pdf", b"%PDF-1.4 garbage")], "could not be opened"),
    ([("huge.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024))], "larger than"),
])
def test_unusable_uploads_are_refused_with_a_clear_reason(files, message):
    with pytest.raises(DocumentError, match=message):
        prepare_pages(files)


def test_without_qwen_reading_is_unavailable_not_broken(predictor):
    with pytest.raises(ReaderUnavailable, match="by hand"):
        read_documents([("bill.png", png())], predictor)


# --- the API ----------------------------------------------------------------------

@pytest.fixture()
def reader(monkeypatch):
    """Qwen-VL configured, answering with REPLY."""
    monkeypatch.setattr(documents, "load_dashscope_config", lambda: DashScopeConfig(
        api_key="test", base_url="http://qwen.invalid", workspace_id=None, model="qwen-plus"))
    monkeypatch.setattr(documents, "_call_model", lambda *a: json.dumps(REPLY))
    monkeypatch.setattr(documents, "date", type("D", (), {"today": staticmethod(lambda: TODAY),
                                                         "fromisoformat": date.fromisoformat}))
    monkeypatch.setattr(main.read_limiter, "per_minute", 100_000)
    monkeypatch.setattr(main.start_limiter, "per_minute", 100_000)
    monkeypatch.setattr(main.answer_limiter, "per_minute", 100_000)
    return TestClient(app)


def upload(*blobs: bytes) -> dict:
    return {"files": [{"name": f"doc{i}.png", "data": b64encode(b).decode()}
                      for i, b in enumerate(blobs, 1)]}


def test_reading_returns_what_was_read_for_the_clerk_to_confirm(reader):
    r = reader.post("/api/documents/read", json=upload(png(), png()))
    assert r.status_code == 200, r.text
    body = r.json()
    assert {c["feature"] for c in body["context"]} == {
        "patient_age", "patient_gender", "procedure_category", "claim_amount_pkr"}
    assert "pre_auth_obtained" in {h["feature"] for h in body["hints"]}
    assert "pre_auth_obtained" not in {f["feature"] for f in body["facts"]}


def test_a_check_started_from_documents_still_asks_pre_authorisation_first(reader):
    body = reader.post("/api/documents/read", json=upload(png(), png())).json()
    facts = [f["feature"] for f in body["facts"]]
    start = {c["feature"]: c["value"] for c in body["context"]}
    step = reader.post("/api/session/start",
                       json={**start, "reading_id": body["reading_id"], "use_facts": facts}).json()
    # Five facts already answered — past the minimum — yet a surgery needing approval
    # is not judged until the clerk has answered for it.
    assert step["n_answered"] == len(facts) >= 3
    assert step["done"] is False
    assert step["question"]["feature"] == "pre_auth_obtained"
    assert "No pre-authorisation" in step["question"]["evidence"]
    assert {a["source"] for a in step["answered"]} == {"document"}
    assert all(a["evidence"] for a in step["answered"])
    assert {c["source"] for c in step["claim"]} == {"document"}
    assert {q["feature"] for q in step["document_questions"]} == set(facts)
    assert [d["type"] for d in step["documents"]] == ["bill", "discharge_summary"]


def test_only_kept_facts_are_used_and_an_edit_makes_a_fact_the_clerks(reader):
    body = reader.post("/api/documents/read", json=upload(png())).json()
    start = {c["feature"]: c["value"] for c in body["context"]}
    step = reader.post("/api/session/start", json={
        **start, "reading_id": body["reading_id"], "use_facts": ["plan_tier"]}).json()
    assert [a["feature"] for a in step["answered"]] == ["plan_tier"]
    sid = step["session_id"]
    step = reader.post(f"/api/session/{sid}/answer",
                       json={"feature": "plan_tier", "value": "premium"}).json()
    plan = next(a for a in step["answered"] if a["feature"] == "plan_tier")
    assert plan["source"] == "answer" and plan["evidence"] is None


def test_a_bill_field_the_clerk_changed_is_not_credited_to_the_documents(reader):
    body = reader.post("/api/documents/read", json=upload(png())).json()
    start = {c["feature"]: c["value"] for c in body["context"]}
    step = reader.post("/api/session/start", json={
        **start, "claim_amount_pkr": 60_000, "reading_id": body["reading_id"]}).json()
    sources = {c["feature"]: c["source"] for c in step["claim"]}
    assert sources == {"procedure_category": "document", "claim_amount_pkr": "bill"}


@pytest.mark.parametrize("extra, message", [
    ({"use_facts": ["plan_tier"]}, "need the reading"),
    ({"reading_id": "not-a-reading"}, "too long ago"),
])
def test_facts_cannot_be_injected_without_a_real_reading(reader, extra, message):
    r = reader.post("/api/session/start", json={
        "patient_age": 44, "patient_gender": "F", "procedure_category": "minor_surgery",
        "claim_amount_pkr": 85_000, **extra})
    assert r.status_code == 422 and message in r.json()["detail"]


def test_a_fact_the_reading_does_not_have_is_refused(reader):
    body = reader.post("/api/documents/read", json=upload(png())).json()
    start = {c["feature"]: c["value"] for c in body["context"]}
    r = reader.post("/api/session/start", json={
        **start, "reading_id": body["reading_id"], "use_facts": ["pre_auth_obtained"]})
    assert r.status_code == 422


def test_bad_uploads_get_a_message_not_a_crash(reader):
    r = reader.post("/api/documents/read", json={"files": [{"name": "x.png", "data": "%%%"}]})
    assert r.status_code == 422 and "could not be read" in r.json()["detail"]
    r = reader.post("/api/documents/read", json=upload(b"plain text"))
    assert r.status_code == 422 and "not a photo" in r.json()["detail"]


def test_without_qwen_the_endpoint_says_to_enter_the_bill_by_hand(monkeypatch):
    monkeypatch.setattr(main.read_limiter, "per_minute", 100_000)
    r = TestClient(app).post("/api/documents/read", json=upload(png()))
    assert r.status_code == 503 and "by hand" in r.json()["detail"]


# --- checks across the documents --------------------------------------------------


SURGERY = {"patient_age": 44, "patient_gender": "F", "procedure_category": "minor_surgery",
           "claim_amount_pkr": 85_000}
NEEDS_APPROVAL = {"pre_auth_required_for_procedure": 1}


def reading_of(*docs: DocumentSummary) -> Reading:
    return Reading(id="r", context={}, facts={}, hints={}, documents=list(docs))


def by_id(checks):
    return {c.id: c for c in checks}


BILL = DocumentSummary(1, "bill", "Itemised bill", date="2026-10-12", amount_pkr=85_000,
                       patient_age=44, patient_gender="F")


def test_missing_papers_are_named_but_only_warned_about():
    checks = by_id(document_checks(SURGERY, NEEDS_APPROVAL, reading_of(BILL)))
    assert checks["doc_required"].status == "warn"
    assert "discharge summary" in checks["doc_required"].detail
    assert "pre-authorisation approval" in checks["doc_required"].detail


def test_an_approval_for_less_than_was_billed_is_a_problem():
    letter = DocumentSummary(2, "pre_auth_letter", "Pre-authorisation approval",
                             date="2026-10-08", amount_pkr=70_000, patient_age=44, patient_gender="F")
    checks = by_id(document_checks(SURGERY, NEEDS_APPROVAL, reading_of(BILL, letter)))
    assert checks["doc_amount"].status == "issue"
    assert "PKR 15,000 above" in checks["doc_amount"].detail
    assert checks["doc_dates"].status == "ok"
    assert checks["doc_patient"].status == "ok"


def test_papers_that_disagree_about_the_patient_or_dates_are_caught():
    late = DocumentSummary(2, "pre_auth_letter", "Pre-authorisation approval",
                           date="2026-10-20", amount_pkr=90_000, patient_age=45, patient_gender="F")
    checks = by_id(document_checks(SURGERY, NEEDS_APPROVAL, reading_of(BILL, late)))
    assert checks["doc_patient"].status == "issue" and "45 years" in checks["doc_patient"].detail
    assert checks["doc_dates"].status == "issue" and "after the treatment" in checks["doc_dates"].detail
    assert checks["doc_amount"].status == "ok"


def test_a_document_problem_reaches_the_verdict_without_inventing_a_reason(reader, monkeypatch):
    reply = json.loads(json.dumps(REPLY))
    reply["documents"][1] = {"file": 2, "type": "pre_auth_letter", "date": "2026-09-30",
                             "amount_pkr": 70000, "patient_age": 44, "patient_gender": "F"}
    monkeypatch.setattr(documents, "_call_model", lambda *a: json.dumps(reply))
    monkeypatch.setattr(main.result_limiter, "per_minute", 100_000)
    body = reader.post("/api/documents/read", json=upload(png(), png())).json()
    assert next(h for h in body["hints"])["value"] == "yes"
    start = {c["feature"]: c["value"] for c in body["context"]}
    step = reader.post("/api/session/start", json={
        **start, "reading_id": body["reading_id"],
        "use_facts": [f["feature"] for f in body["facts"]]}).json()
    sid = step["session_id"]
    while not step["done"]:
        q = step["question"]
        value = "yes" if q["feature"] == "pre_auth_obtained" else (
            q["options"][0]["value"] if q["kind"] == "choice" else 0)
        step = reader.post(f"/api/session/{sid}/answer",
                           json={"feature": q["feature"], "value": value}).json()
    result = reader.get(f"/api/session/{sid}/result").json()
    doc = {c["id"]: c for c in result["checks"]}
    assert doc["doc_amount"]["status"] == "issue"
    assert result["carc"] is None or result["carc"]["code"] != "198"     # never invented
    assert "ready to submit" not in result["explanation"]["text"].lower()


def test_a_missing_required_paper_keeps_a_claim_from_being_called_ready():
    from backend.services.checks import blocks_submission
    checks = by_id(document_checks(SURGERY, NEEDS_APPROVAL, reading_of(BILL)))
    assert blocks_submission(checks["doc_required"])
    ok = document_checks(SURGERY, {}, reading_of(BILL, DocumentSummary(
        2, "discharge_summary", "Discharge summary", date="2026-10-12")))
    assert not any(blocks_submission(c) for c in ok)


def test_documents_never_stand_in_for_the_clerks_own_answers(reader):
    """Read facts are not answers the clerk gave: the check still asks at least the
    minimum, and they count towards neither the minimum nor the cap."""
    body = reader.post("/api/documents/read", json=upload(png(), png())).json()
    start = {c["feature"]: c["value"] for c in body["context"]}
    step = reader.post("/api/session/start", json={
        **start, "reading_id": body["reading_id"],
        "use_facts": [f["feature"] for f in body["facts"]]}).json()
    sid, asked = step["session_id"], 0
    while not step["done"]:
        q = step["question"]
        value = "yes" if q["feature"] == "pre_auth_obtained" else (
            q["options"][0]["value"] if q["kind"] == "choice" else 0)
        step = reader.post(f"/api/session/{sid}/answer",
                           json={"feature": q["feature"], "value": value}).json()
        asked += 1
    assert asked >= 3 and step["n_asked"] == asked
    assert step["n_answered"] == asked + len(body["facts"])


def test_document_started_checks_keep_the_band_guarantees_on_validation():
    """The bands were fitted on question-only checks. A check that starts from document
    facts must still clear "low" claims that are accepted at least 85% of the time and put
    in "high" claims more likely rejected than not (ml/simulate.py's constraints)."""
    import numpy as np
    import pandas as pd

    from backend.services.documents import FACT_FIELDS
    from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES, TARGET
    from ml.simulate import MIN_HIGH_BAND_REJECTION, MIN_LOW_BAND_APPROVAL, _python
    from ml.train import split
    from tests.helpers import CLAIMS

    df = pd.read_parquet(CLAIMS)
    _, idx_val, _ = split(df)
    engine = AdaptiveEngine()
    low, high = [], []
    for row in df.loc[idx_val].to_dict("records"):
        truth = {f: _python(row[f]) for f in ASKABLE_FEATURES + CONTEXT_FEATURES}
        preset = [f for f in FACT_FIELDS if truth.get(f) is not None]
        answers = {**{f: truth[f] for f in CONTEXT_FEATURES}, **{f: truth[f] for f in preset}}
        while not (step := engine.next_step(answers, (), preset)).done:
            answers[step.question.feature] = truth[step.question.feature]
        {"low": low, "high": high}.get(step.risk_band, []).append(bool(row[TARGET]))
    assert 1 - np.mean(low) >= MIN_LOW_BAND_APPROVAL
    assert np.mean(high) >= MIN_HIGH_BAND_REJECTION


def test_the_explanation_names_the_problem_the_action_box_shows():
    from backend.main import _issues
    from backend.schemas import CheckOut
    checks = [CheckOut(id="doc_required", label="Required documents", status="warn",
                       detail="Not among the uploaded documents: a discharge summary. Attach it."),
              CheckOut(id="doc_amount", label="Within the approved amount", status="issue",
                       detail="Billed PKR 85,000 but approved for PKR 70,000: PKR 15,000 above "
                              "the approval. Ask the insurer for a revised approval first.")]
    issues = _issues(checks)
    assert issues[0].startswith("Billed PKR 85,000")           # the problem before the warning
    assert all(i.count(". ") == 0 for i in issues)              # one sentence each, for Qwen
