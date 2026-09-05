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

## Phase 4 — Adaptive question engine ✅ COMPLETE

- [x] `backend/predictor.py` — model serving: probability, SHAP attribution, CARC prediction. Built in full here rather than stubbed, because the engine needs real predictions; Phase 5 only has to wire HTTP.
- [x] `backend/services/adaptive.py` — question bank + information-gain engine
- [x] `QUESTION_BANK`: all 14 askable features with English question text, answer options and a one-line "why we're asking"
- [x] **Expected information gain** per unanswered feature, batched into one LightGBM call (~150 predictions per question)
- [x] Stopping rule — max gain below threshold, confident low/high band, or the 8-question cap
- [x] Guards: never re-asks an answered field, always terminates
- [x] `tests/test_adaptive.py` — **11 tests, all passing**

**Calibrated behaviour** (200 held-out claims):

| | |
|---|---|
| Mean questions, truly approved | **3.71** |
| Mean questions, truly rejected | **4.14** |
| Resolved in ≤3 questions | 67% |
| Hit the 8-question cap | 4% |
| Distinct question orders | **72 / 200** |
| Low band | 143 claims, 87% genuinely approved |
| High band | 38% genuinely rejected (base rate 26.5%) |

`MIN_QUESTIONS` is **3** — the tool never delivers a verdict on fewer than three answers,
even when already confident. A verdict from one question reads as glib to a clinic user.

72 distinct question orders across 200 claims is the evidence for the adaptiveness claim —
it is not a fixed form, and the demo can show two claims being asked different things.

**Problems found and fixed during Phase 4:**
- **Information gains came out negative**, with `documents_complete` — the model's single
  strongest predictor — scoring the *most* negative. Cause: the baseline used the model's
  prediction with the field left unknown, but LightGBM sends a missing value down a learned
  default branch rather than averaging over what the value might have been, so the Jensen
  inequality guaranteeing non-negativity did not hold. Fixed by marginalising per feature:
  the baseline is now `H(Σ P(f=v)·p_v)`, making the quantity a true mutual information —
  non-negative by construction and comparable across fields. A regression test asserts this.
- **`risk_bands.low_max` from Phase 3 was unusable.** At the p25 value of 0.177 it sat
  *below* the model's own no-information prediction (0.188), so almost no claim could ever
  reach the low band: only 2 of 120 did, and the loop ran to the question cap on 62% of
  claims. Recalibrated to 0.20 by simulating the whole loop.
- **`high_min` cannot be lowered.** Dropping it from 0.40 to 0.30 to catch more risky claims
  made rejected claims stop as fast as clean ones (2.23 vs 2.21 questions), erasing the
  adaptive behaviour the demo is built on. Held at 0.40.
- **`MIN_INFO_GAIN` is sharply non-linear.** At 0.03 the loop stops before asking anything
  at all. Calibrated to 0.006.

**Known approximation, stated rather than hidden:** `P(f = v)` uses the marginal training
frequency rather than a posterior conditioned on answers so far. Modelling the joint
distribution over answers is a much larger build; the marginal is a standard stand-in and
is documented in the module docstring.

**Note for Phase 5:** high-band precision is 38% against a 26.5% base rate. That is not a
defect — the band starts at p ≥ 0.40 and the model is calibrated, so roughly 40% of those
claims being rejected is the number behaving correctly. Do not "fix" it.

---

## Phase 5 — Backend API ✅ COMPLETE

- [x] `backend/predictor.py` — built in Phase 4; loads models once at startup
- [x] `backend/schemas.py` — Pydantic request/response models
- [x] `backend/main.py` — FastAPI app, CORS for the Next.js dev origin
- [x] `backend/feedback_store.py` — SQLite log for "mark this wrong"
- [x] `backend/demo_claims.py` — the two scripted claims
- [x] Session state: in-memory dict keyed by `session_id`, bounded at 500 with oldest-first eviction. No auth, no persistence beyond the process.
- [x] Display helpers in `adaptive.py` — `feature_label()` / `value_label()` turn model field names into text a clinic clerk reads
- [x] Qwen wired into `/result`; fallback path verified
- [x] `tests/test_api.py` — **9 tests**; full suite now **20 passing**

**Endpoints**

| Method | Path | Returns |
|---|---|---|
| `POST` | `/api/session/start` | `session_id`, first question, prior risk |
| `POST` | `/api/session/{id}/answer` | updated risk, next question **or** `done: true` |
| `GET` | `/api/session/{id}/result` | probability, SHAP fields, CARC + description, Qwen explanation |
| `POST` | `/api/feedback` | logs the correction |
| `GET` | `/api/demo/{risky\|clean}` | starts a session on a scripted claim |
| `GET` | `/api/demo/{which}/answers` | scripted answers, so the UI can auto-fill |
| `GET` | `/api/health` | model + Qwen reachability, never the key |

**Verified demo walkthrough**

```
RISKY: 4 questions | 21% → 38% → 66% [high] | CARC 197
  "There is a 66% chance this claim will be rejected, mainly because
   pre-authorisation was not obtained. Please obtain and attach the
   required pre-authorisation before submitting the claim."

CLEAN: 3 questions | 21% → 19% → 18% [low]  | no CARC
  "The rejection risk is low at 18%, mainly because all supporting
   documents are complete. The claim looks ready to submit."
```

**Problems found and fixed during Phase 5:**
- **Qwen was inventing work on clean claims.** It read a `reduces_risk` field as a problem and told staff to "ensure all supporting documents are attached" when the documents were already complete. The prompt now branches explicitly on whether any field increases risk, with a standing rule never to ask staff to fix something already correct.
- **A clean claim was being given a CARC code.** The CARC head is trained on rejected claims only, so its output is meaningless for a claim that looks fine — it was confidently returning "duplicate submission" for a spotless claim. `/result` now withholds the reason code entirely when the band is `low`. A test asserts it.
- **Calibration was changed and then changed back.** Isotonic's plateaus (76 distinct values, 33% of claims on one) froze the risk gauge across consecutive answers, so Platt scaling was tried. It gave 340 distinct values and marginally better AUC, but collapsed distinct question orders from 72 to 15 and — at every band setting tried — made risky claims resolve *faster* than clean ones. Reverted to isotonic and documented both results in `ml/calibration.py`.

**Finding worth keeping for the pitch:** under smooth calibration, risky claims are genuinely faster to identify than clean claims are to certify. One bad answer clears the high threshold immediately; proving a claim is clean means ruling out every remaining risk factor. The brief's assumption that simple claims resolve fastest holds here only because isotonic's plateau sits just under the low band.

**Known cosmetic limitation:** the risk figure can sit unchanged across consecutive answers (the risky demo path is 21% → 38% → 38% → 38% → 66%). That is honest — those answers genuinely did not move the estimate — but **the UI must show "no change" explicitly rather than pretending to animate**, or it reads as a broken gauge. Phase 6 requirement.

---

## Phase 6 — Frontend ✅ COMPLETE

Next.js 16 (App Router) + Tailwind v4. **shadcn/ui was skipped deliberately** — its defaults
are the look every AI-built demo has. Components are hand-written against a small token set,
which is what makes this look like a clinical instrument rather than a template.

- [x] `app/globals.css` — design tokens, light + dark, one keyframe
- [x] `app/layout.tsx` — Newsreader (display), IBM Plex Sans (body), IBM Plex Mono (figures)
- [x] `lib/api.ts` + `lib/types.ts` — typed client mirroring the Pydantic schemas
- [x] `components/Masthead.tsx` — provenance line: model, claim count, AUC
- [x] `components/StartScreen.tsx` — the pitch in two paragraphs, three entry points
- [x] `components/QuestionCard.tsx` — one question, lettered options, live info-gain figure
- [x] `components/AnswerLedger.tsx` — answers so far as a record
- [x] `components/RiskMeter.tsx` — measuring scale with ticks and band regions
- [x] `components/FlaggedFields.tsx` — SHAP contributions as signed bars
- [x] `components/ResultPanel.tsx` — verdict, Qwen reading, CARC citation, mark-wrong
- [x] Verified end to end in a browser on both demo claims
- [x] `npx tsc --noEmit` clean, production build clean

**Design decisions**
- Warm paper ground and ink text, not white-on-grey. Hairline rules instead of floating cards
  and drop shadows. No gradients, no glassmorphism, no purple.
- A serif display face (Newsreader) is the single biggest departure from the generic look.
- Motion is limited to one 220ms entrance per question and a 300ms marker slide. Everything
  respects `prefers-reduced-motion`.
- The **info-gain figure is shown on every question**. It is the evidence that the question
  was chosen rather than scripted — worth pointing at during the walkthrough.
- The CARC code is presented as a **formal citation**, with the official X12 wording in mono
  beneath the plain-English version and the standard named. It reads as a reference, not a
  chat reply.
- Qwen's text is captioned with what it can and cannot do: *"restating the figures above. It
  cannot change the probability, the reason code, or which fields were flagged."*

**Problems found and fixed during Phase 6:**
- **The risk meter showed "no change" before any question was answered**, because React's
  development double-invoke ran the effect twice on mount.
- **The change note went stale.** It was keyed on the percentage, so an answer that left the
  estimate untouched kept showing the *previous* answer's delta — the risky claim read
  "▲ 17 pts" three times in a row. Both fixed by keying the note on the answer count and
  storing `{sequence, pct}` together.
- **Zero-confidence reason codes were being listed.** The result showed "Also possible: CARC
  16 (0%), CARC 27 (0%)", which reads as a bug. Filtered below 5% in the API.
- **Negligible SHAP contributions were being shown.** "Patient age, −0.8 points" is not
  something a clerk can act on and it dilutes the fields that matter. Filtered below 1.0
  percentage point in the API.

**Cleanup done in this phase:**
- Removed `StartRequest.prefill` — declared and handled, but nothing ever sent it
- Removed `models/shap_background.parquet` and the code writing it — TreeExplainer on
  LightGBM is tree-path-dependent and needs no background dataset; nothing ever read it
- Removed `feedback_store.count()` — no callers
- Removed unused `DERIVED_FEATURES` import from `train.py`
- Removed the Next.js template leftovers: `public/*.svg`, the default `favicon.ico`,
  and the scaffold's `AGENTS.md` / `CLAUDE.md`. Added a real `app/icon.svg`.

**Verified walkthrough**

| | Risky claim | Clean claim |
|---|---|---|
| Questions | 4 | 3 |
| Path | 21% → 38% → 66% | 21% → 19% → 18% |
| Verdict | "This claim is likely to be rejected." | "This claim looks ready to submit." |
| Reason | CARC 197, 99% confidence | none shown, with the reason why |
| Stop | confident high risk | confident low risk |

---

## Phase 7 — Demo, deployment, submission

**Demo** ✅
- [x] Scripted risky claim → 66%, CARC 197, resolves in 4 questions
- [x] Scripted clean claim → 18%, no reason code, resolves in 3 questions
- [x] `docs/demo-script.md` — timed to under 3 minutes, with the data-provenance line
      delivered *before* judges ask, prepared answers to the six likely questions, and a
      what-to-do-if-it-breaks section
- [ ] **Rehearse it end to end** — needs you

**Deployment** — prepared, not executed
- [x] `Dockerfile` (backend, trains models during build), `frontend/Dockerfile` (standalone)
- [x] `docker-compose.yml`, `.dockerignore` × 2, `output: "standalone"` verified building
- [x] `docs/deployment.md` — ECS runbook, nginx config, and the five things that will bite
- [ ] **Confirm requirements from the official rulebook** — needs you
- [ ] **Build the images and deploy** — needs you; Docker is not installed here, so the
      container definitions are written but have never been built or run

**Submission package**
- [x] `docs/architecture.svg` — offline/runtime lanes, renders cleanly to PNG or PDF
- [x] `docs/project-description.md` — the written submission
- [x] `docs/deck.pptx` + `docs/deck.pdf` — 8 slides, generated from one source by
      `docs/build_deck.py`, so the two formats cannot drift apart
- [x] `README.md` — setup someone else could actually follow
- [ ] **Demo video** — needs you; `docs/demo-script.md` is the shot list

**Verification run at the end of this phase:** 33 end-to-end API checks passing (every
endpoint, both demo claims, six error paths, feedback persistence), 20 pytest tests, `tsc`
clean, production build clean, both demo claims driven through the real UI in a browser.

**Problems found and fixed during Phase 7:**
- **Network failures showed the raw browser string "Failed to fetch"**, which tells a user
  nothing. `lib/api.ts` now catches the network-level failure and names the service and URL
  it could not reach.
- The architecture diagram's "loaded once" arrow crossed an annotation label; rerouted.

**Verified working in Phase 7 that had never been exercised:**
- The blank "Check a claim" path (as opposed to the scripted demos)
- Number-input questions — typing 75 into "days since treatment" recorded "75 days" and
  correctly produced CARC 29, since 75 days exceeds every insurer's filing limit
- The "This prediction is wrong" button, confirmed writing rows to SQLite
- Light mode, which the development machine's dark-mode extension had been masking
- Backend-down behaviour: the UI degrades to a clear message rather than a blank screen

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
