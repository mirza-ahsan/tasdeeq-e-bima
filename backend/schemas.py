"""Request and response models for the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

RiskBand = Literal["low", "medium", "high"]
CheckStatus = Literal["ok", "warn", "issue", "pending"]


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
    min_value: int | None = None
    max_value: int | None = None
    expected_info_gain: float


class FieldOut(BaseModel):
    """One known fact about the claim, with what it is doing to the estimate."""

    feature: str
    label: str
    value: Any
    value_label: str
    # Signed percentage points (exact Shapley). Together with typical_probability these
    # add up to the current estimate.
    contribution_points: float
    source: Literal["bill", "answer"]


class PatientOut(BaseModel):
    age: int
    gender: Literal["M", "F"]
    label: str                      # "44-year-old female"


class CheckOut(BaseModel):
    id: str
    label: str
    status: CheckStatus
    detail: str


class BandsOut(BaseModel):
    low_max: float
    high_min: float


class ChangeOut(BaseModel):
    """What the most recent answer did to the estimate."""

    feature: str
    label: str
    value_label: str
    delta_points: float


class StepOut(BaseModel):
    session_id: str
    done: bool
    probability: float
    risk_band: RiskBand
    bands: BandsOut
    base_rate: float
    typical_probability: float      # this patient, before the bill was entered
    n_answered: int
    min_questions: int
    max_questions: int
    question: QuestionOut | None = None
    stop_reason: str | None = None
    patient: PatientOut
    claim: list[FieldOut]           # the bill, entered at the start
    answered: list[FieldOut]        # the questions, in the order they were asked
    checks: list[CheckOut]
    last_change: ChangeOut | None = None


class StartRequest(BaseModel):
    patient_age: int
    patient_gender: Literal["M", "F"]
    procedure_category: str
    claim_amount_pkr: int


class AnswerRequest(BaseModel):
    feature: str
    value: Any   # validated per field against the training vocabulary and ranges


class DriverOut(BaseModel):
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
    risk_band: RiskBand
    bands: BandsOut
    base_rate: float
    typical_probability: float
    n_answered: int
    stop_reason: str | None
    drivers: list[DriverOut]        # contributions of at least one point, largest first
    other_points: float             # the rest, so the waterfall still adds up
    carc: CarcOut | None
    carc_alternatives: list[CarcOut] = []
    explanation: ExplanationOut
    patient: PatientOut
    claim: list[FieldOut]
    answered: list[FieldOut]
    checks: list[CheckOut]


class FeedbackRequest(BaseModel):
    session_id: str
    note: str | None = Field(None, max_length=500)


class FeedbackResponse(BaseModel):
    ok: bool
    logged_id: int


class ProcedureOption(BaseModel):
    value: str
    label: str
    typical_amount_pkr: int
    needs_pre_auth: bool


class MetaOut(BaseModel):
    """What the intake form and the masthead need, from the trained artefacts."""

    procedures: list[ProcedureOption]
    pre_auth_amount_threshold_pkr: int
    amount_limits: tuple[int, int]
    age_limits: tuple[int, int]
    min_questions: int
    max_questions: int
    bands: BandsOut
    base_rate: float
    n_training_claims: int
    test_auc: float
    verdicts: dict[str, Any]        # test-split verdict quality, for the about panel


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    models_loaded: bool
    qwen_configured: bool
    version: str
