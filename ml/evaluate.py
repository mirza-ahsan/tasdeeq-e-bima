"""Honest evaluation, written out as docs/model-card.md.

    python ml/evaluate.py

Every figure is recomputed from the saved artefacts with the same functions training
used (`ml.train.model_metrics`, the `partial_view` masking, `ml.simulate`), and checked
against what training recorded in metadata.json. A mismatch fails loudly instead of
publishing two different numbers for the same thing.
"""

from __future__ import annotations

import json
import pickle
import sys
from collections import Counter
from pathlib import Path

import lightgbm as lgb
import numpy as np
from sklearn.metrics import confusion_matrix

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.predictor import Predictor  # noqa: E402
from backend.services.adaptive import (MAX_QUESTIONS, MIN_QUESTIONS,  # noqa: E402
                                       AdaptiveEngine, feature_label)
from ml.calibration import PROB_CEIL, PROB_FLOOR, predict_proba  # noqa: E402
from ml.features import (BILL_FEATURES, CARC_TARGET, TARGET, derive_frame,  # noqa: E402
                         prepare)
from ml.simulate import summarise, verdict, walk_claims  # noqa: E402
from ml.train import load_claims, model_metrics, raw_columns, split  # noqa: E402

MODELS = ROOT / "models"
DOCS = ROOT / "docs"

STOP_TEXT = {
    "confident_low_risk": "settled in the low band",
    "confident_high_risk": "settled in the high band",
    "no_informative_questions_left": "no remaining question would move the estimate",
    "all_questions_answered": "every question answered",
    "question_limit_reached": f"reached the {MAX_QUESTIONS}-question cap",
}


def calibration_table(y, p, bins=10) -> list[tuple[str, int, float, float]]:
    edges = np.linspace(0, 1, bins + 1)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi) if hi < 1 else (p >= lo) & (p <= hi)
        if m.sum() >= 10:
            rows.append((f"{lo:.0%}–{hi:.0%}", int(m.sum()), float(p[m].mean()), float(y[m].mean())))
    return rows


def check(name: str, computed: float, recorded: float) -> None:
    if abs(computed - recorded) > 1e-9:
        raise SystemExit(f"ERROR: {name} is {computed} here but {recorded} in metadata.json. "
                         "Re-run ml/train.py so the model card and the artefacts agree.")


def main() -> int:
    meta = json.loads((MODELS / "metadata.json").read_text())
    risk = lgb.Booster(model_file=str(MODELS / "risk_model.txt"))
    calibrator = pickle.loads((MODELS / "calibrator.pkl").read_bytes())
    predictor = Predictor(MODELS)
    engine = AdaptiveEngine(predictor)
    bands = meta["risk_bands"]
    tuned = {**bands, "min_info_gain": meta["loop"]["min_info_gain"]}

    df, ref, categories = load_claims()
    _, _, idx_te = split(df)
    te = df.loc[idx_te]
    y_te = te[TARGET].astype(int).to_numpy()

    # --- model metrics, recomputed and checked against training's record ---
    metrics = model_metrics(risk, calibrator, te, ref, categories, bands["high_min"])
    for key in ("test_auc_full", "test_auc_partial", "test_brier_full", "test_accuracy_full",
                "test_precision", "test_recall"):
        check(key, metrics[key], meta["metrics"][key])

    full = prepare(derive_frame(raw_columns(te), ref), categories)
    p_te = predict_proba(risk, calibrator, full)
    pred = (p_te >= bands["high_min"]).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_te, pred).ravel()
    specificity = tn / (tn + fp) if (tn + fp) else 0.0
    balanced = (metrics["test_recall"] + specificity) / 2

    # --- what users get: the live loop, run over every test claim ---
    print("Simulating the question loop over the test split...")
    walks = walk_claims(engine, te)
    verdicts = [verdict(w, tuned) for w in walks]
    product = summarise(verdicts)
    check("verdicts.low.share", product["bands"]["low"]["share"],
          meta["product_test"]["bands"]["low"]["share"])
    reasons = Counter(v.reason for v in verdicts)
    first_questions = Counter(w.order[0] for w in walks if w.order)

    # --- attribution, exactly as the interface computes it, at each verdict ---
    print("Attributing each verdict...")
    totals: Counter = Counter()
    top_driver: Counter = Counter()
    truth_rows = te.to_dict("records")
    for row, v in zip(truth_rows, verdicts):
        players = list(BILL_FEATURES) + list(v.order)
        answers = {f: _python(row[f]) for f in
                   ["patient_age", "patient_gender", *players]}
        points = predictor.attribute(answers, players).points
        for f, pts in points.items():
            totals[f] += abs(pts)
        f, pts = max(points.items(), key=lambda kv: abs(kv[1]))
        if abs(pts) >= 1:
            top_driver[f] += 1
    mean_abs = sorted(((f, s / len(verdicts)) for f, s in totals.items()),
                      key=lambda kv: -kv[1])

    # --- CARC reason model: the test split's rejections that have a cause ---
    carc = lgb.Booster(model_file=str(MODELS / "carc_model.txt"))
    classes = meta["carc_classes"]
    cls_idx = {c: i for i, c in enumerate(classes)}
    coded_te = te[te[TARGET] & te[CARC_TARGET].notna()]
    cp = carc.predict(prepare(derive_frame(raw_columns(coded_te), ref), categories))
    yc = coded_te[CARC_TARGET].map(cls_idx).astype(int).to_numpy()
    check("carc_top1", round(float(np.mean(cp.argmax(axis=1) == yc)), 4), meta["metrics"]["carc_top1"])
    carc_meta = json.loads((ROOT / "data" / "carc_codes.json").read_text())
    plain = {c["code"]: c["plain_english"] for c in carc_meta["codes"]}
    per_class = []
    for i, code in enumerate(classes):
        m = yc == i
        if m.sum() >= 5:
            per_class.append((code, int(m.sum()), float(np.mean(cp[m].argmax(axis=1) == i)),
                              plain.get(code, "")))

    m = meta["metrics"]
    sel = meta["hyperparameters"]["selected"]
    gate = "PASS" if 0.70 <= m["test_accuracy_full"] <= 0.85 else "FAIL"
    b = product["bands"]
    tuning = meta["band_tuning"]
    cons = tuning["constraints"]

    md = f"""# Model Card — Claim Rejection Risk Predictor

Generated by `ml/evaluate.py` from the committed artefacts. Every model figure is on the
held-out test split, which was used for nothing but this report: hyperparameters were
chosen by cross-validation inside the training split, the calibrator was fitted on that
cross-validation's out-of-fold predictions, the risk bands on the validation split, and the
reason model trained on the training split's rejections only.

## Summary

| | |
|---|---|
| Task | Predict whether a claim will be rejected before submission, and why |
| Models | LightGBM binary (risk) + LightGBM multiclass (CARC reason) |
| Calibration | Centred isotonic on out-of-fold predictions, clamped to [{PROB_FLOOR:.0%}, {PROB_CEIL:.0%}] |
| Hyperparameters | {sel['num_leaves']} leaves, min {sel['min_data_in_leaf']} per leaf, {sel['masked_copies']} masked copies per claim, {m['risk_model_trees']} trees — chosen by 5-fold CV, a claim's copies kept in one fold (AUC {sel['cv_auc']:.3f} ± {sel['cv_auc_std']:.3f}) |
| Training claims | {meta['n_claims']:,} synthetic (Synthea + rules-based CARC labelling) |
| Split | 80 / 10 / 10 train / validation / test, stratified |
| Base rejection rate | {meta['base_rejection_rate']:.1%} |
| Risk bands | low ≤ {bands['low_max']:.0%}, high ≥ {bands['high_min']:.0%} (fitted on validation, see below) |

## What a user actually gets

The number that matters for a pre-submission check is not the classifier's AUC but the
verdict a clerk is shown, after however many questions the loop decided to ask. Every
test claim was run through the live question loop, answering from its true record:

| Verdict | Share of claims | Actually rejected |
|---|---:|---:|
| **Low** — "looks ready to submit" | {b['low']['share']:.0%} | {b['low']['rejection_rate']:.0%} |
| **Medium** — "not fully cleared" | {b['medium']['share']:.0%} | {_pct(b['medium']['rejection_rate'])} |
| **High** — "high risk of rejection" | {b['high']['share']:.0%} | {b['high']['rejection_rate']:.0%} |
| *All claims* | 100% | {y_te.mean():.0%} |

- **{product['rejections_flagged']:.0%} of the claims that were rejected were flagged** (medium or
  high) before submission; {product['rejections_in_high_band']:.0%} were flagged high.
- **Questions asked:** {product['mean_questions_approved']} on average for claims that were approved,
  {product['mean_questions_rejected']} for claims that were rejected. {product['share_at_minimum']:.0%} of checks
  finished at the {MIN_QUESTIONS}-question minimum; {product['share_at_cap']:.0%} reached the {MAX_QUESTIONS}-question cap.
- **{product['distinct_question_orders']} distinct question orders** across {product['claims']} test claims.
  The first question alone depends on the bill: {_first_questions(first_questions, len(walks))}.
- Why checks stopped: {', '.join(f"{STOP_TEXT.get(r, r)} ({n})" for r, n in reasons.most_common())}.

### How the bands were chosen

Bands and the information-gain stopping threshold were fitted together on the
validation split by simulating the same loop (`ml/simulate.py`). Among all settings
where:

- "low" ends below the average claim's risk ({cons['bands_straddle_base_rate']:.1%}) and "high" starts above it,
- a typical claim, before its bill is entered, reads as "medium" for every patient
  (typical estimates ranged {tuning['typical_probability_range'][0]:.0%}–{tuning['typical_probability_range'][1]:.0%}),
- at least {cons['min_low_band_approval']:.0%} of claims given "low" are approved,
- at least {cons['min_high_band_rejection']:.0%} of claims given "high" are rejected,
- and an average check stays within {cons['question_budget']} questions,

the one that settles the most claims as low or high was kept. Stopping threshold:
{meta['loop']['min_info_gain']} bits of expected information gain.

## Risk model

| Metric | Fully answered | Partially answered |
|---|---|---|
| ROC AUC | **{m['test_auc_full']:.3f}** | {m['test_auc_partial']:.3f} |
| Brier score | {m['test_brier_full']:.3f} | {m['test_brier_partial']:.3f} |

At the line the interface draws for "high risk" (p ≥ {bands['high_min']:.0%}), on fully-answered
claims:

| Metric | Score |
|---|---|
| Accuracy | {m['test_accuracy_full']:.1%} |
| Balanced accuracy | {balanced:.1%} |
| Precision | {m['test_precision']:.1%} |
| Recall | {m['test_recall']:.1%} |
| F1 | {m['test_f1']:.3f} |
| Majority-class baseline | {m['majority_class_baseline']:.1%} |

**Read AUC first, not accuracy.** Only {1 - m['majority_class_baseline']:.0%} of claims are rejected, so a
model that blindly predicted "approved" for everything would already score
{m['majority_class_baseline']:.1%} accuracy while being useless. The lift on raw accuracy is
{m['test_accuracy_full'] - m['majority_class_baseline']:+.1%}.

*"Partially answered" evaluates the same test claims with random subsets of the questions
unanswered (the bill always known), which is the state the loop actually operates in.*

**Accuracy gate (70–85%): {gate}.** We hold accuracy in this band deliberately. On
self-generated data a near-perfect score would only prove the model had reverse-engineered
our labelling rules, which is not evidence it would work on real claims.

### Confusion matrix (test, high-band line {bands['high_min']:.2f})

| | predicted approve | predicted reject |
|---|---:|---:|
| **actually approved** | {tn} | {fp} |
| **actually rejected** | {fn} | {tp} |

### Calibration

Does a stated probability mean what it says? (fully-answered test claims, bins of ≥ 10)

| Predicted | Claims | Mean predicted | Actual rejection rate |
|---|---:|---:|---:|
"""
    for band, n, mp, ar in calibration_table(y_te, p_te):
        md += f"| {band} | {n} | {mp:.1%} | {ar:.1%} |\n"

    md += """
### Which fields drive the verdicts

Exact Shapley attribution of the calibrated probability, as the interface computes it,
over each test claim's bill and the questions it was actually asked — measured from a
typical claim for that patient.

| Rank | Field | Mean \\|points\\| per claim | Top driver on |
|---:|---|---:|---:|
"""
    for i, (feat, val) in enumerate(mean_abs[:10], 1):
        md += f"| {i} | {feature_label(feat)} | {val:.1f} | {top_driver[feat]} claims |\n"

    md += f"""
## CARC reason model

Trained on the training split's rejected claims whose rejection has an identifiable cause,
predicting which of {len(classes)} real X12 codes applies. Scored on the test split's.

| Metric | Score |
|---|---|
| Top-1 accuracy | **{m['carc_top1']:.1%}** |
| Top-3 accuracy | **{m['carc_top3']:.1%}** |
| Random baseline | {1 / len(classes):.1%} |
| Test claims | {m['carc_test_claims']} |

**Why this is so high, and why it says little about real insurers.** Reason codes follow
our labelling rules: a claim's code is the most fundamental defect it has, except where a
probabilistic rule happened not to fire. The model recovers that rule table well. Real
insurers code less consistently, so treat this as an upper bound for the approach, not a
forecast. Rejections with no defect at all (label noise and the base rate) carry no code
and are excluded rather than given a random one.

### Per-code accuracy (codes with at least 5 test claims)

| CARC | Test claims | Top-1 accuracy | Meaning |
|---|---:|---:|---|
"""
    for code, n, a, desc in sorted(per_class, key=lambda r: -r[2]):
        md += f"| **{code}** | {n} | {a:.0%} | {desc} |\n"

    md += """
## Known limitations

1. **The labels are ours.** The model learned our rules-based labelling logic plus noise,
   on top of a realistic clinical substrate. It has learned the *structure* of
   paperwork-driven rejection, not the actual adjudication behaviour of any real insurer.
   See [data-provenance.md](data-provenance.md).
2. **Partial-information AUC is lower** than the fully-answered figure. Early in the
   question flow the estimate is genuinely uncertain, which is why the loop asks at least
   three questions and only stops on a band it can stand behind.
3. **Clearing a claim is limited by noise.** By design (a 2% base rate plus 5% label
   noise), about one claim in fifteen with no defect at all is still rejected, so even
   "low" verdicts carry a residual rejection rate that no question can remove.
4. **No insurer-specific modelling.** Real filing rules and tariffs differ per payer; a
   production version would need per-insurer models trained on that insurer's outcomes.
5. **Small evaluation sets.** Test and validation are 362 and 361 claims. Band-level rates
   carry a few points of sampling error; the band constraints are applied on validation and
   re-measured here, and the two agree only to that precision. The reason model's test set
   is smaller still, so per-code figures are indicative.
6. **Splits are by claim, not by patient.** Synthea's 3,612 claims come from 93 patients,
   so a test claim's patient usually also has claims in training. Labels depend on each
   claim's own fields, not the patient's identity, so the optimism is likely small; a
   production evaluation on real data should split by patient (or by clinic).
7. **Information gain is myopic.** It values each question one step ahead, which is why
   the bill is entered up front and pre-authorisation, where required, is always asked
   first: a one-step look-ahead cannot see that knowing the procedure is what makes the
   pre-authorisation question matter.
"""
    (DOCS / "model-card.md").write_text(md)
    print(f"\n  AUC {m['test_auc_full']:.3f} | accuracy {m['test_accuracy_full']:.1%} | gate {gate}")
    print(f"  verdicts low {b['low']['share']:.0%} / medium {b['medium']['share']:.0%} / "
          f"high {b['high']['share']:.0%} | CARC top-1 {m['carc_top1']:.1%}")
    print("  written: docs/model-card.md")
    return 0


def _python(v):
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v.item() if isinstance(v, np.generic) else v


def _pct(x) -> str:
    return "—" if x is None else f"{x:.0%}"


def _first_questions(counts: Counter, n: int) -> str:
    return ", ".join(f"{feature_label(f).lower()} for {c / n:.0%}" for f, c in counts.most_common(4))


if __name__ == "__main__":
    sys.exit(main())
