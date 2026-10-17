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

**Bundle used:** `synthea_sample_data_csv_latest.zip` from
`https://synthetichealth.github.io/synthea-sample-data/downloads/latest/`, pinned by SHA-256
(`d61417b5…0907c35`) in [`scripts/fetch_synthea.py`](../scripts/fetch_synthea.py). The five
tables the build reads are pinned individually in `ml/build_dataset.py`, which refuses to run
on different files: Synthea publishes this bundle only under a "latest" URL, and a silently
changed upstream file would otherwise silently change every number in this repo.

| Table | Rows | Used for |
|---|---:|---|
| `patients.csv` | 108 | Age, sex |
| `encounters.csv` | 5,571 | Claim grain, encounter class, cost, payer |
| `procedures.csv` | 15,884 | Procedure code + description (SNOMED CT) |
| `conditions.csv` | 3,517 | Diagnosis code + description (SNOMED CT) |
| `payers.csv` | 10 | Insurer identity (re-skinned, see below) |

Real code systems carried through: **SNOMED CT** for procedures and conditions.

### X12 CARC codes — the rejection vocabulary

Claim Adjustment Reason Codes are the real, published industry standard for why a claim was
reduced or denied, maintained by X12. We use a 13-code subset relevant to outpatient
private-clinic work, stored in [`data/carc_codes.json`](../data/carc_codes.json).

Every code number and its `official_description` was verified against
<https://x12.org/codes/claim-adjustment-reason-codes> — twelve on **2026-09-05**, and CARC 7
(added when the audit pointed out that a sex mismatch is not an age mismatch) on **2026-10-17**. We do not invent reason
codes, and we do not paraphrase official wording and present it as official — the JSON marks
per-field which values are X12's and which are ours.

---

## What is ours

### 1. The claim table (`ml/build_dataset.py`)

- **Grain:** one claim per encounter, from **2018 onward** (Synthea simulates whole
  lifetimes, 1942–2026). Uninsured encounters are dropped — an uninsured visit is not an
  insurance claim. 5,571 → 4,152 → **3,612 claims**.
- **Primary procedure:** the encounter's most expensive procedure. A quarter of encounters
  have several procedures at exactly that cost, often in different categories; ties break
  toward the category that best describes the visit as billed (treatment, then diagnostics,
  then screening, then referrals and consultations), then SNOMED code, then description, with
  a stable sort. The earlier unstable sort broke these ties differently on different machines,
  which is why a rebuild elsewhere once produced a different, weaker model.
- **Categories:** SNOMED descriptions are mapped to 14 procedure and 13 diagnosis categories by
  keyword. Short words that collide with longer ones carry word boundaries — "labor" no longer
  matches "laboratory", or "Not in labor force" as a pregnancy.
- **Prices in PKR.** Synthea bills US encounters at US prices, and a flat rescale made an
  antenatal check-up cost PKR 116,000. Instead each procedure category is anchored to a
  typical Pakistani private-clinic price (our estimate — consultation PKR 2,500, dental 5,500,
  minor surgery 55,000, oncology 65,000…) and each claim keeps its Synthea cost *relative to
  its own category*, clipped to 0.2–6×. Which claims are unusually expensive for what they are
  is preserved; the rupee figures become believable.

### 2. The Pakistani context layer

| Synthea | Ours |
|---|---|
| USD amounts | PKR, per-category anchored as above |
| Aetna, Humana, Blue Cross, UnitedHealthcare, Cigna, Anthem, Medicare, Medicaid | Invented insurer/TPA names from ordinary Urdu words |
| US plan structures | `basic` / `standard` / `premium` / `corporate` tiers, weighted by insurer type |
| — | Per-insurer filing windows (30, 45, 60 or 90 days) |

The insurer names are **invented placeholders**. We are not attaching claim behaviour to real
Pakistani companies, because we have no data about how any real insurer actually adjudicates.

### 3. The paperwork fields and the rejection labels (`ml/label_carc.py`)

Synthea generates claims but not accept/reject outcomes, and does not model the paperwork that
drives most rejections. We synthesise: pre-authorisation obtained, document completeness, days
since treatment, panel status, policy status, duplicate submission, and prior claims. Two of
these depend on what was done, as they do in practice: incomplete documents are commoner for
complex procedures (surgery, dialysis, oncology) than for a consultation, and duplicates
cluster in recurring treatment (dialysis, physiotherapy).

Then a rules-based labeller maps believable defects to the matching real CARC code — a
procedure that needed pre-authorisation and did not have it gets CARC 197, and so on. The rule
tables (which procedures need pre-authorisation, which plan tiers exclude what, which diagnoses
justify which procedures) live in [`ml/features.py`](../ml/features.py), the same module the
API's rule checks read, so the checks a clerk sees and the labels the model learned from cannot
disagree.

To keep the model from simply memorising our rule table:
- each rule fires **probabilistically** (0.20–0.85), first rule in adjudication order wins;
  emergencies get leeway on pre-authorisation (0.30 instead of 0.72);
- a **2%** base rejection rate fires with no driver;
- **5%** of outcomes are flipped as noise, in both directions;
- overall rejection rate: **23.7%** (target 22–28%).

**Noise touches the outcome, not the reason.** A claim flipped to "rejected", or rejected at
the base rate, is given the code of the most fundamental defect it actually has; if it has
none, it has no code and the reason model never trains on it (145 of 856 rejections). An
earlier version drew those codes at random, which made almost a third of the reason model's
targets noise.

Two coding errors are injected by reassigning a real field rather than flipping a flag, so the
mistake is a learnable relationship between visible fields: a diagnosis recorded against the
wrong procedure (CARC 11), and an obstetric procedure billed for a patient it cannot apply to
— CARC 7 when the patient is male, CARC 6 when the age is outside 12–55.

**Why the noise is 5%, not 8%.** At 8% flips plus a 2.5% base rate, a claim with no defect at
all was still rejected about one time in ten, so no amount of questioning could certify a clean
claim — the "looks ready to submit" verdict was only reachable by placing a band boundary
between two calibration plateaus. At 5% and 2%, a fully answered claim still tops out near
0.85 AUC, well short of the memorisation the noise is there to prevent.

**Leakage check:** the strongest single field predicts the outcome at 0.59 AUC on its own;
nothing approaches 0.95.

---

## What this means for the model

The honest framing: **the model learns our labelling logic, plus noise, on top of a realistic
clinical substrate.** It has learned the *structure* of paperwork-driven claim rejection —
which combinations of defects matter and how they interact — not the actual denial behaviour of
any real insurer.

That is why we hold accuracy to **70–85%** rather than pushing it higher. On self-generated data
a near-perfect score would only prove the model had reverse-engineered our rules, which is not
evidence it would work in production. It is also why the reason-code model scores so well
(see the [model card](model-card.md)): our codes follow our rules far more consistently than
real insurers' do.

## Reproducing it

```bash
uv run python scripts/fetch_synthea.py     # pinned bundle, checksum-verified
uv run python ml/build_dataset.py          # refuses to run on unpinned inputs
uv run python ml/label_carc.py
uv run python ml/train.py
uv run python ml/evaluate.py
```

Every step is deterministic. CI rebuilds from scratch weekly and fails if the labelled dataset
is not byte-for-byte the committed one or the metrics drift past tolerance
(`scripts/check_drift.py`).

## What a production version would need

1. Real historical claims with real adjudication outcomes from a partner clinic or TPA
2. Parsed **835 remittance advice** files to capture the actual CARC codes insurers returned
3. Retraining as real outcomes accumulate, with a human correcting wrong predictions
4. Per-insurer models and rule tables, since filing rules, pre-authorisation lists and tariffs
   differ between payers

The architecture supports all four. The demo simply cannot access the data they need.
