"""Phase 2a — turn Synthea's raw CSVs into one claim-shaped row per encounter.

This step does NOT assign rejection outcomes; that is ml/label_carc.py.
Here we only reshape real Synthea output and re-skin it for a Pakistani
private-clinic context.

    python ml/build_dataset.py

Input : data/raw/*.csv        (Synthea sample bundle)
Output: data/processed/claims_base.parquet
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

RECENCY_CUTOFF_YEAR = 2018
SEED = 42

# Synthea prices are US healthcare prices, which are far above Pakistani private-clinic
# rates. This is a rescale to believable PKR clinic invoice sizes, NOT an FX conversion.
PKR_SCALE = 12
PKR_ROUND_TO = 50

# ---------------------------------------------------------------------------
# Pakistani context layer
# ---------------------------------------------------------------------------

# FICTIONAL insurer/TPA names. These are invented placeholders built from ordinary
# Urdu words; they are deliberately NOT the names of real Pakistani insurers. We have
# no data on how any real insurer adjudicates, so attaching behaviour to a real brand
# would be dishonest. See docs/data-provenance.md.
INSURER_RESKIN = {
    "Medicare":               "Nigheban Health Board",
    "Medicaid":               "Sarparast Welfare Cover",
    "Dual Eligible":          "Nigheban Plus Scheme",
    "Humana":                 "Amanat Health Cover",
    "Blue Cross Blue Shield": "Rehnuma Insurance",
    "UnitedHealthcare":       "Zarrin Medical Cover",
    "Aetna":                  "Peymaan Health",
    "Cigna Health":           "Kohsar Health Plan",
    "Anthem":                 "Aftab Health Assurance",
}

# Invented per-insurer filing deadlines (days). Drives CARC 29.
FILING_LIMIT_DAYS = {
    "Nigheban Health Board":   90,
    "Sarparast Welfare Cover": 90,
    "Nigheban Plus Scheme":    90,
    "Amanat Health Cover":     45,
    "Rehnuma Insurance":       30,
    "Zarrin Medical Cover":    45,
    "Peymaan Health":          60,
    "Kohsar Health Plan":      30,
    "Aftab Health Assurance":  60,
}

PLAN_TIERS = ["basic", "standard", "premium", "corporate"]
# Government-style schemes skew basic; private insurers skew higher.
PLAN_TIER_WEIGHTS = {
    "Nigheban Health Board":   [0.55, 0.30, 0.10, 0.05],
    "Sarparast Welfare Cover": [0.70, 0.25, 0.04, 0.01],
    "Nigheban Plus Scheme":    [0.50, 0.35, 0.10, 0.05],
    "Amanat Health Cover":     [0.20, 0.40, 0.25, 0.15],
    "Rehnuma Insurance":       [0.15, 0.35, 0.30, 0.20],
    "Zarrin Medical Cover":    [0.20, 0.40, 0.25, 0.15],
    "Peymaan Health":          [0.15, 0.35, 0.30, 0.20],
    "Kohsar Health Plan":      [0.25, 0.40, 0.25, 0.10],
    "Aftab Health Assurance":  [0.10, 0.30, 0.35, 0.25],
}

# ---------------------------------------------------------------------------
# Category mapping (SNOMED CT free text -> a vocabulary small enough to ask about)
# ---------------------------------------------------------------------------

PROCEDURE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dental",                  ("dental", "gingiv", "tooth", "teeth", "plaque", "calculus", "oral health", "fluoride", "denture")),
    ("dialysis",                ("dialysis",)),
    ("obstetric_maternity",     ("fetal", "uterine", "pregnan", "obstetric", "prenatal", "delivery", "labor", "episiotomy")),
    ("mental_health",           ("depression", "anxiety", "substance", "alcohol", "drug abuse", "domestic abuse", "mental")),
    ("preventive_screening",    ("screening", "assessment", "surveillance", "reconciliation", "health and social care")),
    ("imaging",                 ("x-ray", "ultrasound", "ct ", "mri", "radiograph", "imaging", "scan")),
    ("laboratory",              ("hemogram", "blood", "urine", "culture", "panel", "test", "measurement", "titer")),
    ("vaccination",             ("immunization", "immunotherapy", "vaccin", "injection")),
    ("oncology_therapy",        ("radiation therapy", "radiotherapy", "chemotherapy")),
    ("minor_surgery",           ("surgical", "excision", "suture", "biopsy", "removal of", "gingivectomy", "repair",
                                 "colonoscopy", "endoscopy", "cystoscopy")),
    ("nursing_hospice",         ("nursing", "hospice", "palliative", "care/supplementary")),
    ("physiotherapy",           ("physiotherapy", "physical therapy", "rehabilitation", "exercise",
                                 "occupational therapy", "movement therapy")),
    ("consultation_exam",       ("consultation", "examination", "physical exam", "referral", "education", "review", "history",
                                 "encounter for", "follow-up", "visit", "admission", "monitoring of patient", "(environment)")),
]

DIAGNOSIS_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dental_oral",             ("gingiv", "dental", "tooth", "teeth", "caries", "filling")),
    ("pregnancy_maternity",     ("pregnan", "contracept", "labor", "prenatal")),
    ("renal",                   ("kidney", "renal", "nephro")),
    ("respiratory_infection",   ("sinusitis", "bronchitis", "pharyngitis", "otitis", "asthma", "copd", "respiratory", "sore throat")),
    ("mental_health",           ("stress", "anxiety", "depress", "isolation", "social contact", "alcohol", "substance", "abuse", "violence")),
    ("cardiometabolic",         ("hyperlipidemia", "diabet", "hypertens", "obesity", "body mass", "cardiac", "coronary",
                                 "anemia", "ischemic", "heart", "valve", "stenosis")),
    ("allergy",                 ("allerg",)),
    ("neurological",            ("cerebral palsy", "alzheimer", "dementia", "epilep", "parkinson", "seizure")),
    ("infectious",              ("immune deficiency", "hiv", "tuberculosis", "hepatitis", "malaria", "dengue", "sepsis")),
    ("oncology",                ("neoplasm", "carcinoma", "cancer", "tumor")),
    ("injury_trauma",           ("laceration", "fracture", "sprain", "injury", "burn", "wound")),
    ("genitourinary",           ("cystitis", "urinary", "prostat")),
    ("social_determinant",      ("employment", "labor force", "unemployed", "medication review")),
]


def _categorize(text: str | float, rules: list[tuple[str, tuple[str, ...]]], default: str) -> str:
    if not isinstance(text, str):
        return default
    low = text.lower()
    for label, keywords in rules:
        if any(k in low for k in keywords):
            return label
    return default


def _age_band(age: float) -> str:
    if age < 1:
        return "infant"
    if age < 13:
        return "child"
    if age < 20:
        return "adolescent"
    if age < 40:
        return "adult_20_39"
    if age < 60:
        return "adult_40_59"
    return "senior_60_plus"


def build() -> pd.DataFrame:
    rng = np.random.default_rng(SEED)

    enc = pd.read_csv(RAW / "encounters.csv")
    pat = pd.read_csv(RAW / "patients.csv")
    proc = pd.read_csv(RAW / "procedures.csv")
    cond = pd.read_csv(RAW / "conditions.csv")
    payers = pd.read_csv(RAW / "payers.csv")

    enc["service_date"] = pd.to_datetime(enc.START, format="mixed", utc=True)

    n0 = len(enc)
    enc = enc[enc.service_date.dt.year >= RECENCY_CUTOFF_YEAR].copy()
    print(f"  recency filter (>= {RECENCY_CUTOFF_YEAR}): {n0} -> {len(enc)}")

    # Payer name, then drop uninsured visits — an uninsured visit is not an
    # insurance claim and cannot be accepted or rejected by an insurer.
    enc["payer_name"] = enc.PAYER.map(payers.set_index("Id").NAME)
    n1 = len(enc)
    enc = enc[enc.payer_name != "NO_INSURANCE"].copy()
    print(f"  drop NO_INSURANCE encounters:   {n1} -> {len(enc)}")

    # --- primary procedure per encounter: the most expensive one ---
    proc_sorted = proc.sort_values("BASE_COST", ascending=False)
    primary_proc = proc_sorted.drop_duplicates("ENCOUNTER").set_index("ENCOUNTER")
    enc["procedure_desc"] = enc.Id.map(primary_proc.DESCRIPTION)
    enc["procedure_code"] = enc.Id.map(primary_proc.CODE)

    # Encounters with no recorded procedure fall back to the encounter itself,
    # which is how a consultation-only visit would actually be billed.
    enc["procedure_desc"] = enc.procedure_desc.fillna(enc.DESCRIPTION)

    # --- primary diagnosis: prefer a clinical disorder, else the encounter reason ---
    disorders = cond[cond.DESCRIPTION.str.contains(r"\(disorder\)", na=False)]
    primary_cond = disorders.drop_duplicates("ENCOUNTER").set_index("ENCOUNTER")
    enc["diagnosis_desc"] = enc.Id.map(primary_cond.DESCRIPTION)
    enc["diagnosis_desc"] = enc.diagnosis_desc.fillna(enc.REASONDESCRIPTION)
    enc["diagnosis_desc"] = enc.diagnosis_desc.fillna("General consultation (situation)")

    # --- categorise ---
    enc["procedure_category"] = enc.procedure_desc.map(
        lambda t: _categorize(t, PROCEDURE_RULES, "other_procedure"))
    enc["diagnosis_category"] = enc.diagnosis_desc.map(
        lambda t: _categorize(t, DIAGNOSIS_RULES, "general_unspecified"))

    # --- patient age at service date ---
    pat_idx = pat.set_index("Id")
    birth = pd.to_datetime(enc.PATIENT.map(pat_idx.BIRTHDATE), format="mixed", utc=True)
    enc["patient_age"] = ((enc.service_date - birth).dt.days / 365.25).round(1)
    enc["patient_age_band"] = enc.patient_age.map(_age_band)
    enc["patient_gender"] = enc.PATIENT.map(pat_idx.GENDER)

    # --- Pakistani context re-skin ---
    enc["insurer_tpa"] = enc.payer_name.map(INSURER_RESKIN)
    enc["insurer_filing_limit_days"] = enc.insurer_tpa.map(FILING_LIMIT_DAYS)

    enc["claim_amount_pkr"] = (
        (enc.TOTAL_CLAIM_COST * PKR_SCALE / PKR_ROUND_TO).round() * PKR_ROUND_TO
    ).astype(int)

    tiers = []
    for insurer in enc.insurer_tpa:
        tiers.append(rng.choice(PLAN_TIERS, p=PLAN_TIER_WEIGHTS[insurer]))
    enc["plan_tier"] = tiers

    enc["encounter_class"] = enc.ENCOUNTERCLASS
    enc["is_emergency"] = enc.ENCOUNTERCLASS.isin(["emergency", "urgentcare"])

    out = enc[[
        "Id", "PATIENT", "ORGANIZATION", "service_date",
        "procedure_category", "procedure_desc", "procedure_code",
        "diagnosis_category", "diagnosis_desc",
        "insurer_tpa", "insurer_filing_limit_days", "plan_tier",
        "claim_amount_pkr", "encounter_class", "is_emergency",
        "patient_age", "patient_age_band", "patient_gender",
    ]].rename(columns={
        "Id": "claim_id", "PATIENT": "patient_id", "ORGANIZATION": "provider_id",
    }).reset_index(drop=True)

    return out


def main() -> int:
    print("Building claim base table from Synthea...")
    if not (RAW / "encounters.csv").exists():
        print(f"ERROR: Synthea CSVs not found in {RAW}", file=sys.stderr)
        return 1

    df = build()
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / "claims_base.parquet"
    df.to_parquet(path, index=False)

    print(f"\n  rows: {len(df)}  columns: {len(df.columns)}")
    print(f"  written: {path.relative_to(ROOT)}")
    print("\n  procedure_category:")
    print(df.procedure_category.value_counts().to_string().replace("\n", "\n    "))
    print("\n  diagnosis_category:")
    print(df.diagnosis_category.value_counts().to_string().replace("\n", "\n    "))
    print("\n  claim_amount_pkr:")
    print(df.claim_amount_pkr.describe([.25, .5, .75, .95]).round(0).to_string().replace("\n", "\n    "))
    return 0


if __name__ == "__main__":
    sys.exit(main())
