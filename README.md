# Tasdeeq-e-Bima

**A claim rejection risk predictor for private clinics in Pakistan.**

Before a clinic submits a bill to an insurer or TPA, this tool looks at the claim and tells staff, in plain Urdu or English: how likely it is to bounce, which specific field is the problem, and what to fix. Think spell-check, but for insurance claims instead of prose.

Built for [Bano Qabil AI hackathon] by Mirza Ahsan Baig and Safee.

---

## The problem

Most claim rejections have nothing to do with whether the treatment was right. They're paperwork: no pre-authorization, a missing document, a claim filed two weeks past the deadline. Denial rates above 10% are common industry-wide, and reworking a single denied claim costs real staff hours. Pakistani private clinics usually don't have a dedicated billing specialist, so these mistakes slip through and the clinic finds out weeks later, when the money doesn't show up.

Enough of that adds up to clinics quietly raising prices, dropping insurance patients, or just eating the loss. It's a cash-flow problem dressed up as a paperwork problem.

## What it does

1. Staff starts entering a claim — procedure, diagnosis, insurer, pre-auth status, and so on.
2. Instead of one long form, the tool asks one question at a time, picking whichever question would tell it the most given what it already knows. A clean, obvious claim might only need three questions; a messy one needs more.
3. Once it's confident, it shows a rejection probability, the specific fields driving that risk, and — if the claim looks headed for rejection — which real CARC code it's likely to get hit with.
4. Qwen turns that into a sentence or two of plain-language advice, in Urdu or English, without touching the actual prediction.
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
frontend/     Next.js — one question at a time, live risk indicator, Urdu/English toggle
backend/      FastAPI — session state, the adaptive loop, Qwen calls
ml/           dataset build, feature definitions, training, calibration, evaluation
data/         the CARC code reference table
models/       trained model artifacts + metadata.json (metrics, feature priors, thresholds)
docs/         model card and data provenance, written for anyone auditing the numbers
```

- **Risk model:** LightGBM, binary classification, isotonic calibration, decision threshold tuned on the validation split.
- **Reason model:** LightGBM, multiclass, trained on rejected claims only.
- **Explanation layer:** Qwen via Alibaba Cloud DashScope, OpenAI-compatible endpoint.
- **No database, no auth.** Session state lives in memory for the process lifetime — this is a demo, not a product. The one thing that does persist to disk is the feedback log, because showing the human-in-the-loop correction path is part of the point.

## Running it

Backend:
```bash
pip install -e .
cp .env.example .env   # fill in DASHSCOPE_API_KEY
uvicorn backend.main:app --reload
```

Frontend:
```bash
cd frontend
npm install
npm run dev
```

Rebuilding the dataset and model from scratch:
```bash
python ml/build_dataset.py
python ml/label_carc.py
python ml/train.py
python ml/evaluate.py   # regenerates docs/model-card.md
```

Tests: `pytest`

## Two demo claims, on purpose

The demo walks through one claim that should come back risky, for a specific traceable reason (no pre-authorization on a Rs. 185,000 minor surgery), and one clean one — same question flow, no shortcuts — so it's obvious the tool isn't just flagging everything it sees. `GET /api/demo/risky` and `GET /api/demo/clean`.
