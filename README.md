# Tasdeeq-e-Bima

**A claim rejection risk predictor for private clinics in Pakistan.**

Before a clinic submits a bill to an insurer or TPA, this tool looks at the claim and tells staff, in plain English: how likely it is to bounce, which specific field is the problem, and what to fix. Think spell-check, but for insurance claims instead of prose.

Built for the Bano Qabil AI hackathon by Mirza Ahsan Baig and Safee Akmal.

---

## The problem

Most claim rejections have nothing to do with whether the treatment was right. They're paperwork: no pre-authorization, a missing document, a claim filed two weeks past the deadline. Denial rates above 10% are common industry-wide, and reworking a single denied claim costs real staff hours. Pakistani private clinics usually don't have a dedicated billing specialist, so these mistakes slip through and the clinic finds out weeks later, when the money doesn't show up.

Enough of that adds up to clinics quietly raising prices, dropping insurance patients, or just eating the loss. It's a cash-flow problem dressed up as a paperwork problem.

## What it does

1. Staff starts entering a claim — procedure, diagnosis, insurer, pre-auth status, and so on.
2. Instead of one long form, the tool asks one question at a time, picking whichever question would tell it the most given what it already knows. A clean, obvious claim might only need three questions; a messy one needs more.
3. Once it's confident, it shows a rejection probability, the specific fields driving that risk, and — if the claim looks headed for rejection — which real CARC code it's likely to get hit with.
4. Qwen turns that into a sentence or two of plain-language advice, without touching the actual prediction.
5. Staff can mark a prediction wrong. That gets logged as feedback, the seed of a real retraining loop.

## Why the data is synthetic

There's no public dataset anywhere of real clinic claims paired with real outcomes — insurers and TPAs treat that as private business information, and every commercial player in this space (Waystar, Apexon, AiClaim) trains on their own claims history, not on anything public. So we built ours, but not out of thin air:

- **[Synthea](https://github.com/synthetichealth/synthea)**, MITRE's open-source patient generator, gives us medically coherent diagnoses, procedures, and encounters — the same substrate CMS used for its own published synthetic Medicare claims set.
- **X12 CARC codes**, the real industry-standard list of reasons a claim gets denied, verified directly against X12's published list on 2026-09-05.

Synthea doesn't generate accept/reject outcomes (that's exactly the private data nobody publishes), so we wrote a rules-based labeller that assigns real CARC codes to claims based on believable defects — no pre-auth on a procedure that needs one gets tagged with the real "pre-authorization missing" code, and so on. Each rule fires probabilistically rather than as a hard trigger, with some noise mixed in, so the model has to learn actual patterns instead of memorizing our rule table. We also re-skin the US-shaped data for Pakistan: PKR amounts, invented insurer/TPA names (we're not attaching claim behavior to real Pakistani companies we have no data on), and basic/standard/premium/corporate plan tiers.

Full writeup, including exactly which Synthea tables and code systems we used: [`docs/data-provenance.md`](docs/data-provenance.md).

We're upfront about this with anyone evaluating the project: this isn't real clinic data, and we're not claiming it is. It's a demonstration that we understand the actual structure of the problem well enough to simulate it honestly.

## The two things doing the real work

**Adaptive questioning, via information gain.** For each unanswered field, the engine estimates how much asking it would reduce uncertainty about the outcome, using the trained model's own predictions across that field's candidate values. It asks whichever question moves the needle most, stops once it's confident, and caps out at 8 questions regardless. It's the same idea behind computer-adaptive tests like the GRE, and it's cheap: a gradient-boosted tree model gives you feature importances as a training byproduct, so there's no need for anything closer to reinforcement learning. Full derivation and the one subtlety we hit (marginalizing over a field's values rather than using the model's "missing value" prediction, which produced impossible negative information gains) is in [`backend/services/adaptive.py`](backend/services/adaptive.py).

**Explaining a specific reason, not a bare score.** The risk model doesn't just output a probability. A second model, trained only on claims that were labeled rejected, predicts which of 12 real CARC codes is the likely cause, and SHAP values point at which fields on this specific claim are driving the number. Qwen's only job is to turn that structured output into a sentence a clinic clerk can act on — it never generates its own diagnosis of the claim. Everything it says traces back to a number the model actually computed.

## Where we landed, honestly

- ROC AUC 0.788 on the held-out test set, with 26.5% of claims labeled rejected — so a model that always guessed "approved" would already hit 73.5% accuracy while catching nothing. We report AUC and recall (67.7%) as the numbers that matter, not raw accuracy.
- We deliberately kept accuracy in the 70–85% range rather than chasing higher. On data we labeled ourselves, a near-perfect score would only mean the model reverse-engineered our own rules, not that it would generalize to a real insurer.
- CARC reason prediction hits 68% top-1, 80% top-3, against an 8.3% random baseline across 12 classes.
- Full numbers, confusion matrix, calibration table, and per-code accuracy: [`docs/model-card.md`](docs/model-card.md).

## What we'd need to make this real

A partner clinic or TPA's actual historical claims with actual outcomes, parsed 835 remittance files so retraining runs on real insurer responses instead of our own rules, and per-insurer models, since filing rules and tariffs aren't the same across payers. The architecture already supports all three — the feedback-logging button, the retraining hook, the per-field CARC breakdown — the demo just doesn't have the data to feed them yet. We'd rather say that plainly than pretend otherwise.

## Architecture

```
frontend/     Next.js — one question at a time, live risk indicator, per-field attribution
backend/      FastAPI — session state, the adaptive loop, Qwen calls
ml/           dataset build, feature definitions, training, calibration, evaluation
data/         the CARC code reference table
models/       trained model artifacts + metadata.json (metrics, feature priors, thresholds)
scripts/      standalone DashScope connectivity check
tests/        20 tests — the adaptive engine and the API contract
docs/         model card, data provenance, demo script, deployment runbook, deck
```

- **Risk model:** LightGBM, binary classification, isotonic calibration, decision threshold tuned on the validation split.
- **Reason model:** LightGBM, multiclass, trained on rejected claims only.
- **Explanation layer:** Qwen via Alibaba Cloud DashScope, OpenAI-compatible endpoint.
- **No database, no auth.** Session state lives in memory for the process lifetime — this is a demo, not a product. The one thing that does persist to disk is the feedback log, because showing the human-in-the-loop correction path is part of the point.

## Running it

Needs Python 3.12+ and Node 20+.

**First time — data and models.** Synthea's sample bundle isn't vendored (63MB), so pull it
before building anything:

```bash
uv venv --python 3.12
uv pip install -e ".[dev]"

cp .env.example .env        # fill in DASHSCOPE_API_KEY and DASHSCOPE_BASE_URL
uv run python scripts/test_dashscope.py     # confirms Qwen end to end before you rely on it

mkdir -p data/raw
curl -L -o data/raw/synthea.zip \
  https://synthetichealth.github.io/synthea-sample-data/downloads/latest/synthea_sample_data_csv_latest.zip
unzip -q data/raw/synthea.zip -d data/raw && rm data/raw/synthea.zip

uv run python ml/build_dataset.py    # Synthea CSVs   -> claims_base.parquet
uv run python ml/label_carc.py       # rejection labels -> claims.parquet
uv run python ml/train.py            # -> models/
uv run python ml/evaluate.py         # regenerates docs/model-card.md
```

The whole pipeline takes about two minutes. `data/` and `models/` are gitignored — they're
rebuilt from the commands above, not stored.

**Then, two processes:**

```bash
uv run uvicorn backend.main:app --reload --port 8000    # API → :8000
cd frontend && npm install && npm run dev               # UI  → :3000
```

Open <http://localhost:3000>. Set `NEXT_PUBLIC_API_URL` if the API isn't on port 8000.

**Tests:** `uv run pytest -q` — 20 tests covering the adaptive engine and the API contract.
The API tests make a real Qwen call, so `.env` has to be filled in for the full suite to pass.
`cd frontend && npx tsc --noEmit` for the frontend.

**Containers:** `docker compose --env-file .env up --build` brings up both. The backend image
trains its own models during the build, so nothing needs shipping alongside it. The ECS
runbook is in [`docs/deployment.md`](docs/deployment.md) — note the images are written but
have not been built or deployed yet.

## Two demo claims, on purpose

The demo walks through one claim that should come back risky, for a specific traceable reason (no pre-authorization on a Rs. 185,000 minor surgery), and one clean one — same question flow, no shortcuts — so it's obvious the tool isn't just flagging everything it sees. `GET /api/demo/risky` and `GET /api/demo/clean`.

## What we cut, on purpose

Urdu output. The original plan had an Urdu/English toggle; we scoped it out to keep the demo
tight and the copy consistent. The Qwen layer still takes the target language as a parameter,
so it's a small change to put back rather than a rewrite — see
[`backend/services/explain_qwen.py`](backend/services/explain_qwen.py).

Also deliberately absent: auth, user accounts, a database, and live retraining. This is a
demo, and the places where a real product would need more are written down rather than
papered over.

## Where everything is documented

| | |
|---|---|
| [`docs/model-card.md`](docs/model-card.md) | Every number, confusion matrix, calibration table, per-code accuracy, known limitations |
| [`docs/data-provenance.md`](docs/data-provenance.md) | Exactly what's real (Synthea, X12) and what's ours (the labels) |
| [`docs/demo-script.md`](docs/demo-script.md) | The 3-minute walkthrough, with prepared answers to the questions judges actually ask |
| [`docs/architecture.svg`](docs/architecture.svg) | Offline pipeline and runtime path on one page |
| [`docs/deployment.md`](docs/deployment.md) | ECS runbook, nginx config, and the five things likely to bite |
| [`docs/project-description.md`](docs/project-description.md) | The written submission |
| `docs/deck.pdf` / `docs/deck.pptx` | 8 slides, both generated from `docs/build_deck.py` |
| [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) | The build log — every phase, and every problem found and fixed along the way |
