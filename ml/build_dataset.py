"""Phase 2a — turn Synthea's raw CSVs into one claim-shaped row per encounter.

This step does NOT assign rejection outcomes; that is ml/label_carc.py.
Here we only reshape real Synthea output and re-skin it for a Pakistani
private-clinic context.

    python ml/build_dataset.py

Input : data/raw/*.csv        (Synthea sample bundle, fetched by scripts/fetch_synthea.py)
Output: data/processed/claims_base.parquet

Every step here is deterministic: the same five CSVs produce the same table, on any
machine. The input files are checked against the hashes they were pinned at, so a
silently changed upstream bundle fails loudly instead of quietly shifting the numbers.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

RECENCY_CUTOFF_YEAR = 2018
SEED = 42

# SHA-256 of the Synthea tables this script reads, as published in the pinned bundle.
RAW_SHA256 = {
    "encounters.csv": "16d209cb1429605c90c80cd859ac16569108dc46890f50eb6a125a0602235b3a",
    "procedures.csv": "0eb6949cc13053f31dda394db8de2dd8ec73a712b21b755cece14f8829f9534b",
    "conditions.csv": "a3141544140b626c9ae941043ad1afe4a0c201749b2e332c98f4cb2e2b7ec4cd",
    "patients.csv":   "0e8a61760644976571e7ede02e8be5bfece5111bc179d2139af87f84b630c859",
    "payers.csv":     "daa80d02749bbd1b1076d8daa290780c89a45802d4161b69cbff1918dc66e69f",
}

# Synthea bills in US dollars at US prices, and a flat rescale of those made an antenatal
# check-up cost PKR 116,000 and a dental consultation PKR 37,000. Instead each procedure
# category is anchored to a typical Pakistani private-clinic price (the median below),
# and each claim keeps its Synthea cost *relative to its own category*. That preserves
# what the model needs (which claims are unusually expensive for what they are) while the
# rupee figures on screen stay believable. These anchors are our estimates, not a tariff.
CATEGORY_MEDIAN_PKR = {
    "consultation_exam":    2_500,
    "preventive_screening": 4_000,
    "dental":               5_500,
    "vaccination":          3_000,
    "obstetric_maternity":  6_500,
    "dialysis":             8_500,
    "mental_health":        4_500,
    "laboratory":           2_800,
    "physiotherapy":        3_200,
    "minor_surgery":       55_000,
    "oncology_therapy":    65_000,
    "imaging":              7_500,
    "nursing_hospice":     28_000,
    "other_procedure":      6_000,
}
RELATIVE_COST_CLIP = (0.2, 6.0)   # keeps a single odd encounter from producing a silly bill
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

# Keywords are regular expressions matched against the lower-cased description. Most
# are plain stems ("diabet" must still catch "prediabetes"); the few short words that
# collide with longer ones carry explicit word boundaries. "labor" once matched
# "laboratory", which would turn lab work into maternity and invent CARC 6 rejections
# for men; on the diagnosis side it also matched "Not in labor force", a social-history
# note, and filed it as a pregnancy.
PROCEDURE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dental",                  ("dental", "gingiv", "tooth", "teeth", "plaque", "calculus", "oral health", "fluoride", "denture")),
    ("dialysis",                ("dialysis",)),
    ("obstetric_maternity",     ("fetal", "uterine", "pregnan", "obstetric", "prenatal", "delivery", r"\blabou?r\b", "episiotomy")),
    ("mental_health",           ("depression", "anxiety", "substance", "alcohol", "drug abuse", "domestic abuse", "mental")),
    ("preventive_screening",    ("screening", "assessment", "surveillance", "reconciliation", "health and social care")),
    ("imaging",                 ("x-ray", "ultrasound", r"\bct\b", r"\bmri\b", "radiograph", "imaging", "scan")),
    ("laboratory",              ("hemogram", "blood", "urine", "culture", "panel", "test", "measurement", "titer")),
    ("vaccination",             ("immunization", "immunotherapy", "vaccin", "injection")),
    ("oncology_therapy",        ("radiation therapy", "radiotherapy", "chemotherapy")),
    ("minor_surgery",           ("surgical", "excision", "suture", "biopsy", "removal of", "gingivectomy", "repair",
                                 "colonoscopy", "endoscopy", "cystoscopy")),
    ("nursing_hospice",         ("nursing", "hospice", "palliative", "care/supplementary")),
    ("physiotherapy",           ("physiotherapy", "physical therapy", "rehabilitation", "exercise",
                                 "occupational therapy", "movement therapy")),
    ("consultation_exam",       ("consultation", "examination", "physical exam", "referral", "education", "review", "history",
                                 "encounter for", "follow-up", "visit", "admission", "monitoring of patient", r"\(environment\)")),
]

DIAGNOSIS_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("dental_oral",             ("gingiv", "dental", "tooth", "teeth", "caries", "filling")),
    ("pregnancy_maternity",     ("pregnan", "contracept", r"\blabou?r\b(?! force)", "prenatal")),
    ("renal",                   ("kidney", "renal", "nephro")),
    ("respiratory_infection",   ("sinusitis", "bronchitis", "pharyngitis", "otitis", "asthma", "copd", "respiratory", "sore throat")),
    ("mental_health",           (r"\bstress", "anxiety", "depress", "isolation", "social contact", "alcohol", "substance", "abuse", "violence")),
    ("cardiometabolic",         ("hyperlipidemia", "diabet", "hypertens", "obesity", "body mass", "cardiac", "coronary",
                                 "anemia", "ischemic", "heart", "valve", "stenosis")),
    ("allergy",                 ("allerg",)),
    ("neurological",            ("cerebral palsy", "alzheimer", "dementia", "epilep", "parkinson", "seizure")),
    ("infectious",              ("immune deficiency", r"\bhiv\b", "tuberculosis", "hepatitis", "malaria", "dengue", "sepsis")),
    ("oncology",                ("neoplasm", "carcinoma", "cancer", "tumor")),
    ("injury_trauma",           ("laceration", "fracture", "sprain", "injury", "burn", "wound")),
    ("genitourinary",           ("cystitis", "urinary", "prostat")),
]

# When an encounter's most expensive procedures cost exactly the same, the claim is filed
# under whichever best describes the visit: a treatment over a test, a test over a
# screening, and referrals or a generic consultation last. Most ties are wellness visits
# where a depression screening, a social-needs assessment and a dental referral all carry
# the same price; those are screening visits, not dental bills.
TIE_BREAK_PRIORITY = [
    "minor_surgery", "oncology_therapy", "dialysis", "obstetric_maternity", "physiotherapy",
    "nursing_hospice", "imaging", "laboratory", "vaccination", "preventive_screening",
    "mental_health", "dental", "consultation_exam", "other_procedure",
]

_COMPILED: dict[int, list[tuple[str, re.Pattern]]] = {}


def _categorize(text: str | float, rules: list[tuple[str, tuple[str, ...]]], default: str) -> str:
    if not isinstance(text, str):
        return default
    compiled = _COMPILED.setdefault(
        id(rules), [(label, re.compile("|".join(kws))) for label, kws in rules])
    low = text.lower()
    for label, pattern in compiled:
        if pattern.search(low):
            return label
    return default


def price_in_pkr(category: pd.Series, cost_usd: pd.Series) -> pd.Series:
    """Anchor each category to its PKR median; keep each claim's cost relative to it."""
    category_median = cost_usd.groupby(category).transform("median")
    relative = (cost_usd / category_median).clip(*RELATIVE_COST_CLIP)
    pkr = category.map(CATEGORY_MEDIAN_PKR) * relative
    return ((pkr / PKR_ROUND_TO).round() * PKR_ROUND_TO).astype(int)


def verify_raw_inputs() -> list[str]:
    """Names of the pinned input files whose contents differ from the pinned hash."""
    bad = []
    for name, expected in RAW_SHA256.items():
        digest = hashlib.sha256((RAW / name).read_bytes()).hexdigest()
        if digest != expected:
            bad.append(name)
    return bad


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
    # A quarter of encounters (1,031 of 3,956) have several procedures at exactly the top
    # cost, often in different categories. An unstable sort broke those ties differently
    # on different machines, so the same bundle built different datasets and different
    # models. Ties now break toward the category that best describes the visit as billed
    # (TIE_BREAK_PRIORITY), then SNOMED code, then description, with a stable sort.
    proc["category"] = proc.DESCRIPTION.map(
        lambda t: _categorize(t, PROCEDURE_RULES, "other_procedure"))
    proc["tie_rank"] = proc.category.map(TIE_BREAK_PRIORITY.index)
    proc_sorted = proc.sort_values(["BASE_COST", "tie_rank", "CODE", "DESCRIPTION"],
                                   ascending=[False, True, True, True], kind="mergesort")
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
    enc["patient_gender"] = enc.PATIENT.map(pat_idx.GENDER)

    # --- Pakistani context re-skin ---
    enc["insurer_tpa"] = enc.payer_name.map(INSURER_RESKIN)
    enc["insurer_filing_limit_days"] = enc.insurer_tpa.map(FILING_LIMIT_DAYS)

    enc["claim_amount_pkr"] = price_in_pkr(enc.procedure_category, enc.TOTAL_CLAIM_COST)

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
        "patient_age", "patient_gender",
    ]].rename(columns={
        "Id": "claim_id", "PATIENT": "patient_id", "ORGANIZATION": "provider_id",
    }).reset_index(drop=True)

    return out


def main() -> int:
    print("Building claim base table from Synthea...")
    missing = [n for n in RAW_SHA256 if not (RAW / n).exists()]
    if missing:
        print(f"ERROR: Synthea CSVs not found in {RAW}: {missing}\n"
              "       Run: uv run python scripts/fetch_synthea.py", file=sys.stderr)
        return 1
    changed = verify_raw_inputs()
    if changed and "--allow-unpinned" not in sys.argv:
        print(f"ERROR: {changed} differ from the pinned Synthea bundle, so the dataset\n"
              "       would not match the published model. Re-fetch with\n"
              "       scripts/fetch_synthea.py, or pass --allow-unpinned to build anyway.",
              file=sys.stderr)
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
