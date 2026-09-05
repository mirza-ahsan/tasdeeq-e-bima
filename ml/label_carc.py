"""Phase 2b — synthesise claim-process fields and assign rejection outcomes.

Synthea generates claims but not adjudication outcomes, because real accept/reject
data is exactly what no insurer publishes. This script adds the paperwork fields that
actually drive rejections, then assigns real X12 CARC codes with a probabilistic
rules engine.

Design constraints (see docs/data-provenance.md):
  * every rule fires PROBABILISTICALLY, so the model must learn patterns rather than
    memorise a lookup table
  * a small base rejection rate fires with no clear driver
  * a slice of labels is flipped as noise
  * target overall rejection rate 22-28%

    python ml/label_carc.py

Input : data/processed/claims_base.parquet
Output: data/processed/claims.parquet
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROC = ROOT / "data" / "processed"

SEED = 42
BASE_REJECTION_RATE = 0.025   # rejected with no clear driver
LABEL_NOISE_RATE = 0.08      # fraction of labels flipped in either direction

# Procedures that a Pakistani insurer would typically require pre-authorisation for.
PRE_AUTH_CATEGORIES = {"minor_surgery", "oncology_therapy", "dialysis", "imaging", "nursing_hospice"}
PRE_AUTH_AMOUNT_THRESHOLD_PKR = 50_000

# Elective / discretionary work — the usual "not medically necessary" territory.
ELECTIVE_CATEGORIES = {"dental", "preventive_screening", "imaging", "physiotherapy"}

# What each plan tier simply does not cover.
TIER_EXCLUSIONS = {
    "basic":     {"physiotherapy", "oncology_therapy", "nursing_hospice"},
    "standard":  {"nursing_hospice"},
    "premium":   set(),
    "corporate": set(),
}

# Which diagnosis categories plausibly justify which procedure categories.
# Generic categories are compatible with anything.
GENERIC_PROCEDURES = {"consultation_exam", "preventive_screening", "laboratory",
                      "vaccination", "imaging", "other_procedure"}
COMPATIBLE = {
    "dental":              {"dental_oral"},
    "obstetric_maternity": {"pregnancy_maternity"},
    "dialysis":            {"renal"},
    "oncology_therapy":    {"oncology"},
    "mental_health":       {"mental_health"},
    "physiotherapy":       {"injury_trauma", "neurological", "cardiometabolic"},
    "minor_surgery":       {"injury_trauma", "dental_oral", "oncology", "genitourinary",
                            "cardiometabolic", "general_unspecified"},
    "nursing_hospice":     {"oncology", "neurological", "general_unspecified"},
}

# Rules in adjudication priority order. An insurer reports the most fundamental
# reason when several apply, so the first rule that fires wins.
# (carc_code, condition column, fire probability)
RULES: list[tuple[str, str, float]] = [
    ("27",  "_policy_inactive",       0.85),
    ("18",  "_duplicate",             0.80),
    ("197", "_pre_auth_missing",      0.72),
    ("29",  "_late_filing",           0.65),
    ("109", "_out_of_network",        0.45),
    ("96",  "_tier_excluded",         0.40),
    ("50",  "_elective_basic",        0.26),
    ("16",  "_docs_missing",          0.55),
    ("16",  "_docs_partial",          0.20),
    ("11",  "_dx_proc_mismatch",      0.38),
    ("6",   "_age_inconsistent",      0.55),
    ("45",  "_amount_excessive",      0.26),
    ("151", "_high_frequency",        0.30),
]


def synthesise_process_fields(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)

    # --- pre-authorisation ---
    df["pre_auth_required_for_procedure"] = (
        df.procedure_category.isin(PRE_AUTH_CATEGORIES)
        | (df.claim_amount_pkr > PRE_AUTH_AMOUNT_THRESHOLD_PKR)
    )
    # Emergencies legitimately skip pre-auth more often — staff cannot get it in time.
    p_obtained = np.where(df.is_emergency, 0.60, 0.86)
    got_it = rng.random(n) < p_obtained
    df["pre_auth_obtained"] = np.where(
        ~df.pre_auth_required_for_procedure, "not_required",
        np.where(got_it, "yes", "no"))

    # --- documentation ---
    df["documents_complete"] = rng.choice(
        ["complete", "partial", "missing"], size=n, p=[0.855, 0.10, 0.045])

    # --- filing delay: mostly prompt, with a long tail ---
    df["days_since_treatment"] = np.clip(
        rng.gamma(shape=2.0, scale=7.0, size=n).round(), 0, 150).astype(int)
    df["days_vs_insurer_filing_limit"] = (
        df.days_since_treatment / df.insurer_filing_limit_days).round(3)

    # --- panel status, policy status, history ---
    df["provider_network_status"] = np.where(
        rng.random(n) < 0.06, "out_of_network", "in_network")
    df["patient_policy_active"] = rng.random(n) > 0.015
    df["prior_claims_30d"] = rng.poisson(0.9, size=n)
    df["is_duplicate_submission"] = rng.random(n) < 0.012

    # --- billed amount versus what this procedure normally costs ---
    median_by_cat = df.groupby("procedure_category").claim_amount_pkr.transform("median")
    df["amount_vs_procedure_median"] = (
        df.claim_amount_pkr / median_by_cat.replace(0, np.nan)).round(3).fillna(1.0)

    # --- diagnosis / procedure consistency ---
    # Corrupt a slice by reassigning an incompatible diagnosis, so the mismatch is a
    # real, learnable relationship between two visible fields rather than a coin flip.
    corrupt = rng.random(n) < 0.045
    all_dx = df.diagnosis_category.unique()
    df.loc[corrupt, "diagnosis_category"] = rng.choice(all_dx, size=corrupt.sum())

    def _matches(row) -> bool:
        proc, dx = row.procedure_category, row.diagnosis_category
        if proc in GENERIC_PROCEDURES or dx == "general_unspecified":
            return True
        return dx in COMPATIBLE.get(proc, set())

    df["diagnosis_procedure_match"] = df.apply(_matches, axis=1)

    # --- age / procedure consistency ---
    # Inject by reassigning the procedure, keeping the inconsistency learnable from
    # the age band and gender the model can already see.
    swap = rng.random(n) < 0.015
    df.loc[swap, "procedure_category"] = "obstetric_maternity"
    df["_age_inconsistent"] = (
        (df.procedure_category == "obstetric_maternity")
        & ((df.patient_gender == "M") | (df.patient_age < 12) | (df.patient_age > 55))
    )
    return df


def build_conditions(df: pd.DataFrame) -> pd.DataFrame:
    df["_policy_inactive"] = ~df.patient_policy_active
    df["_duplicate"] = df.is_duplicate_submission
    df["_pre_auth_missing"] = df.pre_auth_obtained == "no"
    df["_late_filing"] = df.days_vs_insurer_filing_limit > 1.0
    df["_out_of_network"] = df.provider_network_status == "out_of_network"
    df["_tier_excluded"] = [
        cat in TIER_EXCLUSIONS[tier]
        for cat, tier in zip(df.procedure_category, df.plan_tier)
    ]
    df["_elective_basic"] = (
        df.procedure_category.isin(ELECTIVE_CATEGORIES)
        & (df.plan_tier == "basic") & ~df.is_emergency
    )
    df["_docs_missing"] = df.documents_complete == "missing"
    df["_docs_partial"] = df.documents_complete == "partial"
    df["_dx_proc_mismatch"] = ~df.diagnosis_procedure_match
    df["_amount_excessive"] = df.amount_vs_procedure_median > 2.5
    df["_high_frequency"] = df.prior_claims_30d >= 4
    return df


def apply_rules(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    carc = np.array([None] * n, dtype=object)
    source = np.array(["approved"] * n, dtype=object)

    for code, cond_col, p_fire in RULES:
        eligible = df[cond_col].to_numpy(dtype=bool) & (carc == None)  # noqa: E711
        fires = eligible & (rng.random(n) < p_fire)
        carc[fires] = code
        source[fires] = "rule"

    # Base rate: rejected with no clear driver, as happens in real adjudication.
    base = (carc == None) & (rng.random(n) < BASE_REJECTION_RATE)  # noqa: E711
    base_codes = [c for c, _, _ in RULES]
    carc[base] = rng.choice(base_codes, size=base.sum())
    source[base] = "base_rate"

    df["is_rejected"] = carc != None  # noqa: E711
    df["carc_code"] = carc
    df["label_source"] = source

    # Label noise, applied last, in both directions.
    flip = rng.random(n) < LABEL_NOISE_RATE
    flip_to_reject = flip & ~df.is_rejected.to_numpy()
    flip_to_accept = flip & df.is_rejected.to_numpy()

    df.loc[flip_to_reject, "carc_code"] = rng.choice(base_codes, size=flip_to_reject.sum())
    df.loc[flip_to_reject, "is_rejected"] = True
    df.loc[flip_to_accept, "carc_code"] = None
    df.loc[flip_to_accept, "is_rejected"] = False
    df.loc[flip, "label_source"] = "noise"
    return df


def main() -> int:
    rng = np.random.default_rng(SEED)
    src = PROC / "claims_base.parquet"
    if not src.exists():
        print(f"ERROR: {src} not found. Run ml/build_dataset.py first.", file=sys.stderr)
        return 1

    df = pd.read_parquet(src)
    print(f"Labelling {len(df)} claims...")

    df = synthesise_process_fields(df, rng)
    df = build_conditions(df)
    df = apply_rules(df, rng)

    carc_meta = json.loads((ROOT / "data" / "carc_codes.json").read_text())
    descriptions = {c["code"]: c["plain_english"] for c in carc_meta["codes"]}

    rate = df.is_rejected.mean()
    print(f"\n  rejection rate: {rate:.1%}   (target 22-28%)")
    if not 0.22 <= rate <= 0.28:
        print("  WARNING: outside the target band — tune rule probabilities.")

    print("\n  label source:")
    print(df.label_source.value_counts().to_string().replace("\n", "\n    "))

    print("\n  CARC distribution among rejected claims:")
    counts = df[df.is_rejected].carc_code.value_counts()
    for code, cnt in counts.items():
        print(f"    {code:>4}  {cnt:4d}  {descriptions.get(code, '?')}")

    df = df.drop(columns=[c for c in df.columns if c.startswith("_")])
    out = PROC / "claims.parquet"
    df.to_parquet(out, index=False)
    print(f"\n  rows: {len(df)}  columns: {len(df.columns)}")
    print(f"  written: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
