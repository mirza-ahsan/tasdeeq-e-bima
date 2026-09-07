"""FastAPI application — Phase 5.

Session state lives in memory for the life of the process. There is no auth, no user
accounts and no persistence beyond the session, which is deliberate: this is a demo
prototype. The one thing that does persist is the feedback log, because demonstrating
the biller-in-the-loop idea is part of the point.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend import feedback_store
from backend.config import load_dashscope_config
from backend.demo_claims import DEMO_CLAIMS
from backend.predictor import get_predictor
from backend.schemas import (AnsweredField, AnswerRequest, CarcOut, ExplanationOut,
                             FeedbackRequest, FeedbackResponse, FlaggedFieldOut, HealthOut,
                             OptionOut, QuestionOut, ResultOut, StartRequest, StepOut)
from backend.services.adaptive import (MAX_QUESTIONS, MIN_QUESTIONS, AdaptiveEngine, Step,
                                       feature_label, value_label)
from backend.services.explain_qwen import ClaimPrediction, FlaggedField, explain
from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES

ROOT = Path(__file__).resolve().parent.parent
CARC_CODES = {c["code"]: c for c in
              json.loads((ROOT / "data" / "carc_codes.json").read_text())["codes"]}

MAX_SESSIONS = 500  # simple bound; oldest are evicted first

# Presentation thresholds: below these, a value is noise and showing it misleads.
MIN_CONTRIBUTION_POINTS = 1.0     # percentage points
MIN_ALTERNATIVE_CONFIDENCE = 0.05


@dataclass
class Session:
    id: str
    answers: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_step: Step | None = None


SESSIONS: dict[str, Session] = {}

app = FastAPI(title="Claim Rejection Risk Predictor", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"], allow_headers=["*"],
)

engine = AdaptiveEngine()


# --- helpers ---------------------------------------------------------------

def _get_session(session_id: str) -> Session:
    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(404, "Session not found. Start a new one.")
    return session


def _answered_list(answers: dict) -> list[AnsweredField]:
    return [AnsweredField(feature=f, label=feature_label(f), value=answers[f],
                          value_label=value_label(f, answers[f]))
            for f in ASKABLE_FEATURES if answers.get(f) is not None]


def _question_out(step: Step) -> QuestionOut | None:
    q = step.question
    if q is None:
        return None
    return QuestionOut(
        feature=q.feature, text=q.text, help_text=q.help_text, kind=q.kind,
        options=[OptionOut(value=o.value, label=o.label) for o in q.options],
        unit=q.unit, expected_info_gain=round(q.expected_info_gain, 6))


def _step_out(session: Session, step: Step) -> StepOut:
    session.last_step = step
    return StepOut(
        session_id=session.id, done=step.done, probability=round(step.probability, 4),
        risk_band=step.risk_band, n_answered=step.n_answered,
        min_questions=MIN_QUESTIONS, max_questions=MAX_QUESTIONS,
        question=_question_out(step), stop_reason=step.stop_reason,
        answered=_answered_list(session.answers))


def _carc_out(code: str, confidence: float) -> CarcOut:
    meta = CARC_CODES.get(code, {})
    return CarcOut(code=code, confidence=round(confidence, 4),
                   official_description=meta.get("official_description", ""),
                   plain_english=meta.get("plain_english", ""),
                   staff_action=meta.get("staff_action", ""))


def _advance(session: Session) -> StepOut:
    return _step_out(session, engine.next_step(session.answers))


# --- endpoints -------------------------------------------------------------

@app.post("/api/session/start", response_model=StepOut)
def start_session(req: StartRequest) -> StepOut:
    if len(SESSIONS) >= MAX_SESSIONS:
        for old in sorted(SESSIONS.values(), key=lambda s: s.created_at)[:50]:
            SESSIONS.pop(old.id, None)

    session = Session(id=str(uuid.uuid4()))
    session.answers = {"patient_age": req.patient_age, "patient_gender": req.patient_gender}
    SESSIONS[session.id] = session
    return _advance(session)


@app.post("/api/session/{session_id}/answer", response_model=StepOut)
def submit_answer(session_id: str, req: AnswerRequest) -> StepOut:
    session = _get_session(session_id)
    if req.feature not in ASKABLE_FEATURES:
        raise HTTPException(400, f"'{req.feature}' is not an answerable field.")
    session.answers[req.feature] = req.value
    return _advance(session)


@app.get("/api/session/{session_id}/result", response_model=ResultOut)
def get_result(session_id: str) -> ResultOut:
    session = _get_session(session_id)
    predictor = get_predictor()
    answers = session.answers

    probability = predictor.predict(answers)
    band = predictor.risk_band(probability)
    contributions = predictor.explain(answers, top_n=4)
    carc_ranked = predictor.predict_carc(answers, top_n=3)

    # Drop contributions too small to act on. A clerk reading "patient age, -0.8 points"
    # learns nothing and cannot change it; it only dilutes the fields that matter.
    flagged = [
        FlaggedFieldOut(feature=c.feature, label=feature_label(c.feature),
                        value_label=value_label(c.feature, c.value), effect=c.effect,
                        contribution_points=round(c.contribution_points, 1))
        for c in contributions if abs(c.contribution_points) >= MIN_CONTRIBUTION_POINTS
    ]

    # The CARC head is trained on rejected claims only, so its output is only meaningful
    # when the claim actually looks like it will be rejected. Presenting a "most likely
    # rejection reason" for a clean claim would be misleading.
    top_carc = _carc_out(*carc_ranked[0]) if carc_ranked and band != "low" else None
    # Only surface alternatives with real support. Listing "CARC 16 (0%)" reads as a bug.
    alternatives = ([_carc_out(c, p) for c, p in carc_ranked[1:]
                     if p >= MIN_ALTERNATIVE_CONFIDENCE] if top_carc else [])

    # Qwen sees only what the models computed — nothing else is in scope for it.
    prediction = ClaimPrediction(
        rejection_probability=probability,
        risk_band=band,
        carc_code=top_carc.code if top_carc else "none",
        carc_description=(top_carc.official_description if top_carc
                          else "No specific rejection reason indicated."),
        flagged_fields=[
            FlaggedField(label=f.label, value=f.value_label, effect=f.effect,
                         contribution_points=f.contribution_points)
            for f in flagged
        ],
    )
    result = explain(prediction)

    n_answered = sum(1 for f in ASKABLE_FEATURES if answers.get(f) is not None)
    return ResultOut(
        session_id=session.id, probability=round(probability, 4),
        probability_percent=round(probability * 100), risk_band=band,
        n_answered=n_answered,
        stop_reason=session.last_step.stop_reason if session.last_step else None,
        flagged_fields=flagged, carc=top_carc, carc_alternatives=alternatives,
        explanation=ExplanationOut(text=result.text, source=result.source, model=result.model),
        answered=_answered_list(answers))


@app.post("/api/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest) -> FeedbackResponse:
    session = _get_session(req.session_id)
    predictor = get_predictor()
    probability = predictor.predict(session.answers)
    carc = predictor.predict_carc(session.answers, top_n=1)
    n_answered = sum(1 for f in ASKABLE_FEATURES if session.answers.get(f) is not None)

    logged_id = feedback_store.log_feedback(
        session_id=req.session_id, verdict=req.verdict, predicted_prob=probability,
        predicted_carc=carc[0][0] if carc else None, n_answered=n_answered,
        answers=session.answers, note=req.note)
    return FeedbackResponse(ok=True, logged_id=logged_id)


@app.get("/api/demo/{which}", response_model=StepOut)
def start_demo(which: str) -> StepOut:
    """Start a session pre-loaded with a scripted claim, then walk the same question loop.

    The claim's answers are held aside and fed in as the loop asks for them, so the demo
    shows the real adaptive behaviour rather than skipping to the answer.
    """
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, f"Unknown demo claim '{which}'. Try: {list(DEMO_CLAIMS)}")
    claim = DEMO_CLAIMS[which]
    session = Session(id=str(uuid.uuid4()))
    session.answers = {f: claim[f] for f in CONTEXT_FEATURES if f in claim}
    SESSIONS[session.id] = session
    return _advance(session)


@app.get("/api/demo/{which}/answers")
def demo_answers(which: str) -> dict:
    """The scripted answers, so the UI can auto-fill each question as it is asked."""
    if which not in DEMO_CLAIMS:
        raise HTTPException(404, f"Unknown demo claim '{which}'.")
    return DEMO_CLAIMS[which]


@app.get("/api/health", response_model=HealthOut)
def health() -> HealthOut:
    predictor = get_predictor()
    config = load_dashscope_config()
    return HealthOut(
        status="ok", models_loaded=True,
        n_training_claims=predictor.meta["n_claims"],
        test_auc=predictor.meta["metrics"]["test_auc_full"],
        qwen=config.status(),   # reports key presence only, never its value
        active_sessions=len(SESSIONS))
