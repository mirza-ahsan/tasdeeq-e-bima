# Implementation Plan — Claim Rejection Risk Predictor

**Working checklist for the Alibaba Cloud "AI For Pakistan Future" hackathon.**
Tick items as they land. This document is now the single source of truth — it supersedes the
original `plan.md` (narrative/pitch) and `claim-predictor-project-brief.md` (build brief),
which have been removed from the repo. Inline `§` citations below refer back to those originals
and are kept as provenance for where each decision came from.

- **Timeline:** 3–7 days, solo
- **Language:** English only (Urdu dropped; see [Deferred](#deferred--explicitly-not-building))
- **Deployment:** local first, Alibaba Cloud as the final phase
- **Guiding rule:** this is a *demo prototype*. If a shortcut makes the demo easier to build without hiding the core idea, take it.

---

## The one-paragraph summary

A "spell-checker for insurance claims." Clinic staff answer a handful of adaptively-chosen
questions about a claim before submitting it. A LightGBM model returns a calibrated
rejection probability, SHAP names the specific fields driving the risk, a second model
predicts the most likely **real X12 CARC code**, and Qwen restates all of that as two
sentences of plain-language advice. Three things must be genuinely real and not mocked:
**the trained model, the adaptive question loop, and the Qwen call.**

---

## Architecture

```
Synthea sample CSVs ──► build_dataset.py ──► label_carc.py ──► claims.parquet
                                                                    │
                                                                    ▼
                                                     ┌──────────────────────────┐
                                                     │ train.py                 │
                                                     │  • risk_model  (binary)  │
                                                     │  • carc_model  (multi)   │
                                                     │  • isotonic calibration  │
                                                     │  • SHAP TreeExplainer    │
                                                     └───────────┬──────────────┘
                                                                 ▼
   Next.js UI  ◄──── FastAPI ◄──── adaptive.py (expected information gain)
   (chat flow,        │                 │
    risk gauge)       │                 └─► predictor.py ─► p(reject) + SHAP + CARC
                      │                                              │
                      └─────────────────► explain_qwen.py ◄──────────┘
                                            (DashScope / qwen-plus)
```

**Key decisions and why** — these are the answers if a judge asks:

| Decision | Rationale |
|---|---|
| LightGBM, not XGBoost | Handles `NaN` natively at inference. During the adaptive loop most fields are still unanswered, so we predict on half-empty rows after every question. No imputation scaffolding needed. |
| Trees, not a neural net | Research on claim-denial data shows NNs underperform trees here (`plan.md` §6). Also gives us attribution for free. |
| SHAP `TreeExplainer` | Signed **per-claim** field contributions ("pre-auth missing pushed this +31 points"). Global feature importance cannot say that. |
| Separate CARC model | Keeps the binary risk score independently calibrated. Mirrors the "Deep Claim" reason-prediction framing. |
| Real expected information gain, not importance ranking | Question order genuinely changes with prior answers — that is the demo moment, and it is the specific differentiator over Lemonade-style decision-tree flows (`plan.md` §6). |
| Isotonic calibration | So "72%" is a real number a judge can trust. |

---

## Phase 0 — Scaffold & credentials ✅ COMPLETE

- [x] `.gitignore` created — `.env` confirmed ignored via `git add --dry-run`
- [x] Cloud credential CSV exports (`*apiKey*.csv`) added to `.gitignore`
- [x] Folder structure: `data/ ml/ models/ backend/ frontend/ scripts/ docs/ tests/`
- [x] `pyproject.toml` with the full dependency set; `.venv` on Python 3.12
- [x] `.env.example` with placeholders for all four DashScope variables
- [x] `backend/config.py` — env-only config, key presence reported but never its value
- [x] `backend/services/explain_qwen.py` — Part C, strict prompt + template fallback
- [x] `scripts/test_dashscope.py` — standalone end-to-end check
- [x] **Verified live:** raw completion + explanation layer both returned `source: qwen`

---

## Phase 1 — Data acquisition ✅ COMPLETE

**Goal:** claim-shaped rows grounded in real clinical logic and a real reason-code vocabulary.

- [x] Download Synthea sample CSV bundle into `data/raw/` — from `.../synthea-sample-data/downloads/latest/` (the `downloads/` path in older docs 404s)
- [x] Confirm the files we need are present — all 18 tables extracted; 108 patients, 5,571 encounters, 9,421 claims, 15,884 procedures
- [x] `data/carc_codes.json` — 12 real X12 codes with official wording, plain-English text, trigger condition and staff action
- [x] Verify every CARC code against the official X12 published list — all 12 verified against x12.org on 2026-09-05
- [x] `docs/data-provenance.md` — what is real vs. what is ours, plus the production-gap section

**Decisions made during Phase 1** (carry into Phase 2):
- **Claim grain = encounter.** `encounters.csv` already carries payer, cost, class and provider, so one row per encounter is the natural claim row; `procedures`/`conditions` join on `ENCOUNTER`.
- **Recency filter: 2018 onward → 4,152 rows.** Synthea simulates whole lifetimes (1942–2026); training on decades-old care patterns is not useful. 2018+ lands in the 2,000–5,000 target.
- **`carc_codes.json` marks field provenance explicitly** — which values are X12's and which are ours. Do not blur this.
- Synthea uses **SNOMED CT** (not ICD-10/CPT) for procedures and conditions. Real standard codes either way; describe them accurately in the pitch.

**CARC codes to use** (verify wording against X12 before shipping):

| Code | Meaning (short) | Triggered by |
|---|---|---|
| 197 | Pre-authorization absent | Procedure requires pre-auth, none obtained |
| 29 | Time limit for filing expired | `days_since_treatment` > insurer filing limit |
| 16 | Claim lacks information / billing error | Documents incomplete |
| 11 | Diagnosis inconsistent with procedure | Dx/proc mismatch |
| 50 | Not deemed a medical necessity | Elective/cosmetic on a basic tier |
| 96 | Non-covered charge | Procedure outside plan coverage |
| 27 | Expenses after coverage terminated | Policy inactive on service date |
| 18 | Exact duplicate claim | Duplicate submission flag |
| 45 | Charge exceeds fee schedule | Amount ≫ procedure median |
| 109 | Not covered by this payer | Out-of-network provider |
| 6 | Procedure inconsistent with patient age | Age/procedure mismatch |
| 151 | Information does not support this frequency | Too many claims in 30 days |

---

## Phase 2 — Feature engineering & CARC labeling ✅ COMPLETE

- [x] `ml/build_dataset.py` — join Synthea tables into one row per claim (grain = encounter)
- [x] Re-skin to Pakistani private-clinic context:
  - [x] Amounts rescaled to **PKR** (×12, rounded to 50) — median PKR 12,100, max PKR 941,650
  - [x] **Fictional** insurer/TPA names built from ordinary Urdu words — deliberately not real Pakistani insurers
  - [x] Plan tiers `basic` / `standard` / `premium` / `corporate`, weighted by insurer type
- [x] `ml/label_carc.py` — rules-based labeller assigning CARC codes
  - [x] Each rule fires **probabilistically** (0.20–0.85), first rule in adjudication priority order wins
  - [x] Base random rejection rate **2.5%** with no clear driver
  - [x] Label noise: **8%** of labels flipped in both directions
  - [x] Overall rejection rate **26.5%** (target 22–28%)
- [x] Output `data/processed/claims.parquet` — **3,612 rows**
- [x] Leakage check — strongest single feature is `pre_auth_obtained` at **0.699 AUC**, nothing near 0.95

**Outcome:** all 12 CARC codes represented among rejected claims, 34–151 examples each — enough
for the multiclass CARC head in Phase 3.

**Decisions made during Phase 2** (carry into Phase 3):
- **`NO_INSURANCE` encounters dropped** (540 rows). An uninsured visit is not an insurance claim and cannot be accepted or rejected — leaving them in would teach the model nonsense.
- **`is_duplicate_submission` added as a 15th asked feature.** CARC 18 was in the code table with no feature behind it. Added rather than dropping the code.
- **Mismatch features are corrupted, not coin-flipped.** `diagnosis_procedure_match` and the CARC 6 age inconsistency are produced by reassigning a real field, so the inconsistency is a learnable relationship between two fields the model can see — not unlearnable noise.
- **Two tuning rounds needed.** The first pass came out at 41% rejection; both defect *incidence* and rule fire probabilities had to come down, not just the probabilities.

**Feature set as built** (22 features):

| Feature | Type | Asked in the flow? |
|---|---|---|
| `procedure_category` | categorical, 14 levels | ✅ |
| `diagnosis_category` | categorical, 13 levels | ✅ |
| `insurer_tpa` | categorical, 9 levels | ✅ |
| `plan_tier` | categorical, 4 levels | ✅ |
| `pre_auth_obtained` | yes / no / not_required | ✅ |
| `documents_complete` | complete / partial / missing | ✅ |
| `days_since_treatment` | numeric | ✅ |
| `claim_amount_pkr` | numeric | ✅ |
| `provider_network_status` | in / out of network | ✅ |
| `patient_policy_active` | boolean | ✅ |
| `is_emergency` | boolean | ✅ |
| `diagnosis_procedure_match` | boolean | ✅ |
| `is_duplicate_submission` | boolean | ✅ |
| `prior_claims_30d` | numeric | ✅ |
| `patient_age_band` | categorical, 6 levels | ✅ |
| `pre_auth_required_for_procedure` | derived boolean | ✖ |
| `amount_vs_procedure_median` | derived ratio | ✖ |
| `days_vs_insurer_filing_limit` | derived ratio | ✖ |
| `insurer_filing_limit_days` | known from insurer | ✖ |
| `patient_age` | numeric, from record | ✖ |
| `patient_gender` | categorical, from record | ✖ |
| `encounter_class` | categorical, from record | ✖ |

---

## Phase 3 — Train & calibrate ✅ COMPLETE

- [x] `ml/features.py` — single shared feature contract imported by training, the adaptive engine and the API, so the question flow can never drift from what the model was trained on
- [x] `ml/train.py`
  - [x] 80 / 10 / 10 stratified split (2,889 / 361 / 362)
  - [x] **Risk model:** LightGBM binary, early stopping on val — 85 trees
  - [x] **Masking augmentation** — 3 partial copies per claim, masked count drawn uniformly 0–14 so the model sees every state the loop passes through, including "nothing answered yet"
  - [x] Derived features masked whenever any of their inputs is masked
  - [x] Isotonic calibration on val, over the same partial-information distribution
  - [x] **CARC model:** LightGBM multiclass on rejected claims only (12 classes)
  - [x] SHAP `TreeExplainer` verified end to end + background sample persisted
  - [x] Persisted `risk_model.txt`, `carc_model.txt`, `calibrator.pkl`, `metadata.json`
- [x] `metadata.json` carries the feature contract, category vocabularies, per-feature value priors, derived-feature reference tables, operating points and metrics
- [x] `ml/evaluate.py` → `docs/model-card.md` with confusion matrix, calibration table, SHAP ranking and per-code accuracy
- [x] **Accuracy gate 70–85%: PASS at 77.9%**

**Final metrics**

| | Fully answered | Partially answered |
|---|---|---|
| ROC AUC | **0.788** | 0.733 |
| Accuracy | 77.9% | 71.3% |
| Precision / Recall / F1 | 57.0% / 67.7% / 0.619 | — |
| Majority-class baseline | 73.5% | — |
| CARC top-1 / top-3 | 68.2% / 79.7% | — |

**Problems found and fixed during Phase 3:**
- **Isotonic saturation.** Calibration mapped the top bin to exactly 1.0, but only 33% of claims scored 1.0 were actually rejected — real overconfidence, not a display quirk. Added `ml/calibration.py` clamping to [2%, 97%], imported by training, evaluation and the API so all three agree.
- **Recall collapsed to 19.8%** at the default 0.5 threshold — the model was playing the majority class, exactly the imbalance laziness the research notes warn about. Fixed with `scale_pos_weight` plus a decision threshold tuned on validation (0.23). Recall 19.8% → 67.7%, F1 0.311 → 0.619.
- **Accuracy was only +0.6% over the majority baseline.** A hyperparameter sweep found fewer masked copies and smaller trees consistently better; lift is now +4.4% and partial-information AUC rose 0.669 → 0.733.
- **`evaluate.py` was still hardcoding threshold 0.5** while training used the tuned value, so the model card disagreed with training by 1.4 points. Both now read `decision_threshold` from metadata.

**Corrections that carry into Phase 4:**
- **The stopping rule's "confidently risky" threshold must change.** The plan assumed `p > 0.70`, but the 95th percentile of predicted probability is **0.537** — `p > 0.70` would essentially never fire. Use the data-driven bands now stored in `metadata.json`: `risk_bands.low_max = 0.177`, `risk_bands.high_min = 0.403`.
- `metadata.json` stores `val_probability_quantiles` (p10 0.133 → p95 0.537). Tune stopping against these, not against guessed constants.
- Model binaries stay gitignored and are rebuilt with `python ml/train.py`. Phase 7 deployment must either train in the container or ship the artifacts separately.

---

## Phase 4 — Adaptive question engine

The technical centrepiece. Pure Python, no I/O, fully unit-testable.

- [ ] `backend/services/adaptive.py`
- [ ] `QUESTION_BANK`: feature → English question text + answer options + help text
- [ ] **Expected information gain** for each unanswered feature:
  1. For each candidate value of that feature, impute it and predict `p(reject)`
  2. Weight each outcome by that value's prior frequency from `metadata.json`
  3. Compute expected reduction in predictive (binary) entropy
  4. Ask the feature with the largest expected reduction
- [ ] Stopping rule — stop when **any** holds:
  - [ ] max expected information gain < threshold
  - [ ] `p(reject) < 0.15` (confidently clean) or `> 0.70` (confidently risky)
  - [ ] 8 questions asked
- [ ] Guard: never re-ask an answered field; always terminate
- [ ] `tests/test_adaptive.py`
  - [ ] A clean claim resolves in **≤ 3 questions**
  - [ ] A missing-pre-auth claim surfaces `pre_auth_obtained` early and flags CARC 197
  - [ ] Question order **differs** between the two demo claims (proves adaptiveness)
  - [ ] Loop always terminates within the cap

---

## Phase 5 — Backend API

- [ ] `backend/predictor.py` — load models once at startup; `predict(partial_answers) -> {probability, shap_fields, carc}`
- [ ] `backend/schemas.py` — Pydantic request/response models
- [ ] `backend/main.py` — FastAPI app, CORS for the Next.js dev origin
- [ ] Session state: in-memory dict keyed by `session_id`. No auth, no persistence beyond the process.
- [ ] SQLite (`backend/feedback.db`) for the "mark this wrong" log only

**Endpoints**

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/session/start` | `session_id`, first question, prior risk |
| `POST` | `/api/session/{id}/answer` | updated risk, next question **or** `done: true` |
| `GET` | `/api/session/{id}/result` | probability, SHAP fields, CARC code + description, Qwen explanation |
| `POST` | `/api/feedback` | logs the correction, returns `{ok: true}` |
| `GET` | `/api/demo/{risky\|clean}` | preloaded answers for the two scripted demo claims |
| `GET` | `/api/health` | model + Qwen reachability (**never** the key) |

- [ ] Wire `explain_qwen.explain()` into `/result`
- [ ] Confirm the fallback path renders acceptably (kill the network and check)

---

## Phase 6 — Frontend

- [ ] `npx create-next-app@latest frontend --typescript --tailwind --app`
- [ ] shadcn/ui for the primitives
- [ ] Components:
  - [ ] `ChatFlow` — one question at a time, previous answers visible above
  - [ ] `RiskGauge` — animated 0–100%, colour-banded, updates after every answer
  - [ ] `QuestionCard` — question + options + a one-line "why we're asking this"
  - [ ] `ExplanationCard` — final probability, flagged fields with SHAP bars, CARC code + description, Qwen text
  - [ ] `MarkWrongButton` — posts to `/api/feedback`, shows a confirmation toast
  - [ ] `DemoSwitcher` — one-click load of the risky / clean scripted claim
- [ ] Show the **question count** ("Question 2 of ~4") so the adaptive brevity is visible
- [ ] Visibly label the Qwen text as an explanation of the computed result, not a separate opinion

---

## Phase 7 — Demo, deployment, submission

**Demo**
- [ ] Scripted risky claim → high risk, CARC 197, resolves in ~5–6 questions
- [ ] Scripted clean claim → low risk, resolves in 2–3 questions (proves it doesn't flag everything)
- [ ] `docs/demo-script.md` — full walkthrough under 3 minutes, with the data-provenance line delivered *before* judges ask
- [ ] Rehearse end to end; have screenshots ready as a backup if the network dies

**Deployment**
- [ ] Confirm exact deployment requirements from the official hackathon rulebook
- [ ] `Dockerfile` (backend) + `Dockerfile` (frontend) + `docker-compose.yml`
- [ ] Alibaba Cloud ECS instance, security group opened for HTTP
- [ ] Env vars injected at the platform level — **never** bake `.env` into an image
- [ ] Nginx reverse proxy; confirm the live URL works from a phone on mobile data

**Submission package**
- [ ] Demo video (screen recording + voiceover, follows `docs/demo-script.md`)
- [ ] Architecture diagram (the flow above, cleaned up)
- [ ] Written project description — the problem framing, synthetic-data honesty, and
      differentiators are captured in [Anticipated judge questions](#anticipated-judge-questions)
      and [Architecture](#architecture) below
- [ ] Presentation deck — **PPT and PDF**
- [ ] `README.md` — setup instructions someone else could actually follow

---

## Deferred — explicitly not building

Recorded so we can answer "why didn't you…" confidently, and so scope doesn't creep back in.

| Not building | Why |
|---|---|
| **Urdu / language toggle** | Dropped by decision. The Qwen prompt and API layer stay language-parameterized, so it is a ~30-minute add-back if we change our mind before submission. |
| Auth, accounts, user management | Out of scope per brief §5 |
| Real retraining from feedback | Logging the correction demonstrates the idea; brief §5 says don't build the infra |
| Two-model deny-expert / accept-expert split | Stretch only, brief §5. Revisit only if Phases 1–6 finish early. |
| Live 835 file parsing | Describe it as the production path; don't claim to have built it (`plan.md` §6) |
| Persistence beyond the session | Out of scope per brief §5 |

---

## Standing guardrails

1. **Qwen explains, never invents.** It only ever sees the already-computed payload. Any change to `explain_qwen.py` must preserve rules 1–5 of the system prompt.
2. **Model accuracy stays in 70–85%.** Too good looks fake.
3. **Only real CARC codes.** Never invent a reason code or paraphrase official X12 wording as if it were official.
4. **Never commit `.env` or credential CSVs.** Re-run `git add --dry-run .` before any commit.
5. **Never print, log, or echo the API key** — presence only, never value, length, or prefix.
6. **The demo must survive a dead network.** Qwen falls back to a template; keep screenshots.

---

## Anticipated judge questions

| Question | Answer |
|---|---|
| "Is the data real?" | No, and no public dataset of real clinic claims + outcomes exists anywhere — insurers treat it as private. We built ours from Synthea (real clinical logic) plus the real X12 CARC vocabulary. Only the labeling rules are ours. |
| "Isn't the model too good?" | It's deliberately held at 70–85%. We added label noise and probabilistic rules specifically to avoid a fake-looking number. |
| "Why should a clinic trust this?" | It's a second check for staff, not a replacement for judgment. Every score comes with the specific field driving it and a real industry reason code — it shows its reasoning instead of outputting a black-box number. |
| "Is it actually adaptive, or just a form?" | Genuinely adaptive: we compute expected entropy reduction over every unanswered field after every answer. The two demo claims ask different questions in a different order — watch. |
| "Does the AI make the prediction?" | No. A calibrated LightGBM model computes it. Qwen only restates that result in plain language, under a prompt that forbids inventing a number, a reason, or a field. |
