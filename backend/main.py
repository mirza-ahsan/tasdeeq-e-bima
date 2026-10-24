"""FastAPI application.

Session state lives in memory for the life of the process. There is no auth, no user
accounts and no persistence beyond the session, which is deliberate: this is a demo
prototype. The one thing that does persist is the feedback log, because demonstrating
the biller-in-the-loop idea is part of the point.

What the API guarantees, whoever calls it:
  * every value is checked against the vocabulary and ranges the model was trained on
    before it reaches a session; nothing unknown is silently scored as "unknown"
  * only the question currently being asked, or one already answered (an edit), can be
    answered, and a verdict exists only once the loop has finished
  * sessions are bounded in number and expire when idle, and starting one is rate
    limited per client, so the store cannot be grown without bound
  * a result is computed once per state of the claim, so viewing it again never makes a
    second Qwen call
"""

from __future__ import annotations

import binascii
import json
import logging
import threading
import time
import uuid
from base64 import b64decode
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from backend import feedback_store
from backend.config import cors_origins, load_dashscope_config, trust_proxy_headers
from backend.demo_claims import DEMO_CLAIMS
from backend.predictor import Attribution, get_predictor
from backend.schemas import (AnswerRequest, BandsOut, CarcOut, ChangeOut, CheckOut,
                             DocumentOut, DocumentsRequest, DriverOut, ExplanationOut,
                             FeedbackRequest, FeedbackResponse, FieldOut, HealthOut, MetaOut,
                             OptionOut, PatientOut, PreviewOut, PreviewPoint, ProcedureOption,
                             QuestionOut, ReadingOut, ReadValueOut, ResultOut, StartRequest,
                             StepOut)
from backend.services.adaptive import (FEATURE_LABELS, MAX_QUESTIONS, MIN_QUESTIONS,
                                       NUMBER_LIMITS, VALUE_LABELS, AdaptiveEngine,
                                       InvalidAnswer, Question, Step, feature_label,
                                       validate_answer, value_label)
from backend.services.checks import build_checks
from backend.services.documents import (DocumentError, Reading, ReaderUnavailable,
                                        ReadingStore, ReadValue, read_documents)
from backend.services.explain_qwen import ClaimPrediction, FlaggedField, explain
from ml.features import ASKABLE_FEATURES, BILL_FEATURES, CONTEXT_FEATURES, defect_codes

logger = logging.getLogger(__name__)

VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parent.parent
CARC_CODES = {c["code"]: c for c in
              json.loads((ROOT / "data" / "carc_codes.json").read_text())["codes"]}

MAX_SESSIONS = 500
SESSION_TTL_S = 30 * 60
# Per client, per minute. Starting a check is the expensive, store-growing action; the
# others only need to stop one client draining the shared Qwen budget or the log.
STARTS_PER_MINUTE = 30
ANSWERS_PER_MINUTE = 120
RESULTS_PER_MINUTE = 30
FEEDBACK_PER_MINUTE = 10
READS_PER_MINUTE = 6       # each read is a multi-image Qwen-VL call

# Presentation threshold: below this, a contribution is noise and showing it misleads.
MIN_CONTRIBUTION_POINTS = 1.0     # percentage points


# --- sessions ----------------------------------------------------------------

@dataclass
class Session:
    id: str
    answers: dict
    order: list[str] = field(default_factory=list)      # questions, in the order asked
    last_step: Step | None = None
    last_change: ChangeOut | None = None
    result: tuple[str, ResultOut] | None = None         # (claim state, cached result)
    feedback: tuple[str, int] | None = None             # (claim state, logged row)
    # Started from read documents: the reading, and which known fields are still exactly
    # as read (an edit by the clerk makes a field theirs).
    reading: Reading | None = None
    from_documents: set[str] = field(default_factory=set)
    last_seen: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def state_key(self) -> str:
        return json.dumps(self.answers, sort_keys=True, default=str)


class SessionStore:
    """Bounded, expiring, thread-safe. FastAPI runs these handlers on a thread pool."""

    def __init__(self, max_sessions: int = MAX_SESSIONS, ttl_s: float = SESSION_TTL_S):
        self.max_sessions, self.ttl_s = max_sessions, ttl_s
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, answers: dict) -> Session:
        with self._lock:
            self._evict()
            session = Session(id=str(uuid.uuid4()), answers=answers)
            self._sessions[session.id] = session
            return session

    def get(self, session_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and time.monotonic() - session.last_seen > self.ttl_s:
                del self._sessions[session_id]
                session = None
            if session is None:
                raise HTTPException(404, "This check has expired or was never started. "
                                         "Start a new one.")
            session.last_seen = time.monotonic()
            return session

    def _evict(self) -> None:
        now = time.monotonic()
        for sid in [s.id for s in self._sessions.values() if now - s.last_seen > self.ttl_s]:
            del self._sessions[sid]
        overflow = len(self._sessions) - self.max_sessions + 1
        if overflow > 0:
            oldest = sorted(self._sessions.values(), key=lambda s: s.last_seen)[:overflow]
            for s in oldest:
                del self._sessions[s.id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)


class RateLimiter:
    """Sliding one-minute window per client."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, what: str = "checks started") -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise HTTPException(429, f"Too many {what} from here in the last minute. "
                                         "Wait a moment and try again.")
            hits.append(now)
            if len(self._hits) > 10_000:   # forget idle clients rather than grow forever
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    del self._hits[k]


sessions = SessionStore()
start_limiter = RateLimiter(STARTS_PER_MINUTE)
answer_limiter = RateLimiter(ANSWERS_PER_MINUTE)
result_limiter = RateLimiter(RESULTS_PER_MINUTE)
feedback_limiter = RateLimiter(FEEDBACK_PER_MINUTE)
read_limiter = RateLimiter(READS_PER_MINUTE)
readings = ReadingStore()

app = FastAPI(title="Claim Rejection Risk Predictor", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(),
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


@app.exception_handler(RequestValidationError)
def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
    """A malformed request, in one sentence. FastAPI's default echoes every submitted value
    back, and crashes with a 500 when one of them is NaN or Infinity."""
    first = exc.errors()[0] if exc.errors() else {}
    field = next((str(p) for p in reversed(first.get("loc", ())) if isinstance(p, str)
                  and p != "body"), None)
    kind = str(first.get("type", ""))
    if field is None or kind == "json_invalid":
        message = "The request could not be read."
    else:
        reason = ("must be a whole number" if kind.startswith("int_")
                  else "is not one of the listed options" if kind == "literal_error"
                  else "is required" if kind == "missing"
                  else "is too long" if kind == "string_too_long"
                  else "must be text" if kind.startswith("string_")
                  else "is not valid")
        name = FEATURE_LABELS.get(field) or field.replace("_", " ").capitalize()
        message = f"{name} {reason}."
    return JSONResponse(status_code=422, content={"detail": message})

# Built on first use rather than at import, so a missing model directory leaves the app
# up and reporting "degraded" on /api/health instead of failing to start at all.
_engine: AdaptiveEngine | None = None
_engine_lock = threading.Lock()


def get_engine() -> AdaptiveEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            try:
                _engine = AdaptiveEngine(get_predictor())
            except Exception as exc:  # noqa: BLE001 — any load failure means "degraded"
                logger.error("Models failed to load: %s", type(exc).__name__)
                raise HTTPException(503, "The risk model is not available on this server.")
        return _engine


def client_key(request: Request) -> str:
    """Who to rate-limit. Behind our own proxy, the *rightmost* X-Forwarded-For entry is the
    one that proxy wrote; anything to its left came from the client and is not evidence of
    anything. Taking the leftmost let a caller pick a fresh identity per request."""
    if trust_proxy_headers():
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",")]
        hops = [h for h in hops if h]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"


# --- views -------------------------------------------------------------------

def _players(session: Session) -> list[str]:
    return [*BILL_FEATURES, *(f for f in session.order if session.answers.get(f) is not None)]


def _read_value(session: Session, f: str) -> ReadValue | None:
    if f not in session.from_documents or session.reading is None:
        return None
    return session.reading.context.get(f) or session.reading.facts.get(f)


def _fields(session: Session, attribution: Attribution) -> tuple[list[FieldOut], list[FieldOut]]:
    def out(f: str, source: str) -> FieldOut:
        v = session.answers[f]
        read = _read_value(session, f)
        return FieldOut(feature=f, label=feature_label(f), value=v,
                        value_label=value_label(f, v),
                        contribution_points=round(float(attribution.points.get(f, 0.0)), 1),
                        source="document" if read else source,
                        evidence=read.evidence if read else None)
    claim = [out(f, "bill") for f in BILL_FEATURES]
    answered = [out(f, "answer") for f in session.order if session.answers.get(f) is not None]
    return claim, answered


def _documents(session: Session) -> list[DocumentOut]:
    if session.reading is None:
        return []
    return [DocumentOut(index=d.index, type=d.type, label=d.label)
            for d in session.reading.documents]


def _patient(answers: dict) -> PatientOut:
    age, gender = int(answers["patient_age"]), answers["patient_gender"]
    return PatientOut(age=age, gender=gender,
                      label=f"{age}-year-old {value_label('patient_gender', gender).lower()}")


def _question_out(q: Question | None, reading: Reading | None = None) -> QuestionOut | None:
    if q is None:
        return None
    hint = reading.hints.get(q.feature) if reading else None
    return QuestionOut(
        feature=q.feature, text=q.text, help_text=q.help_text, kind=q.kind,
        options=[OptionOut(value=o.value, label=o.label) for o in q.options],
        unit=q.unit, min_value=q.min_value, max_value=q.max_value,
        expected_info_gain=round(q.expected_info_gain, 6),
        evidence=hint.evidence if hint else None,
        suggested=hint.value if hint else None)


def _checks(engine: AdaptiveEngine, answers: dict) -> list[CheckOut]:
    derived = engine.p.derived(answers)
    return [CheckOut(id=c.id, label=c.label, status=c.status, detail=c.detail)
            for c in build_checks(answers, derived, engine.p.ref)]


def _bands(engine: AdaptiveEngine) -> BandsOut:
    return BandsOut(**engine.p.bands)


def _step_out(session: Session, step: Step, engine: AdaptiveEngine) -> StepOut:
    session.last_step = step
    attribution = engine.p.attribute(session.answers, _players(session))
    claim, answered = _fields(session, attribution)
    return StepOut(
        session_id=session.id, done=step.done, probability=round(step.probability, 4),
        risk_band=step.risk_band, bands=_bands(engine),
        base_rate=engine.p.meta["base_rejection_rate"],
        typical_probability=round(attribution.base_probability, 4),
        n_answered=step.n_answered, min_questions=MIN_QUESTIONS, max_questions=MAX_QUESTIONS,
        question=_question_out(step.question, session.reading), stop_reason=step.stop_reason,
        patient=_patient(session.answers), claim=claim, answered=answered,
        checks=_checks(engine, session.answers), last_change=session.last_change,
        document_questions=[_question_out(engine.question_for(f, session.answers))
                            for f in session.order if f in session.from_documents],
        documents=_documents(session))


def _carc_out(code: str, confidence: float) -> CarcOut:
    meta = CARC_CODES.get(code, {})
    return CarcOut(code=code, confidence=round(confidence, 4),
                   official_description=meta.get("official_description", ""),
                   plain_english=meta.get("plain_english", ""),
                   staff_action=meta.get("staff_action", ""))


def _reasons(engine: AdaptiveEngine, answers: dict, derived: dict) -> list[CarcOut]:
    """Reason codes for the defects actually present in the claim, most likely first.

    The reason model ranks them; it never adds one. Naming "documents missing" for a claim
    whose documents were never asked about would be a guess presented as a finding.
    """
    present = defect_codes({**answers, **derived})
    if not present:
        return []
    ranked = dict(engine.p.predict_carc(answers, top_n=len(engine.p.carc_classes)))
    present.sort(key=lambda c: -ranked.get(c, 0.0))
    return [_carc_out(c, ranked.get(c, 0.0)) for c in present]


def _start(answers: dict, engine: AdaptiveEngine, reading: Reading | None = None,
           facts: list[str] = ()) -> StepOut:
    session = sessions.create(answers)
    with session.lock:
        if reading is not None:
            session.reading = reading
            # The bill fields count as read only if the clerk left them as read.
            session.from_documents = {f for f, r in reading.context.items()
                                      if answers.get(f) == r.value}
            for f in facts:
                session.answers[f] = reading.facts[f].value
                session.order.append(f)
                session.from_documents.add(f)
        return _step_out(session, engine.next_step(session.answers), engine)


def _reading_out(reading: Reading) -> ReadingOut:
    def out(r: ReadValue) -> ReadValueOut:
        return ReadValueOut(feature=r.feature, label=feature_label(r.feature), value=r.value,
                            value_label=r.value_label, evidence=r.evidence, document=r.document)
    return ReadingOut(
        reading_id=reading.id, context=[out(r) for r in reading.context.values()],
        facts=[out(r) for r in reading.facts.values()],
        hints=[out(r) for r in reading.hints.values()],
        documents=[DocumentOut(index=d.index, type=d.type, label=d.label)
                   for d in reading.documents],
        notes=reading.notes, model=reading.model)


# --- endpoints -----------------------------------------------------------------

@app.post("/api/session/start", response_model=StepOut)
def start_session(req: StartRequest, request: Request) -> StepOut:
    start_limiter.check(client_key(request))
    engine = get_engine()
    try:
        answers = {f: validate_answer(f, getattr(req, f), engine.p) for f in CONTEXT_FEATURES}
    except InvalidAnswer as exc:
        raise HTTPException(422, str(exc))
    reading = None
    if req.reading_id is not None:
        reading = readings.get(req.reading_id)
        if reading is None:
            raise HTTPException(422, "The documents were read too long ago. Read them again, "
                                     "or start without them.")
    elif req.use_facts:
        raise HTTPException(422, "Facts from documents need the reading they came from.")
    facts = list(dict.fromkeys(req.use_facts))
    if reading is not None and any(f not in reading.facts for f in facts):
        raise HTTPException(422, "Only facts read from these documents can be kept.")
    # In question order, so the ledger lists them the way the questions would come.
    facts.sort(key=ASKABLE_FEATURES.index)
    return _start(answers, engine, reading, facts)


@app.post("/api/documents/read", response_model=ReadingOut)
def read_claim_documents(req: DocumentsRequest, request: Request) -> ReadingOut:
    """Read the bill and supporting documents with Qwen-VL. Returns what was read for the
    clerk to confirm; nothing reaches a check until they start one from it."""
    read_limiter.check(client_key(request), "documents read")
    engine = get_engine()
    files = []
    for f in req.files:
        data = f.data.split(",", 1)[1] if f.data.startswith("data:") else f.data
        try:
            files.append((f.name or "A document", b64decode(data, validate=True)))
        except (binascii.Error, ValueError):
            raise HTTPException(422, f"{f.name or 'A document'} could not be read as a file.")
    try:
        reading = read_documents(files, engine.p)
    except DocumentError as exc:
        raise HTTPException(422, str(exc))
    except ReaderUnavailable as exc:
        raise HTTPException(503, str(exc))
    readings.put(reading)
    return _reading_out(reading)


@app.post("/api/demo/{which}", response_model=StepOut)
def start_demo(which: str, request: Request) -> StepOut:
    """Start a session on a scripted claim, then walk the same question loop.

    Only the patient and the bill are entered up front; every other answer is held back
    and fed in as the loop asks for it, so the demo shows the real adaptive behaviour.
    """
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, "There is no such demo claim.")
    start_limiter.check(client_key(request))
    engine = get_engine()
    return _start({f: DEMO_CLAIMS[which][f] for f in CONTEXT_FEATURES}, engine)


@app.get("/api/demo/{which}/answers")
def demo_answers(which: str) -> dict:
    """The scripted answers, so the UI can suggest each one as it is asked."""
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, "There is no such demo claim.")
    return DEMO_CLAIMS[which]


_previews: dict[str, PreviewOut] = {}


@app.get("/api/demo/{which}/preview", response_model=PreviewOut)
def demo_preview(which: str) -> PreviewOut:
    """What the scripted claim does, end to end, without starting a session. Computed
    once per process: the models and the script never change while it runs."""
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, "There is no such demo claim.")
    if which in _previews:
        return _previews[which]
    engine = get_engine()
    claim = DEMO_CLAIMS[which]
    answers = {f: claim[f] for f in CONTEXT_FEATURES}
    bill = (f"{value_label('procedure_category', claim['procedure_category'])} · "
            f"{value_label('claim_amount_pkr', claim['claim_amount_pkr'])}")
    path = [PreviewPoint(label="Bill entered", value_label=bill,
                         probability=round(engine.p.predict(answers), 4))]
    order: list[str] = []
    step = engine.next_step(answers)
    while not step.done:
        f = step.question.feature
        answers[f] = claim[f]
        order.append(f)
        step = engine.next_step(answers)
        path.append(PreviewPoint(label=feature_label(f), value_label=value_label(f, claim[f]),
                                 probability=round(step.probability, 4)))
    points = engine.p.attribute(answers, [*BILL_FEATURES, *order]).points
    top = max(points, key=lambda f: points[f]) if points else None
    driver = (DriverOut(feature=top, label=feature_label(top),
                        value_label=value_label(top, answers[top]),
                        effect="increases_risk" if points[top] > 0 else "reduces_risk",
                        contribution_points=round(points[top], 1))
              if top and abs(points[top]) >= MIN_CONTRIBUTION_POINTS else None)
    reasons = _reasons(engine, answers, engine.p.derived(answers))
    carc = reasons[0] if reasons and step.risk_band != "low" else None
    _previews[which] = PreviewOut(
        claim_label=bill, patient_label=_patient(answers).label, path=path,
        risk_band=step.risk_band, probability=round(step.probability, 4),
        top_driver=driver, carc=carc)
    return _previews[which]


@app.post("/api/session/{session_id}/answer", response_model=StepOut)
def submit_answer(session_id: str, req: AnswerRequest, request: Request) -> StepOut:
    answer_limiter.check(client_key(request), "answers")
    engine = get_engine()
    session = sessions.get(session_id)
    with session.lock:
        if req.feature not in ASKABLE_FEATURES:
            raise HTTPException(400, "That is not a question in this check.")
        step = session.last_step
        current = step.question.feature if step and step.question else None
        editing = req.feature in session.order
        if req.feature != current and not editing:
            raise HTTPException(409, "That question has not been asked in this check yet.")
        try:
            value = validate_answer(req.feature, req.value, engine.p)
        except InvalidAnswer as exc:
            raise HTTPException(422, str(exc))

        if editing and session.answers.get(req.feature) == value and step is not None:
            # Re-sending an unchanged answer changes nothing, so it must not invalidate
            # the cached result either (that would let a client force fresh Qwen calls).
            return _step_out(session, step, engine)

        before = step.probability if step else engine.p.predict(session.answers)
        session.answers[req.feature] = value
        session.from_documents.discard(req.feature)   # the clerk's answer now, not the paper's
        if not editing:
            session.order.append(req.feature)
        next_step = engine.next_step(session.answers)
        session.last_change = ChangeOut(
            feature=req.feature, label=feature_label(req.feature),
            value_label=value_label(req.feature, value),
            delta_points=round((next_step.probability - before) * 100, 1))
        session.result = None
        return _step_out(session, next_step, engine)


@app.get("/api/session/{session_id}/result", response_model=ResultOut)
def get_result(session_id: str, request: Request) -> ResultOut:
    result_limiter.check(client_key(request), "results")
    engine = get_engine()
    session = sessions.get(session_id)
    with session.lock:
        step = session.last_step
        if step is None or not step.done:
            raise HTTPException(409, "This check is not finished yet. Answer the remaining "
                                     "questions first.")
        key = session.state_key()
        if session.result and session.result[0] == key:
            return session.result[1]

        predictor = engine.p
        answers = session.answers
        attribution = predictor.attribute(answers, _players(session))
        claim, answered = _fields(session, attribution)
        ranked = sorted(claim + answered, key=lambda f: -abs(f.contribution_points))
        drivers = [DriverOut(feature=f.feature, label=f.label, value_label=f.value_label,
                             effect="increases_risk" if f.contribution_points > 0 else "reduces_risk",
                             contribution_points=f.contribution_points)
                   for f in ranked if abs(f.contribution_points) >= MIN_CONTRIBUTION_POINTS]
        total = sum(attribution.points.values())
        other = round(total - sum(d.contribution_points for d in drivers), 1)

        # Reasons come only from defects the clerk's answers actually show, ranked by the
        # reason model. For a claim that is not low risk the first is "the most likely
        # reason"; for a low-risk claim that still has one, it is something to fix first,
        # so the tool never calls a claim ready to submit while it knows of a problem.
        derived = predictor.derived(answers)
        reasons = _reasons(engine, answers, derived)
        carc = reasons[0] if reasons and step.risk_band != "low" else None
        alternatives = reasons[1:] if carc else []
        fix_first = reasons[0] if reasons and step.risk_band == "low" else None

        checks = _checks(engine, answers)
        lead = carc or fix_first
        prediction = ClaimPrediction(
            rejection_probability=step.probability, risk_band=step.risk_band,
            carc_code=lead.code if lead else None,
            carc_reason=lead.plain_english if lead else None,
            staff_action=lead.staff_action if lead else None,
            flagged_fields=[FlaggedField(label=d.label, value=d.value_label, effect=d.effect,
                                         contribution_points=d.contribution_points)
                            for d in drivers[:4]],
            issues=[c.detail for c in checks if c.status == "issue"])
        explanation = explain(prediction)

        result = ResultOut(
            session_id=session.id, probability=round(step.probability, 4),
            probability_percent=prediction.percent, risk_band=step.risk_band,
            bands=_bands(engine), base_rate=predictor.meta["base_rejection_rate"],
            typical_probability=round(attribution.base_probability, 4),
            n_answered=step.n_answered, stop_reason=step.stop_reason,
            drivers=drivers, other_points=other, carc=carc, carc_alternatives=alternatives,
            fix_first=fix_first,
            explanation=ExplanationOut(text=explanation.text, source=explanation.source,
                                       model=explanation.model),
            patient=_patient(answers), claim=claim, answered=answered, checks=checks,
            documents=_documents(session))
        session.result = (key, result)
        return result


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest, request: Request) -> FeedbackResponse:
    feedback_limiter.check(client_key(request), "corrections")
    session = sessions.get(req.session_id)
    with session.lock:
        step = session.last_step
        if step is None or not step.done:
            raise HTTPException(409, "There is no verdict to correct yet.")
        key = session.state_key()
        if session.feedback and session.feedback[0] == key:
            # One correction per verdict; clicking again does not add rows.
            return FeedbackResponse(ok=True, logged_id=session.feedback[1])
        carc = (session.result[1].carc.code
                if session.result and session.result[1].carc else None)
        logged_id = feedback_store.log_feedback(
            session_id=session.id, predicted_prob=step.probability,
            predicted_carc=carc, n_answered=step.n_answered,
            answers=session.answers, note=req.note)
        session.feedback = (key, logged_id)
    return FeedbackResponse(ok=True, logged_id=logged_id)


@app.get("/api/meta", response_model=MetaOut)
def meta() -> MetaOut:
    engine = get_engine()
    m, ref = engine.p.meta, engine.p.ref
    labels = VALUE_LABELS["procedure_category"]
    procedures = sorted(
        (ProcedureOption(value=c, label=labels.get(c, c),
                         typical_amount_pkr=int(ref["procedure_median_amount_pkr"].get(c, 0)),
                         needs_pre_auth=c in ref["pre_auth_categories"])
         for c in engine.p.categories["procedure_category"]),
        key=lambda o: (o.value == "other_procedure", o.label))
    return MetaOut(
        procedures=procedures,
        pre_auth_amount_threshold_pkr=ref["pre_auth_amount_threshold_pkr"],
        amount_limits=NUMBER_LIMITS["claim_amount_pkr"], age_limits=NUMBER_LIMITS["patient_age"],
        min_questions=MIN_QUESTIONS, max_questions=MAX_QUESTIONS, bands=_bands(engine),
        base_rate=m["base_rejection_rate"], n_training_claims=m["n_claims"],
        test_auc=m["metrics"]["test_auc_full"], verdicts=m["product_test"])


@app.get("/api/health", response_model=HealthOut,
         responses={503: {"model": HealthOut, "description": "Models failed to load"}})
def health() -> HealthOut | JSONResponse:
    """Health for the container and the masthead. Discloses nothing about the account:
    whether Qwen is configured, not where or as whom. Answers 503 when the models are not
    loaded, so the container's healthcheck (and compose's service_healthy) fails rather
    than reporting a service that cannot answer a single claim."""
    try:
        get_engine()
        loaded = True
    except HTTPException:
        loaded = False
    body = HealthOut(status="ok" if loaded else "degraded", models_loaded=loaded,
                     qwen_configured=load_dashscope_config().is_configured, version=VERSION)
    return body if loaded else JSONResponse(status_code=503, content=body.model_dump())
