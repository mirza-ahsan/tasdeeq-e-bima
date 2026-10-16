"""Shared feature contract between training, the adaptive engine and the API.

One definition, imported everywhere, so the question flow can never drift out of
sync with what the model was trained on. That covers not just which fields exist but
how the derived ones are computed: `derive_frame` is the only implementation, and the
labeller, the training-time masking and the live API all call it.

Three kinds of field:

* **context** — the patient and the bill, entered once before the check starts: age,
  gender, what was done and what it cost. Every one of these is printed on the claim, so
  asking for them one at a time would only make the clerk wait.
* **askable** — the paperwork and coverage facts staff are asked, one at a time, in the
  order the adaptive engine chooses.
* **derived** — facts the tool works out itself from the other two. These are where it
  knows something the clerk may not: whether this procedure needs pre-authorisation,
  how much of this insurer's filing window has gone, whether the diagnosis justifies
  the procedure, whether the procedure fits the patient. Nothing derived is ever asked.

Procedure and amount used to be questions. Information gain is myopic: the procedure's
real value is that it decides whether pre-authorisation matters, which a one-step
look-ahead cannot see, so the engine would clear a minor surgery without pre-approval
as low risk without ever having asked what the procedure was.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Fields the clinic staff member is asked about, one at a time.
ASKABLE_FEATURES = [
    "diagnosis_category",
    "insurer_tpa",
    "plan_tier",
    "pre_auth_obtained",
    "documents_complete",
    "days_since_treatment",
    "provider_network_status",
    "patient_policy_active",
    "is_emergency",
    "is_duplicate_submission",
    "prior_claims_30d",
]

# The patient and the bill. Entered once at the start, never asked.
PATIENT_FEATURES = ["patient_age", "patient_gender"]
BILL_FEATURES = ["procedure_category", "claim_amount_pkr"]
CONTEXT_FEATURES = PATIENT_FEATURES + BILL_FEATURES

# Computed from other fields. Each is unknown until it can be decided from what is known.
DERIVED_DEPENDENCIES = {
    "pre_auth_required_for_procedure": ["procedure_category", "claim_amount_pkr"],
    "amount_vs_procedure_median":      ["claim_amount_pkr", "procedure_category"],
    "days_vs_insurer_filing_limit":    ["days_since_treatment", "insurer_tpa"],
    "insurer_filing_limit_days":       ["insurer_tpa"],
    "diagnosis_procedure_match":       ["procedure_category", "diagnosis_category"],
    "procedure_patient_conflict":      ["procedure_category", "patient_age", "patient_gender"],
}
DERIVED_FEATURES = list(DERIVED_DEPENDENCIES)

FEATURES = ASKABLE_FEATURES + CONTEXT_FEATURES + DERIVED_FEATURES

CATEGORICAL_FEATURES = [
    "procedure_category",
    "diagnosis_category",
    "insurer_tpa",
    "plan_tier",
    "pre_auth_obtained",
    "documents_complete",
    "provider_network_status",
    "patient_gender",
]

BOOLEAN_FEATURES = [
    "patient_policy_active",
    "is_emergency",
    "is_duplicate_submission",
    "pre_auth_required_for_procedure",
    "diagnosis_procedure_match",
    "procedure_patient_conflict",
]

NUMERIC_FEATURES = [f for f in FEATURES if f not in CATEGORICAL_FEATURES + BOOLEAN_FEATURES]

TARGET = "is_rejected"
CARC_TARGET = "carc_code"

# ---------------------------------------------------------------------------
# Business rules the derived features encode. Invented for the demo, but shaped like
# real Pakistani insurer policy; the labeller and the API both read them from here.
# ---------------------------------------------------------------------------

# Procedures that need pre-authorisation whatever they cost.
PRE_AUTH_CATEGORIES = ("minor_surgery", "oncology_therapy", "dialysis", "nursing_hospice")
# Any claim above this needs pre-authorisation, whatever the procedure.
PRE_AUTH_AMOUNT_THRESHOLD_PKR = 20_000

# Procedure categories any diagnosis can justify.
GENERIC_PROCEDURES = ("consultation_exam", "preventive_screening", "laboratory",
                      "vaccination", "imaging", "other_procedure")
# Which diagnosis categories justify which specific procedures. "general_unspecified"
# justifies anything: a vague diagnosis is weak, not contradictory.
COMPATIBLE_DIAGNOSES = {
    "dental":              ("dental_oral",),
    "obstetric_maternity": ("pregnancy_maternity",),
    "dialysis":            ("renal",),
    "oncology_therapy":    ("oncology",),
    "mental_health":       ("mental_health",),
    "physiotherapy":       ("injury_trauma", "neurological", "cardiometabolic"),
    "minor_surgery":       ("injury_trauma", "dental_oral", "oncology", "genitourinary",
                            "cardiometabolic"),
    "nursing_hospice":     ("oncology", "neurological"),
}

# Obstetric care billed for a patient it cannot apply to (CARC 6).
OBSTETRIC_AGE_RANGE = (12, 55)

# What each plan tier does not cover at all (CARC 96).
TIER_EXCLUSIONS = {
    "basic":     ("physiotherapy", "oncology_therapy", "nursing_hospice"),
    "standard":  ("nursing_hospice",),
    "premium":   (),
    "corporate": (),
}
# Elective or discretionary work a basic plan often refuses as not medically necessary
# (CARC 50), unless it was an emergency.
ELECTIVE_CATEGORIES = ("dental", "preventive_screening", "imaging", "physiotherapy")

# A bill this many times the procedure's typical amount gets queried (CARC 45).
AMOUNT_EXCESSIVE_RATIO = 2.5
# This many other claims in 30 days triggers a frequency review (CARC 151).
HIGH_FREQUENCY_CLAIMS = 4

# The defects that justify each reason code, in adjudication priority order: an insurer
# reports the most fundamental reason when several apply. The labeller fires these rules
# (probabilistically), and the API names a reason only when one of them is actually present
# in what the clerk entered — never because a field nobody asked about *might* be wrong.
REASON_ORDER = ["27", "18", "197", "29", "109", "96", "50", "16", "11", "7", "6", "45", "151"]


def _val(row: dict, key: str):
    v = row.get(key)
    return None if v is None or (isinstance(v, float) and v != v) else v


def defect_codes(row: dict) -> list[str]:
    """Reason codes whose defect is present in a claim, in priority order.

    `row` holds raw and derived values (see derive_frame); unknown fields are None or NaN
    and never count as a defect.
    """
    proc, tier = _val(row, "procedure_category"), _val(row, "plan_tier")
    conflict = _val(row, "procedure_patient_conflict") == 1
    present = {
        "27": _val(row, "patient_policy_active") in (False, 0),
        "18": _val(row, "is_duplicate_submission") in (True, 1),
        "197": _val(row, "pre_auth_obtained") == "no",
        "29": (_val(row, "days_vs_insurer_filing_limit") or 0) > 1.0,
        "109": _val(row, "provider_network_status") == "out_of_network",
        "96": tier is not None and proc in TIER_EXCLUSIONS.get(tier, ()),
        "50": (tier == "basic" and proc in ELECTIVE_CATEGORIES
               and _val(row, "is_emergency") not in (True, 1)),
        "16": _val(row, "documents_complete") in ("partial", "missing"),
        "11": _val(row, "diagnosis_procedure_match") == 0,
        "7": conflict and _val(row, "patient_gender") == "M",
        "6": conflict and _val(row, "patient_gender") != "M",
        "45": (_val(row, "amount_vs_procedure_median") or 0) > AMOUNT_EXCESSIVE_RATIO,
        "151": (_val(row, "prior_claims_30d") or 0) >= HIGH_FREQUENCY_CLAIMS,
    }
    return [code for code in REASON_ORDER if present[code]]


def diagnosis_matches(procedure: str, diagnosis: str) -> bool:
    if procedure in GENERIC_PROCEDURES or diagnosis == "general_unspecified":
        return True
    return diagnosis in COMPATIBLE_DIAGNOSES.get(procedure, ())


def build_reference(df: pd.DataFrame, filing_limits: dict[str, int]) -> dict:
    """Reference values derived features need, computed once from the training data."""
    medians = df.groupby("procedure_category", observed=True).claim_amount_pkr.median()
    return {
        "procedure_median_amount_pkr": {str(k): float(v) for k, v in medians.items()},
        "insurer_filing_limit_days": {str(k): int(v) for k, v in filing_limits.items()},
        "pre_auth_amount_threshold_pkr": PRE_AUTH_AMOUNT_THRESHOLD_PKR,
        "pre_auth_categories": list(PRE_AUTH_CATEGORIES),
    }


# "Not answered yet", as a category of its own. LightGBM gives a missing numeric value a
# learned branch, but a missing categorical simply follows the catch-all side of each
# split, which made "documents: not yet asked" score the same as "documents: complete" —
# so answering "complete" taught the model nothing and a clean claim never looked cleaner.
UNKNOWN = "__unknown__"


def prepare(df: pd.DataFrame, categories: dict[str, list[str]]) -> pd.DataFrame:
    """Model-ready frame: fixed category vocabularies, with "unknown" as a category.

    Shared by training and serving so a value is encoded identically in both.
    """
    X = df.reindex(columns=FEATURES).copy()
    for c in CATEGORICAL_FEATURES:
        X[c] = pd.Categorical(X[c].astype(object).where(X[c].notna(), UNKNOWN),
                              categories=[*categories[c], UNKNOWN])
    for c in BOOLEAN_FEATURES + NUMERIC_FEATURES:
        X[c] = pd.to_numeric(X[c], errors="coerce").astype("float64")
    return X


def _known(s: pd.Series) -> pd.Series:
    return s.notna()


def derive_frame(df: pd.DataFrame, ref: dict) -> pd.DataFrame:
    """Fill every derived feature from the askable and context columns present.

    The single implementation used by the labeller, by training-time masking and by
    the live API, so the model is only ever shown states it can meet in production.

    A derived value is unknown (NaN) until it can be decided. That is usually "until
    every input is known", with one deliberate exception: pre-authorisation is required
    as soon as *either* input settles it, since a listed procedure needs it at any price.

    `pre_auth_obtained` is also reconciled here, because its meaning depends on whether
    pre-authorisation was required at all:
      * known not required -> "not_required", whatever was entered;
      * required, but entered as "not_required" -> "no". Staff believing it was not
        needed is the costliest real-world version of this mistake, not a safe answer.
    """
    out = df.copy()
    for col in ASKABLE_FEATURES + CONTEXT_FEATURES:
        if col not in out:
            out[col] = np.nan

    proc = out["procedure_category"].astype(object)
    dx = out["diagnosis_category"].astype(object)
    amount = pd.to_numeric(out["claim_amount_pkr"], errors="coerce")
    days = pd.to_numeric(out["days_since_treatment"], errors="coerce")
    insurer = out["insurer_tpa"].astype(object)
    age = pd.to_numeric(out["patient_age"], errors="coerce")
    gender = out["patient_gender"].astype(object)

    # --- pre-authorisation required: either input can settle it ---
    listed = proc.isin(ref["pre_auth_categories"])
    over = amount > ref["pre_auth_amount_threshold_pkr"]
    required = pd.Series(np.nan, index=out.index, dtype=object)
    required[_known(proc) & _known(amount)] = False
    required[listed | over] = True
    out["pre_auth_required_for_procedure"] = required.astype("float64")

    pre_auth = out["pre_auth_obtained"].astype(object)
    pre_auth = pre_auth.where(~(required == False), "not_required")       # noqa: E712
    pre_auth = pre_auth.where(~((required == True) & (pre_auth == "not_required")), "no")  # noqa: E712
    out["pre_auth_obtained"] = pre_auth

    # --- amount against what this procedure usually costs ---
    medians = proc.map(ref["procedure_median_amount_pkr"]).astype("float64")
    out["amount_vs_procedure_median"] = (amount / medians).round(3)

    # --- filing window ---
    limit = insurer.map(ref["insurer_filing_limit_days"]).astype("float64")
    out["insurer_filing_limit_days"] = limit
    out["days_vs_insurer_filing_limit"] = (days / limit).round(3)

    # --- does the diagnosis justify the procedure ---
    both = _known(proc) & _known(dx)
    match = pd.Series(np.nan, index=out.index, dtype="float64")
    match[both] = [float(diagnosis_matches(p, d)) for p, d in zip(proc[both], dx[both])]
    out["diagnosis_procedure_match"] = match

    # --- does the procedure fit the patient ---
    lo, hi = OBSTETRIC_AGE_RANGE
    ready = _known(proc) & _known(age) & _known(gender)
    conflict = (proc == "obstetric_maternity") & ((gender == "M") | (age < lo) | (age > hi))
    out["procedure_patient_conflict"] = conflict.astype("float64").where(ready)
    return out
