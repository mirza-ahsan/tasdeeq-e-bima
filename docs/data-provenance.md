# Data Provenance

**The short version, for judges:** we are not pretending this is real clinic data. No public
dataset of real clinic claims paired with their accept/reject outcomes exists anywhere —
insurers and TPAs treat that as private business information, and every commercial product in
this space trains on its own private claims history. So we built ours from two real, published
components and one layer of our own logic, and this page states exactly where the line is.

---

## What is real

### Synthea — the clinical substrate

[Synthea](https://github.com/synthetichealth/synthea) is an open-source synthetic patient
generator from MITRE. It simulates patient lives through disease-progression models built on
real clinical care pathways, so diagnoses, procedures and encounters follow medically coherent
sequences rather than random draws. CMS has published an official synthetic Medicare claims
dataset built the same way.

**Bundle used:** `synthea_sample_data_csv_latest.zip`, downloaded 2026-09-05 from
`https://synthetichealth.github.io/synthea-sample-data/downloads/latest/`

| Table | Rows | Used for |
|---|---:|---|
| `patients.csv` | 108 | Age band, gender |
| `encounters.csv` | 5,571 | Claim grain, encounter class, cost, payer |
| `procedures.csv` | 15,884 | Procedure code + description (SNOMED CT) |
| `conditions.csv` | 3,517 | Diagnosis code + description (SNOMED CT) |
| `claims.csv` | 9,421 | Claim ID, service date, diagnosis linkage |
| `payers.csv` | 10 | Insurer identity (re-skinned, see below) |
| `organizations.csv` | 278 | Provider / facility |

Real code systems carried through: **SNOMED CT** for procedures and conditions, **RxNorm** for
medications, **LOINC** for observations.

### X12 CARC codes — the rejection vocabulary

Claim Adjustment Reason Codes are the real, published industry standard for why a claim was
reduced or denied, maintained by X12. We use a 12-code subset relevant to outpatient private-clinic
work, stored in [`data/carc_codes.json`](../data/carc_codes.json).

Every code number and its `official_description` was verified against
<https://x12.org/codes/claim-adjustment-reason-codes> on **2026-09-05**. We do not invent reason
codes, and we do not paraphrase official wording and present it as official — the JSON marks
per-field which values are X12's and which are ours.

---

## What is ours

Three things, stated plainly:

**1. The rejection labels.** Synthea generates claims but not accept/reject outcomes, because
real adjudication outcomes are exactly the private data nobody publishes. We assign them with a
rules-based labeller (`ml/label_carc.py`) that maps believable claim defects to the matching real
CARC code — a procedure needing pre-authorisation that has none gets CARC 197, and so on.

To keep the model from simply memorising our rule table:
- each rule fires **probabilistically**, not deterministically
- a ~3–5% base rejection rate fires with no clear driver
- ~8–12% of labels are flipped as noise
- target overall rejection rate: **22–28%**

**2. The Pakistani context layer.** Synthea generates US data. We re-skin it:

| Synthea | Ours |
|---|---|
| USD amounts | PKR, rounded to believable clinic invoice sizes |
| Aetna, Humana, Blue Cross, UnitedHealthcare, Cigna, Anthem, Medicare, Medicaid | Generic invented Pakistani insurer/TPA placeholder names |
| US plan structures | `basic` / `standard` / `premium` / `corporate` tiers |

The insurer names are **invented placeholders**. We are not attaching claim behaviour to real
Pakistani companies, because we have no data about how any real insurer actually adjudicates.

**3. Several claim-process fields Synthea does not model** — pre-authorisation status, document
completeness, filing delay, panel/network status, duplicate submission. These are the fields that
actually drive paperwork rejections, and they are synthesised as part of the labelling step.

### Recency filter

Synthea encounters span **1942–2026** because it simulates whole patient lifetimes. We keep
encounters from **2018 onward** (4,152 rows), which lands in the 2,000–5,000 target range and
avoids training on decades-old care patterns.

---

## What this means for the model

The honest framing: **the model learns our labelling logic, plus noise, on top of a realistic
clinical substrate.** It has learned the *structure* of paperwork-driven claim rejection — which
combinations of defects matter and how they interact — not the actual denial behaviour of any
real insurer.

That is why we hold accuracy to **70–85%** rather than pushing it higher. On self-generated data
a near-perfect score would only prove the model had reverse-engineered our rules, which is not
evidence it would work in production.

## What a production version would need

1. Real historical claims with real adjudication outcomes from a partner clinic or TPA
2. Parsed **835 remittance advice** files to capture the actual CARC codes insurers returned
3. Retraining as real outcomes accumulate, with a human correcting wrong predictions
4. Per-insurer models, since filing rules and tariffs differ between payers

The architecture we built supports all four. The demo simply cannot access the data they need.
