"""Request and response models for the API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt

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
    # What the uploaded documents say about this question, when they say anything. A
    # suggestion only: the clerk still answers it.
    evidence: str | None = None
    suggested: Any = None


class FieldOut(BaseModel):
    """One known fact about the claim, with what it is doing to the estimate."""

    feature: str
    label: str
    value: Any
    value_label: str
    # Signed percentage points (exact Shapley). Together with typical_probability these
    # add up to the current estimate.
    contribution_points: float
    source: Literal["bill", "answer", "document"]
    evidence: str | None = None     # the quote it was read from, for a "document" field


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


class DocumentOut(BaseModel):
    index: int
    type: str
    label: str


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
    # How to change an answer that came from the documents: no question was asked for it.
    document_questions: list[QuestionOut] = []
    documents: list[DocumentOut] = []


class StartRequest(BaseModel):
    # Strict: JSON true is not an age, and 44.5 is not a whole number of rupees.
    patient_age: StrictInt
    patient_gender: Literal["M", "F"]
    procedure_category: str
    claim_amount_pkr: StrictInt
    # Starting from read documents: which of the reading's facts the clerk kept. The
    # values themselves come from the server's copy of the reading, never the request.
    reading_id: str | None = Field(None, max_length=64)
    use_facts: list[str] = Field(default_factory=list, max_length=12)


class DocumentFileIn(BaseModel):
    name: str = Field(max_length=120)
    data: str = Field(max_length=5_800_000)    # base64; 4 MB of file at most


class DocumentsRequest(BaseModel):
    files: list[DocumentFileIn] = Field(min_length=1, max_length=3)


class ReadValueOut(BaseModel):
    feature: str
    label: str
    value: Any
    value_label: str
    evidence: str
    document: int | None = None


class ReadingOut(BaseModel):
    reading_id: str
    context: list[ReadValueOut]     # the bill: pre-fills the intake form
    facts: list[ReadValueOut]       # printed facts the clerk can keep as answers
    hints: list[ReadValueOut]       # suggestions for questions that are still asked
    documents: list[DocumentOut]
    notes: list[str]
    model: str | None = None


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
    # The most likely rejection reason, chosen only among defects actually present in the
    # claim, and only for a claim that is not low risk.
    carc: CarcOut | None
    carc_alternatives: list[CarcOut] = []
    # A low-risk claim that still carries a known defect: what to fix before submitting.
    fix_first: CarcOut | None = None
    explanation: ExplanationOut
    patient: PatientOut
    claim: list[FieldOut]
    answered: list[FieldOut]
    checks: list[CheckOut]
    documents: list[DocumentOut] = []


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


class PreviewPoint(BaseModel):
    label: str
    value_label: str
    probability: float


class PreviewOut(BaseModel):
    """A scripted claim's whole path, for the landing page's live specimen."""

    claim_label: str
    patient_label: str
    path: list[PreviewPoint]        # the bill first, then each answer in the order asked
    risk_band: RiskBand
    probability: float
    top_driver: DriverOut | None
    carc: CarcOut | None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    models_loaded: bool
    qwen_configured: bool
    version: str
