"""Phase 4 — the adaptive question engine.

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

For each candidate value of each unanswered field we predict the probability the claim
would have if that were the answer, weight it by how often that value occurs, and keep
the field whose answers would move us furthest from maximum uncertainty.

This is genuinely information-theoretic rather than a fixed question order ranked by
global feature importance: because every p_given_f=v is conditioned on the answers
already given, the ranking changes as the conversation proceeds, and two different
claims get asked different questions in a different order.

Cost is roughly 150 predictions per question, batched into a single LightGBM call.

Known approximation: P(f = v) uses the marginal training frequency rather than the
posterior conditioned on answers so far. Modelling the joint distribution over answers
would be a far larger build for a demo, and the marginal is a standard, defensible
stand-in. It is stated here rather than hidden.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from backend.predictor import Predictor, get_predictor
from ml.features import ASKABLE_FEATURES

MIN_QUESTIONS = 2      # never present a verdict before this many answers
MAX_QUESTIONS = 8      # hard cap, guarantees termination
MIN_INFO_GAIN = 0.006  # below this, further questions are not earning their keep

# MIN_INFO_GAIN, and the risk bands in metadata.json, were calibrated together by
# simulating the loop over held-out claims (see tests/test_adaptive.py). At 0.03 the loop
# stops before asking anything; at 0.004 with the original bands it ran to the cap on most
# claims. These values give clean claims a ~3-question path and risky ones ~4.

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
    "procedure_category":        ("What kind of procedure was performed?",
                                  "Pick the closest category to what was billed."),
    "diagnosis_category":        ("What was the main diagnosis?",
                                  "The condition recorded on the case notes."),
    "insurer_tpa":               ("Which insurer or TPA is this claim going to?",
                                  "Filing deadlines and tariffs differ by insurer."),
    "plan_tier":                 ("What plan tier is the patient on?",
                                  "Lower tiers exclude more procedures."),
    "pre_auth_obtained":         ("Was pre-authorisation obtained?",
                                  "Missing pre-authorisation is the single most common avoidable rejection."),
    "documents_complete":        ("Are the supporting documents complete?",
                                  "Prescriptions, reports, discharge notes and the itemised bill."),
    "days_since_treatment":      ("How many days ago was the treatment?",
                                  "Insurers reject claims filed after their deadline."),
    "claim_amount_pkr":          ("What is the total claim amount, in PKR?",
                                  "Amounts well above the usual rate for a procedure get queried."),
    "provider_network_status":   ("Is your clinic on this insurer's panel?",
                                  "Off-panel claims often have to go to a different payer."),
    "patient_policy_active":     ("Was the patient's policy active on the treatment date?",
                                  "Cover that had lapsed is an automatic rejection."),
    "is_emergency":              ("Was this an emergency or urgent care visit?",
                                  "Emergencies are held to different pre-authorisation rules."),
    "diagnosis_procedure_match": ("Does the recorded diagnosis clearly justify the procedure billed?",
                                  "Insurers check that the treatment fits the diagnosis."),
    "is_duplicate_submission":   ("Has a claim for this same treatment already been submitted?",
                                  "Duplicates are rejected outright."),
    "prior_claims_30d":          ("How many other claims has this patient filed in the last 30 days?",
                                  "Unusual frequency triggers a review."),
}

BOOLEAN_LABELS: dict[str, tuple[str, str]] = {
    "patient_policy_active":     ("Yes, active", "No, lapsed"),
    "is_emergency":              ("Yes", "No"),
    "diagnosis_procedure_match": ("Yes, it matches", "No, there's a mismatch"),
    "is_duplicate_submission":   ("Yes, already submitted", "No, first submission"),
}

NUMBER_UNITS = {"days_since_treatment": "days", "claim_amount_pkr": "PKR",
                "prior_claims_30d": "claims"}


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
    expected_info_gain: float = 0.0


@dataclass
class Step:
    done: bool
    probability: float
    risk_band: str
    n_answered: int
    question: Question | None = None
    stop_reason: str | None = None


def _entropy(p: np.ndarray | float) -> np.ndarray | float:
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return -(p * np.log2(p) + (1 - p) * np.log2(1 - p))


class AdaptiveEngine:
    def __init__(self, predictor: Predictor | None = None):
        self.p = predictor or get_predictor()
        self.priors = self.p.meta["value_priors"]

    # -- question construction ---------------------------------------------

    def _build_question(self, feature: str, gain: float) -> Question:
        text, help_text = QUESTION_TEXT[feature]
        prior = self.priors[feature]
        kind = prior["kind"]

        if kind == "numeric":
            return Question(feature, text, help_text, "number",
                            unit=NUMBER_UNITS.get(feature), expected_info_gain=gain)

        if kind == "boolean":
            yes, no = BOOLEAN_LABELS[feature]
            return Question(feature, text, help_text, "choice",
                            options=[Option(True, yes), Option(False, no)],
                            expected_info_gain=gain)

        labels = VALUE_LABELS.get(feature, {})
        opts = [Option(v, labels.get(v, v)) for v in prior["values"]]
        return Question(feature, text, help_text, "choice", options=opts,
                        expected_info_gain=gain)

    # -- information gain ---------------------------------------------------

    def information_gains(self, answers: dict) -> dict[str, float]:
        """Expected entropy reduction for every unanswered askable field."""
        unanswered = [f for f in ASKABLE_FEATURES if answers.get(f) is None]
        if not unanswered:
            return {}

        # One batched prediction across every (field, candidate value) pair.
        trials: list[dict] = []
        spans: list[tuple[str, int, int, list[float]]] = []
        for feat in unanswered:
            prior = self.priors[feat]
            start = len(trials)
            for v in prior["values"]:
                trials.append({**answers, feat: v})
            spans.append((feat, start, len(trials), prior["probs"]))

        probs = self.p.predict_many(trials)
        entropies = _entropy(probs)

        gains: dict[str, float] = {}
        for feat, start, end, weights in spans:
            w = np.asarray(weights, dtype=float)
            w = w / w.sum()
            p_marginal = float(np.dot(w, probs[start:end]))
            expected_h = float(np.dot(w, entropies[start:end]))
            gains[feat] = float(_entropy(p_marginal)) - expected_h
        return gains

    # -- the loop -----------------------------------------------------------

    def next_step(self, answers: dict) -> Step:
        answers = {k: v for k, v in answers.items() if v is not None}
        n_answered = sum(1 for f in ASKABLE_FEATURES if answers.get(f) is not None)
        p = self.p.predict(answers)
        band = self.p.risk_band(p)

        def stop(reason: str) -> Step:
            return Step(True, p, band, n_answered, None, reason)

        if n_answered >= MAX_QUESTIONS:
            return stop("question_limit_reached")

        gains = self.information_gains(answers)
        if not gains:
            return stop("all_questions_answered")

        best_feature = max(gains, key=gains.get)
        best_gain = gains[best_feature]

        # Confident enough to stop early — but only after a minimum number of answers,
        # so we never present a verdict built on almost nothing.
        if n_answered >= MIN_QUESTIONS and band != "medium":
            return stop(f"confident_{band}_risk")

        if best_gain < MIN_INFO_GAIN:
            return stop("no_informative_questions_left")

        return Step(False, p, band, n_answered,
                    self._build_question(best_feature, best_gain))
