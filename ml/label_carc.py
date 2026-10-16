"""Phase 2b — synthesise claim-process fields and assign rejection outcomes.

Synthea generates claims but not adjudication outcomes, because real accept/reject
data is exactly what no insurer publishes. This script adds the paperwork fields that
actually drive rejections, then assigns real X12 CARC codes with a probabilistic
rules engine.

Design constraints (see docs/data-provenance.md):
  * every rule fires PROBABILISTICALLY, so the model must learn patterns rather than
    memorise a lookup table
  * a small base rejection rate fires with no clear driver
  * a slice of outcomes is flipped as noise
  * target overall rejection rate 22-28%

Noise touches the accept/reject outcome only. A claim flipped to "rejected", or rejected
at the base rate, is given the reason code of the most fundamental defect it actually
has; if it has none, it carries no code and the reason model never trains on it. The
earlier version drew those codes uniformly at random, which made almost a third of the
reason model's targets pure noise.

Derived facts (pre-authorisation requirement, diagnosis/procedure fit, procedure/patient
fit, filing window, amount against typical) come from `ml.features.derive_frame`, the
same function the API calls, and are computed only after every deliberate corruption has
been applied, so the labels are a function of exactly what the model and the API see.

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
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.features import (AMOUNT_EXCESSIVE_RATIO, ASKABLE_FEATURES,  # noqa: E402
                         CONTEXT_FEATURES, ELECTIVE_CATEGORIES, NUMERIC_FEATURES,
                         TIER_EXCLUSIONS, build_reference, derive_frame)

PROC = ROOT / "data" / "processed"

SEED = 42
# Rejected with no clear driver, and outcomes flipped at random in either direction.
# Both stop the model memorising the rule table. They were 2.5% and 8%; at that level a
# claim with no defect at all was still rejected about one time in ten, so no amount of
# questioning could certify a clean claim, and the "looks ready to submit" verdict was
# only reachable by placing a band boundary between two calibration plateaus. At 2% and
# 5% a fully-answered claim still tops out near 0.8 AUC, which is the point of the noise.
BASE_REJECTION_RATE = 0.02
LABEL_NOISE_RATE = 0.05

# Share of claims needing pre-authorisation where staff actually obtained it. Emergencies
# are lower: there is often no time to ask the insurer first.
P_PRE_AUTH_OBTAINED = 0.80
P_PRE_AUTH_OBTAINED_EMERGENCY = 0.55

# Paperwork burden grows with what was done. A consultation needs a prescription; a
# procedure needs reports, a discharge note and an itemised bill, and is far more often
# submitted with something missing. Recurring treatment is where duplicates happen.
COMPLEXITY = {
    "routine":  {"consultation_exam", "laboratory", "vaccination", "preventive_screening",
                 "mental_health"},
    "standard": {"dental", "obstetric_maternity", "imaging", "physiotherapy", "other_procedure"},
    "complex":  {"minor_surgery", "oncology_therapy", "dialysis", "nursing_hospice"},
}
DOCUMENTS_P = {            # complete, partial, missing
    "routine":  [0.93, 0.05, 0.02],
    "standard": [0.82, 0.12, 0.06],
    "complex":  [0.64, 0.24, 0.12],
}
RECURRING = {"dialysis", "physiotherapy"}
P_DUPLICATE = {"recurring": 0.035, "other": 0.008}

# Rules in adjudication priority order. An insurer reports the most fundamental
# reason when several apply, so the first rule that fires wins.
# (carc_code, condition column, fire probability)
RULES: list[tuple[str, str, float]] = [
    ("27",  "_policy_inactive",            0.85),
    ("18",  "_duplicate",                  0.80),
    ("197", "_pre_auth_missing",           0.72),
    # Insurers give emergencies leeway on prior approval, but not a blank cheque.
    ("197", "_pre_auth_missing_emergency", 0.30),
    ("29",  "_late_filing",                0.65),
    ("109", "_out_of_network",             0.45),
    ("96",  "_tier_excluded",              0.40),
    ("50",  "_elective_basic",             0.26),
    ("16",  "_docs_missing",               0.55),
    ("16",  "_docs_partial",               0.20),
    ("11",  "_dx_proc_mismatch",           0.38),
    ("6",   "_procedure_patient_conflict", 0.55),
    ("45",  "_amount_excessive",           0.26),
    ("151", "_high_frequency",             0.30),
]

HIGH_FREQUENCY_CLAIMS = 4


def corrupt(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Inject the two coding errors the model has to learn to spot.

    Both reassign a real field rather than flipping a flag, so the mistake is a learnable
    relationship between fields the model can see, not unlearnable noise.
    """
    n = len(df)
    # A diagnosis recorded against the wrong procedure (CARC 11).
    wrong_dx = rng.random(n) < 0.045
    all_dx = np.sort(df.diagnosis_category.unique())
    df.loc[wrong_dx, "diagnosis_category"] = rng.choice(all_dx, size=wrong_dx.sum())
    # A procedure code that cannot apply to this patient (CARC 6).
    wrong_proc = rng.random(n) < 0.02
    df.loc[wrong_proc, "procedure_category"] = "obstetric_maternity"
    return df


def synthesise_process_fields(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    tier = df.procedure_category.map(
        {cat: t for t, cats in COMPLEXITY.items() for cat in cats}).to_numpy()
    docs = np.empty(n, dtype=object)
    for t, probs in DOCUMENTS_P.items():
        rows = tier == t
        docs[rows] = rng.choice(["complete", "partial", "missing"], size=rows.sum(), p=probs)
    df["documents_complete"] = docs
    # Filing delay: mostly prompt, with a long tail.
    df["days_since_treatment"] = np.clip(
        rng.gamma(shape=2.0, scale=7.0, size=n).round(), 0, 150).astype(int)
    df["provider_network_status"] = np.where(
        rng.random(n) < 0.06, "out_of_network", "in_network")
    df["patient_policy_active"] = rng.random(n) > 0.015
    df["prior_claims_30d"] = rng.poisson(0.9, size=n)
    p_dup = np.where(df.procedure_category.isin(RECURRING),
                     P_DUPLICATE["recurring"], P_DUPLICATE["other"])
    df["is_duplicate_submission"] = rng.random(n) < p_dup

    # Whether pre-authorisation was needed is a fact about the procedure and amount;
    # whether staff obtained it is the process field.
    df["pre_auth_obtained"] = np.nan
    ref = reference(df)
    df = derive_frame(df, ref)
    required = df.pre_auth_required_for_procedure == 1
    p_obtained = np.where(df.is_emergency, P_PRE_AUTH_OBTAINED_EMERGENCY, P_PRE_AUTH_OBTAINED)
    got_it = rng.random(n) < p_obtained
    mask = required.to_numpy()
    df.loc[mask, "pre_auth_obtained"] = np.where(got_it[mask], "yes", "no")
    return derive_frame(df, ref)


def reference(df: pd.DataFrame) -> dict:
    limits = df.groupby("insurer_tpa").insurer_filing_limit_days.first().to_dict()
    return build_reference(df, limits)


def build_conditions(df: pd.DataFrame) -> pd.DataFrame:
    df["_policy_inactive"] = ~df.patient_policy_active.astype(bool)
    df["_duplicate"] = df.is_duplicate_submission.astype(bool)
    missing = df.pre_auth_obtained == "no"
    df["_pre_auth_missing"] = missing & ~df.is_emergency
    df["_pre_auth_missing_emergency"] = missing & df.is_emergency
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
    df["_dx_proc_mismatch"] = df.diagnosis_procedure_match == 0
    df["_procedure_patient_conflict"] = df.procedure_patient_conflict == 1
    df["_amount_excessive"] = df.amount_vs_procedure_median > AMOUNT_EXCESSIVE_RATIO
    df["_high_frequency"] = df.prior_claims_30d >= HIGH_FREQUENCY_CLAIMS
    return df


def apply_rules(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    n = len(df)
    carc = np.array([None] * n, dtype=object)
    source = np.array(["approved"] * n, dtype=object)

    # The code an insurer would cite for this claim's most fundamental defect, whether or
    # not that defect's rule happened to fire.
    eligible = np.array([None] * n, dtype=object)
    for code, cond_col, _ in reversed(RULES):
        eligible[df[cond_col].to_numpy(dtype=bool)] = code

    for code, cond_col, p_fire in RULES:
        open_ = df[cond_col].to_numpy(dtype=bool) & (carc == None)  # noqa: E711
        fires = open_ & (rng.random(n) < p_fire)
        carc[fires] = code
        source[fires] = "rule"

    # Base rate: rejected with no clear driver, as happens in real adjudication.
    base = (carc == None) & (rng.random(n) < BASE_REJECTION_RATE)  # noqa: E711
    carc[base] = eligible[base]
    source[base] = "base_rate"
    rejected = (source != "approved")

    # Label noise, applied last, in both directions, to the outcome only.
    flip = rng.random(n) < LABEL_NOISE_RATE
    to_reject = flip & ~rejected
    to_accept = flip & rejected
    carc[to_reject] = eligible[to_reject]
    carc[to_accept] = None
    rejected = (rejected | to_reject) & ~to_accept
    source[flip] = "noise"

    df["is_rejected"] = rejected
    df["carc_code"] = carc
    df["label_source"] = source
    return df


def single_feature_auc(df: pd.DataFrame) -> pd.Series:
    """Leakage check: how well each input field predicts the outcome on its own."""
    y = df.is_rejected.astype(int)
    out = {}
    for f in ASKABLE_FEATURES + CONTEXT_FEATURES:
        if f in NUMERIC_FEATURES:
            # Rank on the value itself. Target-encoding every distinct amount would let a
            # continuous field "predict" the outcome by memorising rows.
            auc = roc_auc_score(y, df[f])
            out[f] = max(auc, 1 - auc)
        else:
            rate = df.groupby(df[f].astype(str)).is_rejected.transform("mean")
            out[f] = roc_auc_score(y, rate)
    return pd.Series(out).sort_values(ascending=False)


def main() -> int:
    rng = np.random.default_rng(SEED)
    src = PROC / "claims_base.parquet"
    if not src.exists():
        print(f"ERROR: {src} not found. Run ml/build_dataset.py first.", file=sys.stderr)
        return 1

    df = pd.read_parquet(src)
    print(f"Labelling {len(df)} claims...")

    df = corrupt(df, rng)
    df = synthesise_process_fields(df, rng)
    df = build_conditions(df)
    df = apply_rules(df, rng)

    carc_meta = json.loads((ROOT / "data" / "carc_codes.json").read_text())
    descriptions = {c["code"]: c["plain_english"] for c in carc_meta["codes"]}

    rate = df.is_rejected.mean()
    print(f"\n  rejection rate: {rate:.1%}   (target 22-28%)")
    if not 0.22 <= rate <= 0.28:
        print("  WARNING: outside the target band — tune rule probabilities.")
    print(f"  pre-authorisation required: {(df.pre_auth_required_for_procedure == 1).mean():.1%}")

    print("\n  label source:")
    print(df.label_source.value_counts().to_string().replace("\n", "\n    "))

    rej = df[df.is_rejected]
    print(f"\n  CARC distribution among rejected claims "
          f"({rej.carc_code.notna().sum()} coded, {rej.carc_code.isna().sum()} with no defect):")
    for code, cnt in rej.carc_code.value_counts().items():
        print(f"    {code:>4}  {cnt:4d}  {descriptions.get(code, '?')}")

    print("\n  leakage check (single-field AUC; nothing should approach 0.95):")
    print(single_feature_auc(df).head(5).round(3).to_string().replace("\n", "\n    "))

    df = df.drop(columns=[c for c in df.columns if c.startswith("_")])
    out = PROC / "claims.parquet"
    df.to_parquet(out, index=False)
    print(f"\n  rows: {len(df)}  columns: {len(df.columns)}")
    print(f"  written: {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
