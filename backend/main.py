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

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from backend import feedback_store
from backend.config import cors_origins, load_dashscope_config, trust_proxy_headers
from backend.demo_claims import DEMO_CLAIMS
from backend.predictor import Attribution, get_predictor
from backend.schemas import (AnswerRequest, BandsOut, CarcOut, ChangeOut, CheckOut,
                             DriverOut, ExplanationOut, FeedbackRequest, FeedbackResponse,
                             FieldOut, HealthOut, MetaOut, OptionOut, PatientOut,
                             ProcedureOption, QuestionOut, ResultOut, StartRequest, StepOut)
from backend.services.adaptive import (MAX_QUESTIONS, MIN_QUESTIONS, NUMBER_LIMITS,
                                       VALUE_LABELS, AdaptiveEngine, InvalidAnswer, Step,
                                       feature_label, validate_answer, value_label)
from backend.services.checks import build_checks
from backend.services.explain_qwen import ClaimPrediction, FlaggedField, explain
from ml.features import ASKABLE_FEATURES, BILL_FEATURES, CONTEXT_FEATURES

logger = logging.getLogger(__name__)

VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parent.parent
CARC_CODES = {c["code"]: c for c in
              json.loads((ROOT / "data" / "carc_codes.json").read_text())["codes"]}

MAX_SESSIONS = 500
SESSION_TTL_S = 30 * 60
STARTS_PER_MINUTE = 30          # per client

# Presentation thresholds: below these, a value is noise and showing it misleads.
MIN_CONTRIBUTION_POINTS = 1.0     # percentage points
MIN_ALTERNATIVE_CONFIDENCE = 0.05


# --- sessions ----------------------------------------------------------------

@dataclass
class Session:
    id: str
    answers: dict
    order: list[str] = field(default_factory=list)      # questions, in the order asked
    last_step: Step | None = None
    last_change: ChangeOut | None = None
    result: tuple[str, ResultOut] | None = None         # (claim state, cached result)
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

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > 60:
                hits.popleft()
            if len(hits) >= self.per_minute:
                raise HTTPException(429, "Too many checks started from here in the last "
                                         "minute. Wait a moment and try again.")
            hits.append(now)
            if len(self._hits) > 10_000:   # forget idle clients rather than grow forever
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > 60]:
                    del self._hits[k]


sessions = SessionStore()
start_limiter = RateLimiter(STARTS_PER_MINUTE)

app = FastAPI(title="Claim Rejection Risk Predictor", version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=cors_origins(),
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

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
    if trust_proxy_headers():
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# --- views -------------------------------------------------------------------

def _players(session: Session) -> list[str]:
    return [*BILL_FEATURES, *(f for f in session.order if session.answers.get(f) is not None)]


def _fields(session: Session, attribution: Attribution) -> tuple[list[FieldOut], list[FieldOut]]:
    def out(f: str, source: str) -> FieldOut:
        v = session.answers[f]
        return FieldOut(feature=f, label=feature_label(f), value=v,
                        value_label=value_label(f, v),
                        contribution_points=round(float(attribution.points.get(f, 0.0)), 1),
                        source=source)
    claim = [out(f, "bill") for f in BILL_FEATURES]
    answered = [out(f, "answer") for f in session.order if session.answers.get(f) is not None]
    return claim, answered


def _patient(answers: dict) -> PatientOut:
    age, gender = int(answers["patient_age"]), answers["patient_gender"]
    return PatientOut(age=age, gender=gender,
                      label=f"{age}-year-old {value_label('patient_gender', gender).lower()}")


def _question_out(step: Step) -> QuestionOut | None:
    q = step.question
    if q is None:
        return None
    return QuestionOut(
        feature=q.feature, text=q.text, help_text=q.help_text, kind=q.kind,
        options=[OptionOut(value=o.value, label=o.label) for o in q.options],
        unit=q.unit, min_value=q.min_value, max_value=q.max_value,
        expected_info_gain=round(q.expected_info_gain, 6))


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
        question=_question_out(step), stop_reason=step.stop_reason,
        patient=_patient(session.answers), claim=claim, answered=answered,
        checks=_checks(engine, session.answers), last_change=session.last_change)


def _carc_out(code: str, confidence: float) -> CarcOut:
    meta = CARC_CODES.get(code, {})
    return CarcOut(code=code, confidence=round(confidence, 4),
                   official_description=meta.get("official_description", ""),
                   plain_english=meta.get("plain_english", ""),
                   staff_action=meta.get("staff_action", ""))


def _start(answers: dict, engine: AdaptiveEngine) -> StepOut:
    session = sessions.create(answers)
    with session.lock:
        return _step_out(session, engine.next_step(session.answers), engine)


# --- endpoints -----------------------------------------------------------------

@app.post("/api/session/start", response_model=StepOut)
def start_session(req: StartRequest, request: Request) -> StepOut:
    start_limiter.check(client_key(request))
    engine = get_engine()
    try:
        answers = {f: validate_answer(f, getattr(req, f), engine.p) for f in CONTEXT_FEATURES}
    except InvalidAnswer as exc:
        raise HTTPException(422, str(exc))
    return _start(answers, engine)


@app.post("/api/demo/{which}", response_model=StepOut)
def start_demo(which: str, request: Request) -> StepOut:
    """Start a session on a scripted claim, then walk the same question loop.

    Only the patient and the bill are entered up front; every other answer is held back
    and fed in as the loop asks for it, so the demo shows the real adaptive behaviour.
    """
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, f"Unknown demo claim '{which}'. Try: {sorted(DEMO_CLAIMS)}")
    start_limiter.check(client_key(request))
    engine = get_engine()
    return _start({f: DEMO_CLAIMS[which][f] for f in CONTEXT_FEATURES}, engine)


@app.get("/api/demo/{which}/answers")
def demo_answers(which: str) -> dict:
    """The scripted answers, so the UI can suggest each one as it is asked."""
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, f"Unknown demo claim '{which}'.")
    return DEMO_CLAIMS[which]


@app.post("/api/session/{session_id}/answer", response_model=StepOut)
def submit_answer(session_id: str, req: AnswerRequest) -> StepOut:
    engine = get_engine()
    session = sessions.get(session_id)
    with session.lock:
        if req.feature not in ASKABLE_FEATURES:
            raise HTTPException(400, f"'{req.feature}' is not a question in this check.")
        step = session.last_step
        current = step.question.feature if step and step.question else None
        editing = req.feature in session.order
        if req.feature != current and not editing:
            raise HTTPException(409, "That question has not been asked in this check yet.")
        try:
            value = validate_answer(req.feature, req.value, engine.p)
        except InvalidAnswer as exc:
            raise HTTPException(422, str(exc))

        before = step.probability if step else engine.p.predict(session.answers)
        session.answers[req.feature] = value
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
def get_result(session_id: str) -> ResultOut:
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

        # The reason model is trained on rejected claims only, so its output is only
        # meaningful when the claim actually looks as if it will be rejected. Naming a
        # "most likely rejection reason" for a claim that looks clean would be inventing one.
        carc, alternatives = None, []
        if step.risk_band != "low":
            ranked_codes = predictor.predict_carc(answers, top_n=3)
            carc = _carc_out(*ranked_codes[0])
            # Only surface alternatives with real support. "CARC 16 (0%)" reads as a bug.
            alternatives = [_carc_out(c, p) for c, p in ranked_codes[1:]
                            if p >= MIN_ALTERNATIVE_CONFIDENCE]

        checks = _checks(engine, answers)
        prediction = ClaimPrediction(
            rejection_probability=step.probability, risk_band=step.risk_band,
            carc_code=carc.code if carc else None,
            carc_reason=carc.plain_english if carc else None,
            staff_action=carc.staff_action if carc else None,
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
            explanation=ExplanationOut(text=explanation.text, source=explanation.source,
                                       model=explanation.model),
            patient=_patient(answers), claim=claim, answered=answered, checks=checks)
        session.result = (key, result)
        return result


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    session = sessions.get(req.session_id)
    with session.lock:
        step = session.last_step
        if step is None or not step.done:
            raise HTTPException(409, "There is no verdict to correct yet.")
        carc = (session.result[1].carc.code
                if session.result and session.result[1].carc else None)
        logged_id = feedback_store.log_feedback(
            session_id=session.id, predicted_prob=step.probability,
            predicted_carc=carc, n_answered=step.n_answered,
            answers=session.answers, note=req.note)
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


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    """Liveness for the container and the masthead. Never fails, and discloses nothing
    about the account: whether Qwen is configured, not where or as whom."""
    try:
        get_engine()
        loaded = True
    except HTTPException:
        loaded = False
    return HealthOut(status="ok" if loaded else "degraded", models_loaded=loaded,
                     qwen_configured=load_dashscope_config().is_configured, version=VERSION)
