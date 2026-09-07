"""Request and response models for the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class OptionOut(BaseModel):
    value: Any
    label: str


class QuestionOut(BaseModel):
    feature: str
    text: str
    help_text: str
    kind: Literal["choice", "number"]
    options: list[OptionOut] = []
    unit: str | None = None
    expected_info_gain: float


class AnsweredField(BaseModel):
    feature: str
    label: str
    value: Any
    value_label: str


class StepOut(BaseModel):
    session_id: str
    done: bool
    probability: float
    risk_band: Literal["low", "medium", "high"]
    n_answered: int
    min_questions: int
    max_questions: int
    question: QuestionOut | None = None
    stop_reason: str | None = None
    answered: list[AnsweredField] = []


class StartRequest(BaseModel):
    patient_age: int = Field(44, ge=0, le=120)
    patient_gender: Literal["M", "F"] = "F"


class AnswerRequest(BaseModel):
    feature: str
    value: Any


class FlaggedFieldOut(BaseModel):
    feature: str
    label: str
    value_label: str
    effect: Literal["increases_risk", "reduces_risk"]
    contribution_points: float


class CarcOut(BaseModel):
    code: str
    confidence: float
    official_description: str
    plain_english: str
    staff_action: str


class ExplanationOut(BaseModel):
    text: str
    source: Literal["qwen", "fallback"]
    model: str | None = None


class ResultOut(BaseModel):
    session_id: str
    probability: float
    probability_percent: int
    risk_band: Literal["low", "medium", "high"]
    n_answered: int
    stop_reason: str | None
    flagged_fields: list[FlaggedFieldOut]
    carc: CarcOut | None
    carc_alternatives: list[CarcOut] = []
    explanation: ExplanationOut
    answered: list[AnsweredField]


class FeedbackRequest(BaseModel):
    session_id: str
    verdict: Literal["wrong", "right"] = "wrong"
    note: str | None = Field(None, max_length=1000)


class FeedbackResponse(BaseModel):
    ok: bool
    logged_id: int


class HealthOut(BaseModel):
    status: str
    models_loaded: bool
    n_training_claims: int
    test_auc: float
    qwen: dict[str, str]
    active_sessions: int
