"""The adaptive question engine.

After every answer we ask whichever unanswered field would most reduce our uncertainty
about this claim. The quantity is the mutual information between the rejection outcome
and that field, given everything answered so far:

    IG(f) = H( SUM_v P(f=v) * p_v )  -  SUM_v P(f=v) * H(p_v)

where p_v is the probability this claim would carry if the answer to f were v.

Note the baseline is the *marginalised* probability over f's candidate values, not the
model's prediction with f left unknown. Those two are not the same: LightGBM sends a
missing value down a learned default branch rather than averaging over what the value
might have been. Using the model's unknown-prediction as the baseline produces negative
"information gains" — it did, on the first run, with the strongest predictor in the whole
model scoring the most negative. Marginalising per feature makes the quantity a true
mutual information: non-negative by concavity of entropy, and comparable across fields.

This is genuinely information-theoretic rather than a fixed question order ranked by
global feature importance: because every p_v is conditioned on the answers already
given, the ranking changes as the conversation proceeds, and two different claims get
asked different questions in a different order.

The procedure and the amount come from the bill, entered before the first question (see
ml/features.py), so from the first question on the ranking is specific to this claim.
They also settle whether pre-authorisation was required. Where it was not, the answer is
implied and never asked. Where it was, it is always the first question, with only
"obtained" and "not obtained" on offer: offering "not required" for a procedure that
requires it let the costliest real mistake score as safely as having approval, and no
claim that needed approval can be cleared without the tool having asked about it.

Known approximation: P(f = v) uses training frequencies rather than a posterior
conditioned on every answer so far; pre-authorisation uses its frequency among claims
that required it. Modelling the full joint distribution would be a far larger build.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from backend.predictor import Predictor, get_predictor
from ml.features import ASKABLE_FEATURES, BOOLEAN_FEATURES, CATEGORICAL_FEATURES, TIER_EXCLUSIONS

MIN_QUESTIONS = 3      # never present a verdict before this many answers
MAX_QUESTIONS = 8      # hard cap, guarantees termination
# Below this expected gain (bits) a further question is not earning its keep. The value
# actually used is fitted with the bands on validation and read from metadata.json; this
# is the fallback for artefacts that predate that.
MIN_INFO_GAIN = 0.006

VALUE_LABELS: dict[str, dict] = {
    "procedure_category": {
        "consultation_exam": "Consultation or examination",
        "preventive_screening": "Preventive screening or assessment",
        "dental": "Dental",
        "vaccination": "Vaccination or injection",
        "obstetric_maternity": "Obstetric / maternity",
        "dialysis": "Dialysis",
        "mental_health": "Mental health assessment",
        "laboratory": "Laboratory test",
        "physiotherapy": "Physiotherapy or rehabilitation",
        "minor_surgery": "Minor surgery or endoscopy",
        "oncology_therapy": "Oncology therapy",
        "imaging": "Imaging (X-ray, ultrasound, scan)",
        "nursing_hospice": "Nursing or hospice care",
        "other_procedure": "Other",
    },
    "diagnosis_category": {
        "general_unspecified": "General / not specified",
        "pregnancy_maternity": "Pregnancy or maternity",
        "dental_oral": "Dental or oral",
        "renal": "Kidney / renal",
        "respiratory_infection": "Respiratory infection",
        "allergy": "Allergy",
        "cardiometabolic": "Cardiac or metabolic",
        "oncology": "Cancer",
        "injury_trauma": "Injury or trauma",
        "genitourinary": "Urinary",
        "mental_health": "Mental health",
        "neurological": "Neurological",
        "infectious": "Infectious disease",
    },
    "plan_tier": {"basic": "Basic", "standard": "Standard",
                  "premium": "Premium", "corporate": "Corporate"},
    "pre_auth_obtained": {"yes": "Yes, obtained", "no": "No, not obtained",
                          "not_required": "Not required for this procedure"},
    "documents_complete": {"complete": "All complete", "partial": "Some missing",
                           "missing": "Mostly missing"},
    "provider_network_status": {"in_network": "Yes, on the panel",
                                "out_of_network": "No, off panel"},
}

QUESTION_TEXT: dict[str, tuple[str, str]] = {
    "diagnosis_category":        ("What was the main diagnosis?",
                                  "The condition recorded on the case notes."),
    "insurer_tpa":               ("Which insurer or TPA is this claim going to?",
                                  "Filing deadlines and tariffs differ by insurer."),
    "plan_tier":                 ("What plan tier is the patient on?",
                                  "Lower tiers exclude more procedures."),
    "pre_auth_obtained":         ("This claim needs pre-authorisation. Was it obtained?",
                                  "Missing pre-authorisation is the single most common avoidable rejection."),
    "documents_complete":        ("Are the supporting documents complete?",
                                  "Prescriptions, reports, discharge notes and the itemised bill."),
    "days_since_treatment":      ("How many days ago was the treatment?",
                                  "Insurers reject claims filed after their deadline."),
    "provider_network_status":   ("Is your clinic on this insurer's panel?",
                                  "Off-panel claims often have to go to a different payer."),
    "patient_policy_active":     ("Was the patient's policy active on the treatment date?",
                                  "Cover that had lapsed is an automatic rejection."),
    "is_emergency":              ("Was this an emergency or urgent care visit?",
                                  "Insurers give emergency care more leeway on pre-authorisation."),
    "is_duplicate_submission":   ("Has a claim for this same treatment already been submitted?",
                                  "Duplicates are rejected outright."),
    "prior_claims_30d":          ("How many other claims has this patient filed in the last 30 days?",
                                  "Unusual frequency triggers a review."),
}

BOOLEAN_LABELS: dict[str, tuple[str, str]] = {
    "patient_policy_active":     ("Yes, active", "No, lapsed"),
    "is_emergency":              ("Yes", "No"),
    "is_duplicate_submission":   ("Yes, already submitted", "No, first submission"),
}

NUMBER_UNITS = {"days_since_treatment": "days", "claim_amount_pkr": "PKR",
                "prior_claims_30d": "claims"}

# Accepted ranges for typed answers (inclusive). Anything outside is refused rather than
# fed to the model, which would treat a nonsense value as if it were real.
NUMBER_LIMITS: dict[str, tuple[int, int]] = {
    "days_since_treatment": (0, 365),
    "claim_amount_pkr": (100, 5_000_000),
    "prior_claims_30d": (0, 30),
    "patient_age": (0, 110),
}
NUMBER_UNITS["patient_age"] = "years"


@dataclass
class Option:
    value: object
    label: str


@dataclass
class Question:
    feature: str
    text: str
    help_text: str
    kind: Literal["choice", "number"]
    options: list[Option] = field(default_factory=list)
    unit: str | None = None
    min_value: int | None = None
    max_value: int | None = None
    expected_info_gain: float = 0.0
    # "Not sure" is offered for everything except pre-authorisation, which a claim that
    # needs it can never be cleared without.
    can_skip: bool = True


@dataclass
class Step:
    done: bool
    probability: float
    risk_band: str
    n_answered: int
    question: Question | None = None
    stop_reason: str | None = None


@dataclass
class TrajectoryStep:
    """One point on a claim's path through the loop, ignoring band-based stopping."""

    n_answered: int
    probability: float
    best_feature: str | None
    best_gain: float | None


def _entropy(p: np.ndarray | float) -> np.ndarray | float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


def stop_reason(n_answered: int, band: str, best_gain: float | None,
                min_gain: float = MIN_INFO_GAIN) -> str | None:
    """Why the loop stops here, or None to keep asking.

    The live loop and the band-tuning simulation both call this, so the bands chosen in
    training are chosen for exactly the stopping behaviour users get.
    """
    if n_answered >= MAX_QUESTIONS:
        return "question_limit_reached"
    if n_answered >= MIN_QUESTIONS and band != "medium":
        return f"confident_{band}_risk"
    if best_gain is None:
        return "all_questions_answered"
    if n_answered >= MIN_QUESTIONS and best_gain < min_gain:
        return "no_informative_questions_left"
    return None


def n_answered(answers: dict) -> int:
    return sum(1 for f in ASKABLE_FEATURES if answers.get(f) is not None)


class AdaptiveEngine:
    def __init__(self, predictor: Predictor | None = None):
        self.p = predictor or get_predictor()
        self.priors = self.p.meta["value_priors"]
        self.min_gain = self.p.meta.get("loop", {}).get("min_info_gain", MIN_INFO_GAIN)

    # -- what can be asked ----------------------------------------------------

    def candidates(self, answers: dict, derived: dict | None = None,
                   skipped: tuple | list = ()) -> list[str]:
        """Unanswered fields that can be asked now.

        Pre-authorisation waits until the tool knows it was required. Until then its
        answer has no fixed meaning; once it is known not to be required, the answer
        is implied and asking would only invite a contradiction. A field the clerk was
        not sure about is not asked again.
        """
        derived = derived if derived is not None else self.p.derived(answers)
        out = []
        for f in ASKABLE_FEATURES:
            if answers.get(f) is not None or f in skipped:
                continue
            if f == "pre_auth_obtained" and derived.get("pre_auth_required_for_procedure") != 1:
                continue
            out.append(f)
        return out

    def candidate_values(self, feature: str) -> tuple[list, np.ndarray]:
        prior = self.priors[feature]
        w = np.asarray(prior["probs"], dtype=float)
        return list(prior["values"]), w / w.sum()

    # -- question construction ---------------------------------------------

    def _build_question(self, feature: str, gain: float, answers: dict) -> Question:
        text, help_text = question_text(feature, answers, self.p.ref)
        kind = self.priors[feature]["kind"]
        can_skip = feature != "pre_auth_obtained"

        if kind == "numeric":
            lo, hi = NUMBER_LIMITS[feature]
            return Question(feature, text, help_text, "number",
                            unit=NUMBER_UNITS.get(feature), min_value=lo, max_value=hi,
                            expected_info_gain=gain, can_skip=can_skip)

        if kind == "boolean":
            yes, no = BOOLEAN_LABELS[feature]
            return Question(feature, text, help_text, "choice",
                            options=[Option(True, yes), Option(False, no)],
                            expected_info_gain=gain, can_skip=can_skip)

        labels = VALUE_LABELS.get(feature, {})
        values, _ = self.candidate_values(feature)
        opts = [Option(v, labels.get(v, v)) for v in values]
        return Question(feature, text, help_text, "choice", options=opts,
                        expected_info_gain=gain, can_skip=can_skip)

    def question_for(self, feature: str, answers: dict) -> Question:
        """The question as it would be asked now, for editing an answer nobody was asked
        for (one read from the documents)."""
        return self._build_question(feature, 0.0, answers)

    # -- information gain ---------------------------------------------------

    def information_gains(self, answers: dict, derived: dict | None = None,
                          skipped: tuple | list = ()) -> dict[str, float]:
        """Expected entropy reduction for every field that can be asked now."""
        fields = self.candidates(answers, derived, skipped)
        if not fields:
            return {}

        # One batched prediction across every (field, candidate value) pair.
        trials: list[dict] = []
        spans: list[tuple[str, int, int, np.ndarray]] = []
        for feat in fields:
            values, weights = self.candidate_values(feat)
            start = len(trials)
            trials.extend({**answers, feat: v} for v in values)
            spans.append((feat, start, len(trials), weights))

        probs = self.p.predict_many(trials)
        entropies = _entropy(probs)

        gains: dict[str, float] = {}
        for feat, start, end, w in spans:
            p_marginal = float(np.dot(w, probs[start:end]))
            expected_h = float(np.dot(w, entropies[start:end]))
            gains[feat] = float(_entropy(p_marginal)) - expected_h
        return gains

    # -- the loop -----------------------------------------------------------

    def next_step(self, answers: dict, skipped: tuple | list = ()) -> Step:
        """The next question, or the verdict. `skipped` are questions the clerk was not
        sure about: they stay unknown to the model, are not asked again, and count
        towards the question cap (so the loop still ends) but not towards the minimum
        number of answers a verdict needs."""
        answers = {k: v for k, v in answers.items() if v is not None}
        n = n_answered(answers)
        p = self.p.predict(answers)
        band = self.p.risk_band(p)
        # Answers read from documents can pass the minimum before anything is asked, so
        # an open pre-authorisation question overrides every stopping rule: a claim that
        # needed approval is never cleared without the clerk having answered it. In the
        # question-only loop it is always asked first, so this never changes that loop.
        must_ask = "pre_auth_obtained" in self.candidates(answers)

        # Stopping on the cap or on confidence needs no information gains, so they are
        # only computed when a question might actually be asked.
        early = stop_reason(n, band, best_gain=0.0, min_gain=self.min_gain)
        if n + len(skipped) >= MAX_QUESTIONS:
            early = "question_limit_reached"
        if early in ("question_limit_reached", f"confident_{band}_risk") and not must_ask:
            return Step(True, p, band, n, None, early)

        gains = self.information_gains(answers, skipped=skipped)
        best = self.choose(gains)
        reason = stop_reason(n, band, gains[best] if best else None, self.min_gain)
        if reason and not must_ask:
            return Step(True, p, band, n, None, reason)
        return Step(False, p, band, n, self._build_question(best, gains[best], answers))

    @staticmethod
    def choose(gains: dict[str, float]) -> str | None:
        """The next question: pre-authorisation first whenever it is open, since a claim
        that needed approval must never be cleared without it; otherwise the field with
        the highest expected information gain."""
        if not gains:
            return None
        if "pre_auth_obtained" in gains:
            return "pre_auth_obtained"
        return max(gains, key=gains.get)

    def trajectory(self, truth: dict, context: dict) -> tuple[list[TrajectoryStep], list[str]]:
        """Walk a claim through the loop answering from `truth`, never stopping on the
        bands, so one walk can be scored under any band setting (see ml/simulate.py)."""
        answers = dict(context)
        steps: list[TrajectoryStep] = []
        order: list[str] = []
        while True:
            n = n_answered(answers)
            p = self.p.predict(answers)
            gains = self.information_gains(answers) if n < MAX_QUESTIONS else {}
            best = self.choose(gains)
            steps.append(TrajectoryStep(n, p, best, gains[best] if best else None))
            if best is None:
                return steps, order
            answers[best] = truth[best]
            order.append(best)


def question_text(feature: str, answers: dict, ref: dict) -> tuple[str, str]:
    """The question and its help line, worded for what is already known about the claim."""
    text, help_text = QUESTION_TEXT[feature]
    insurer = answers.get("insurer_tpa")
    if feature == "pre_auth_obtained":
        help_text = pre_auth_reason(answers, ref)
    elif feature == "provider_network_status":
        text = (f"Is your clinic on {insurer}'s panel?" if insurer
                else "Is your clinic on the panel of the insurer this claim goes to?")
    elif feature == "days_since_treatment" and insurer:
        limit = ref["insurer_filing_limit_days"].get(insurer)
        if limit:
            help_text = f"{insurer} rejects claims filed more than {limit} days after treatment."
    elif feature == "plan_tier":
        proc = answers.get("procedure_category")
        if any(proc in excluded for excluded in TIER_EXCLUSIONS.values()):
            help_text = f"Some plans do not cover {value_label('procedure_category', proc).lower()} at all."
    return text, help_text


def pre_auth_reason(answers: dict, ref: dict) -> str:
    """Why this claim needs pre-authorisation, in the clerk's terms."""
    proc = answers.get("procedure_category")
    amount = answers.get("claim_amount_pkr")
    if proc in ref["pre_auth_categories"]:
        why = f"{value_label('procedure_category', proc)} needs insurer approval before treatment, whatever it costs."
    elif amount is not None and amount > ref["pre_auth_amount_threshold_pkr"]:
        why = (f"Claims above PKR {ref['pre_auth_amount_threshold_pkr']:,} need insurer "
               "approval before treatment.")
    else:
        why = "This claim needs insurer approval before treatment."
    if answers.get("is_emergency") is True:
        why += " Emergencies get some leeway, but most insurers still expect it."
    return why


# ---------------------------------------------------------------------------
# Answer validation — every value is checked against what the model was trained on
# before it reaches the session, so nothing unknown is silently scored as "unknown".
# ---------------------------------------------------------------------------

class InvalidAnswer(ValueError):
    pass


def validate_answer(feature: str, value: object, predictor: Predictor) -> object:
    """Return the value in canonical form, or raise InvalidAnswer with a message a clerk
    can act on. Covers the intake's patient and bill fields as well as the questions."""
    label = feature_label(feature)

    if feature in BOOLEAN_FEATURES:
        if not isinstance(value, bool):
            raise InvalidAnswer(f"{label} must be answered yes or no.")
        return value

    if feature in NUMBER_LIMITS:
        # Integers only, as at intake: 4.0 is refused here just as 44.0 is there.
        if isinstance(value, bool) or not isinstance(value, int):
            raise InvalidAnswer(f"{label} must be a whole number.")
        lo, hi = NUMBER_LIMITS[feature]
        if not lo <= value <= hi:
            unit = NUMBER_UNITS.get(feature)
            raise InvalidAnswer(f"{label} must be between {lo:,} and {hi:,}"
                                f"{f' {unit}' if unit else ''}.")
        return value

    if feature not in CATEGORICAL_FEATURES:
        raise InvalidAnswer(f"{label} cannot be answered here.")
    allowed = (["yes", "no"] if feature == "pre_auth_obtained"
               else predictor.categories[feature])
    if not isinstance(value, str) or value not in allowed:
        raise InvalidAnswer(f"{label}: that is not one of the listed options.")
    return value


# ---------------------------------------------------------------------------
# Display helpers — turning model field names into something a clinic clerk reads
# ---------------------------------------------------------------------------

FEATURE_LABELS: dict[str, str] = {
    "procedure_category": "Procedure type",
    "diagnosis_category": "Diagnosis",
    "insurer_tpa": "Insurer / TPA",
    "plan_tier": "Plan tier",
    "pre_auth_obtained": "Pre-authorisation",
    "documents_complete": "Supporting documents",
    "days_since_treatment": "Days since treatment",
    "claim_amount_pkr": "Claim amount",
    "provider_network_status": "Panel status",
    "patient_policy_active": "Policy status",
    "is_emergency": "Emergency visit",
    "is_duplicate_submission": "Duplicate submission",
    "prior_claims_30d": "Claims in last 30 days",
    "patient_age": "Patient age",
    "patient_gender": "Patient gender",
    "pre_auth_required_for_procedure": "Pre-authorisation required",
    "amount_vs_procedure_median": "Amount vs typical for procedure",
    "days_vs_insurer_filing_limit": "Share of filing window used",
    "insurer_filing_limit_days": "Insurer filing limit",
    "diagnosis_procedure_match": "Diagnosis fits procedure",
    "procedure_patient_conflict": "Procedure fits patient",
}


def feature_label(feature: str) -> str:
    return FEATURE_LABELS.get(feature, feature.replace("_", " ").capitalize())


def value_label(feature: str, value: object) -> str:
    """Human-readable rendering of one answer. Only ever sees validated values."""
    if value is None:
        return "Not answered"
    if feature in VALUE_LABELS:
        return VALUE_LABELS[feature].get(value, str(value))
    if feature in BOOLEAN_LABELS:
        return BOOLEAN_LABELS[feature][0 if value else 1]
    if feature == "patient_gender":
        return {"M": "Male", "F": "Female"}.get(value, str(value))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if feature == "claim_amount_pkr":
        return f"PKR {int(value):,}"
    if feature == "days_since_treatment":
        n = int(value)
        return f"{n} day{'s' if n != 1 else ''}"
    if feature == "prior_claims_30d":
        n = int(value)
        return "No other claims" if n == 0 else f"{n} claim{'s' if n > 1 else ''}"
    if feature == "patient_age":
        return f"{int(value)} years"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)

