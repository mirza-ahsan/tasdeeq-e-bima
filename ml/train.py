"""Train and calibrate the risk model and the CARC reason model, then fit the bands.

    python ml/train.py

The critical detail is MASKING AUGMENTATION. The adaptive question loop asks one
question at a time, so at prediction time most fields are still unknown. A model
trained only on complete rows behaves erratically on half-empty ones. We therefore
train on copies of each claim with random subsets of the askable fields masked out,
covering the whole trajectory from "nothing answered" to "everything answered". Derived
facts are recomputed after masking with `ml.features.derive_frame`, exactly as the API
computes them, so a masked copy is a state the live loop can actually reach.

Each split has one job, and the test split is used for nothing but the final report:

  * train (80%)       — fits the models; hyperparameters chosen by 5-fold CV inside it
                        (each claim's masked copies stay in one fold), and the
                        calibrator fitted on that CV's out-of-fold predictions
  * validation (10%)  — the risk bands, chosen by simulating the real question loop
                        (ml/simulate.py)
  * test (10%)        — reported once, both as model metrics and as the verdicts users
                        would actually have been given

Input : data/processed/claims.parquet
Output: models/risk_model.txt, models/carc_model.txt, models/calibrator.pkl,
        models/metadata.json
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import (accuracy_score, brier_score_loss,
                             precision_recall_fscore_support, roc_auc_score)
from sklearn.model_selection import StratifiedKFold, train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.calibration import PROB_CEIL, PROB_FLOOR, build_calibrator, predict_proba  # noqa: E402
from ml.features import (  # noqa: E402
    ASKABLE_FEATURES, BILL_FEATURES, BOOLEAN_FEATURES, CARC_TARGET, CATEGORICAL_FEATURES,
    CONTEXT_FEATURES, DERIVED_DEPENDENCIES, FEATURES, TARGET, build_reference,
    derive_frame, prepare,
)

PROC = ROOT / "data" / "processed"
MODELS = ROOT / "models"

SEED = 42
PARTIAL_EVAL_SEED = 7     # one fixed masking of an evaluation set, shared with evaluate.py
PARTIAL_EVAL_COPIES = 3
N_QUANTILE_BINS = 8       # candidate values per numeric feature, for information gain
CV_FOLDS = 5

# Searched by grouped cross-validation on the training split only.
TREE_GRID = [
    {"num_leaves": 7, "min_data_in_leaf": 20},
    {"num_leaves": 15, "min_data_in_leaf": 20},
    {"num_leaves": 15, "min_data_in_leaf": 50},
    {"num_leaves": 31, "min_data_in_leaf": 50},
]
COPIES_GRID = [2, 3, 5]

BASE_PARAMS = {
    "objective": "binary", "metric": "auc", "learning_rate": 0.03,
    "feature_fraction": 0.85, "bagging_fraction": 0.85, "bagging_freq": 1,
    "lambda_l2": 1.0, "verbosity": -1, "seed": SEED,
    # Same inputs, same model, whatever the thread count.
    "deterministic": True, "force_col_wise": True,
}


def raw_columns(df: pd.DataFrame) -> pd.DataFrame:
    return df[ASKABLE_FEATURES + CONTEXT_FEATURES].copy()


# Training hides the bill as well as the answers. The live loop always knows the bill,
# but attribution compares the claim against one whose bill is not yet known (that is
# how "procedure" and "amount" get their share of the points), so the model has to have
# seen that state too.
TRAIN_MASKABLE = ASKABLE_FEATURES + BILL_FEATURES


def mask_augment(df: pd.DataFrame, y: pd.Series, rng: np.random.Generator, n_copies: int,
                 ref: dict, maskable: list[str] = TRAIN_MASKABLE
                 ) -> tuple[pd.DataFrame, pd.Series, np.ndarray]:
    """The original rows plus `n_copies` partially-masked variants of each.

    The number of masked fields is drawn uniformly from 0..len(maskable), so the model
    sees the full range of states the question loop passes through, including the
    opening state where nothing has been answered yet. Returns the claim index of every
    row as well, so cross-validation can keep a claim's copies together.
    """
    base = raw_columns(df).reset_index(drop=True)
    frames = [base]
    n_rows, n_maskable = len(base), len(maskable)
    for _ in range(n_copies):
        masked = base.copy()
        n_mask = rng.integers(0, n_maskable + 1, size=n_rows)
        order = np.argsort(rng.random((n_rows, n_maskable)), axis=1)
        hidden = order < n_mask[:, None]
        for j, feat in enumerate(maskable):
            masked[feat] = masked[feat].astype(object).mask(hidden[:, j])
        frames.append(masked)
    out = derive_frame(pd.concat(frames, ignore_index=True), ref)
    groups = np.tile(np.arange(n_rows), n_copies + 1)
    return out, pd.Series(np.tile(y.to_numpy(), n_copies + 1)), groups


def partial_view(df: pd.DataFrame, y: pd.Series, ref: dict) -> tuple[pd.DataFrame, pd.Series]:
    """One fixed masking of an evaluation set: the partially-answered states the loop
    actually operates in, so the bill is always known and only answers are hidden.
    train.py and evaluate.py both call this, so they report the same numbers."""
    view, y_view, _ = mask_augment(df, y, np.random.default_rng(PARTIAL_EVAL_SEED),
                                   PARTIAL_EVAL_COPIES, ref, maskable=ASKABLE_FEATURES)
    # The first block is the unmasked originals; keep only the masked copies.
    return view.iloc[len(df):].reset_index(drop=True), y_view.iloc[len(df):].reset_index(drop=True)


def params_for(tree: dict, y: pd.Series) -> dict:
    # Denials are the minority class. Without this the model maximises accuracy by
    # predicting "approved" for everything and its ranking of risky claims suffers.
    return {**BASE_PARAMS, **tree, "scale_pos_weight": float((1 - y.mean()) / y.mean())}


def select_hyperparameters(df: pd.DataFrame, ref: dict, categories: dict
                           ) -> tuple[dict, list, np.ndarray, np.ndarray]:
    """5-fold CV on the training split, folds formed by claim so a claim's masked copies
    never straddle one. Each fold model is scored on the held-out
    claims' full rows plus one fixed partial view, the mix the product depends on.

    Also returns the winning configuration's out-of-fold predictions. Isotonic
    calibration is fitted on those (about 11,000 rows) rather than on the 361-claim
    validation split, which left it a coarse staircase: after three answers almost every
    claim landed on one of a handful of identical values."""
    y = df[TARGET].astype(int).reset_index(drop=True)
    df = df.reset_index(drop=True)
    folds = list(StratifiedKFold(CV_FOLDS, shuffle=True, random_state=SEED).split(df, y))
    # Each fold's held-out claims, as full rows plus one fixed partial view.
    held_out, held_out_y = [], []
    for _, va in folds:
        Xp, yp = partial_view(df.iloc[va], y.iloc[va], ref)
        Xv = pd.concat([derive_frame(raw_columns(df.iloc[va]), ref), Xp], ignore_index=True)
        yv = pd.concat([y.iloc[va].reset_index(drop=True), yp], ignore_index=True)
        held_out.append(lgb.Dataset(prepare(Xv, categories), yv,
                                    categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False))
        held_out_y.append(yv.to_numpy())

    scores: dict[tuple, list[tuple[float, int]]] = {}
    oof: dict[tuple, list[np.ndarray]] = {}
    for copies in COPIES_GRID:
        for k, (tr, _) in enumerate(folds):
            Xa, ya, _ = mask_augment(df.iloc[tr], y.iloc[tr],
                                     np.random.default_rng([SEED, copies, k]), copies, ref)
            train_set = lgb.Dataset(prepare(Xa, categories), ya,
                                    categorical_feature=CATEGORICAL_FEATURES, free_raw_data=False)
            for t, tree in enumerate(TREE_GRID):
                booster = lgb.train(params_for(tree, ya), train_set, num_boost_round=2000,
                                    valid_sets=[held_out[k]],
                                    callbacks=[lgb.early_stopping(50, verbose=False)])
                scores.setdefault((copies, t), []).append(
                    (booster.best_score["valid_0"]["auc"], booster.best_iteration))
                oof.setdefault((copies, t), []).append(
                    booster.predict(held_out[k].data, num_iteration=booster.best_iteration))

    results = []
    for (copies, t), runs in scores.items():
        aucs = [a for a, _ in runs]
        rounds = [r for _, r in runs]
        tree = TREE_GRID[t]
        results.append({"masked_copies": copies, **tree,
                        "cv_auc": round(float(np.mean(aucs)), 4),
                        "cv_auc_std": round(float(np.std(aucs)), 4),
                        "rounds": int(round(np.mean(rounds)))})
        print(f"    copies={copies} leaves={tree['num_leaves']:>2} "
              f"min_leaf={tree['min_data_in_leaf']:>2}  AUC {np.mean(aucs):.4f} "
              f"± {np.std(aucs):.4f}  rounds {np.mean(rounds):.0f}")
    best_i = max(range(len(results)), key=lambda i: results[i]["cv_auc"])
    best_key = list(scores)[best_i]
    return (results[best_i], results, np.concatenate(oof[best_key]),
            np.concatenate(held_out_y))


def _weighted_reps(values: pd.Series) -> dict:
    """Quantile representatives of a numeric field, with duplicate quantiles merged so
    each value carries the probability mass it actually stands for."""
    qs = np.linspace(0.5 / N_QUANTILE_BINS, 1 - 0.5 / N_QUANTILE_BINS, N_QUANTILE_BINS)
    reps = np.quantile(values, qs, method="nearest")
    uniq, counts = np.unique(reps, return_counts=True)
    return {"values": [float(v) for v in uniq],
            "probs": [round(c / N_QUANTILE_BINS, 5) for c in counts]}


def value_priors(df: pd.DataFrame) -> dict:
    """Candidate values and their frequencies, consumed by the information-gain step."""
    priors: dict[str, dict] = {}
    for feat in ASKABLE_FEATURES:
        s = df[feat].dropna()
        if feat == "pre_auth_obtained":
            # Only ever asked once pre-authorisation is known to have been required.
            s = df.loc[df.pre_auth_required_for_procedure == 1, feat]
        if feat in CATEGORICAL_FEATURES:
            vc = s.astype(str).value_counts(normalize=True)
            ordered = sorted(vc.items(), key=lambda kv: (-kv[1], kv[0]))
            priors[feat] = {"kind": "categorical",
                            "values": [k for k, _ in ordered],
                            "probs": [round(float(v), 5) for _, v in ordered]}
        elif feat in BOOLEAN_FEATURES:
            p_true = float(s.astype(bool).mean())
            priors[feat] = {"kind": "boolean", "values": [True, False],
                            "probs": [round(p_true, 5), round(1 - p_true, 5)]}
        else:
            priors[feat] = {"kind": "numeric", **_weighted_reps(s)}
    return priors


def train_carc(df: pd.DataFrame, idx_tr: pd.Index, idx_val: pd.Index, idx_te: pd.Index,
               ref: dict, categories: dict, rng: np.random.Generator):
    """Reason model, trained only on rejected claims whose rejection has a cause.

    It uses the same split as the risk model: trained on the training split's coded
    rejections, stopped early on the validation split's, scored on the test split's. No
    test claim is ever in its training data.
    """
    coded = df[df[TARGET] & df[CARC_TARGET].notna()]
    classes = sorted(coded[CARC_TARGET].unique(), key=int)
    cls_idx = {c: i for i, c in enumerate(classes)}
    y = coded[CARC_TARGET].map(cls_idx).astype(int)
    tr = coded.index.intersection(idx_tr)
    stop = coded.index.intersection(idx_val)
    te = coded.index.intersection(idx_te)

    Xf, yf, _ = mask_augment(coded.loc[tr], y.loc[tr], rng, 3, ref)
    Xs, ys = partial_view(coded.loc[stop], y.loc[stop], ref)
    Xs = pd.concat([derive_frame(raw_columns(coded.loc[stop]), ref), Xs], ignore_index=True)
    ys = pd.concat([y.loc[stop].reset_index(drop=True), ys], ignore_index=True)

    carc = lgb.train(
        {"objective": "multiclass", "num_class": len(classes), "metric": "multi_logloss",
         "learning_rate": 0.05, "num_leaves": 15, "min_data_in_leaf": 20,
         "feature_fraction": 0.85, "lambda_l2": 1.0, "verbosity": -1, "seed": SEED,
         "deterministic": True, "force_col_wise": True},
        lgb.Dataset(prepare(Xf, categories), yf, categorical_feature=CATEGORICAL_FEATURES),
        num_boost_round=1000,
        valid_sets=[lgb.Dataset(prepare(Xs, categories), ys,
                                categorical_feature=CATEGORICAL_FEATURES)],
        callbacks=[lgb.early_stopping(50, verbose=False)])

    probs = carc.predict(prepare(derive_frame(raw_columns(coded.loc[te]), ref), categories))
    yt = y.loc[te].to_numpy()
    top1 = float(np.mean(probs.argmax(axis=1) == yt))
    top3 = float(np.mean([yt[i] in np.argsort(r)[-3:] for i, r in enumerate(probs)]))
    return carc, classes, {"carc_top1": round(top1, 4), "carc_top3": round(top3, 4),
                           "carc_test_claims": int(len(te)),
                           "carc_training_claims": int(len(tr)),
                           "carc_model_trees": int(carc.best_iteration)}


def model_metrics(risk, calibrator, df_te: pd.DataFrame, ref: dict, categories: dict,
                  high_min: float) -> dict:
    y = df_te[TARGET].astype(int)
    full = prepare(derive_frame(raw_columns(df_te), ref), categories)
    Xp, yp = partial_view(df_te, y, ref)
    p_full = predict_proba(risk, calibrator, full)
    p_part = predict_proba(risk, calibrator, prepare(Xp, categories))
    pred = (p_full >= high_min).astype(int)
    prec, rec, f1, _ = precision_recall_fscore_support(y, pred, average="binary",
                                                       zero_division=0)
    acc = accuracy_score(y, pred)
    majority = float(max(y.mean(), 1 - y.mean()))
    return {
        "test_auc_full": round(float(roc_auc_score(y, p_full)), 4),
        "test_auc_partial": round(float(roc_auc_score(yp, p_part)), 4),
        "test_brier_full": round(float(brier_score_loss(y, p_full)), 4),
        "test_brier_partial": round(float(brier_score_loss(yp, p_part)), 4),
        # Binary figures at the line the interface actually draws: "high risk" from
        # high_min. There is no separate tuned threshold any more.
        "operating_point": high_min,
        "test_accuracy_full": round(float(acc), 4),
        "test_precision": round(float(prec), 4),
        "test_recall": round(float(rec), 4),
        "test_f1": round(float(f1), 4),
        "majority_class_baseline": round(majority, 4),
        "test_claims": int(len(df_te)),
    }


def split(df: pd.DataFrame) -> tuple[pd.Index, pd.Index, pd.Index]:
    y = df[TARGET].astype(int)
    idx_tr, idx_tmp = train_test_split(df.index, test_size=0.2, stratify=y, random_state=SEED)
    idx_val, idx_te = train_test_split(idx_tmp, test_size=0.5, stratify=y.loc[idx_tmp],
                                       random_state=SEED)
    return idx_tr, idx_val, idx_te


def load_claims() -> tuple[pd.DataFrame, dict, dict]:
    df = pd.read_parquet(PROC / "claims.parquet")
    limits = df.groupby("insurer_tpa").insurer_filing_limit_days.first().to_dict()
    ref = build_reference(df, limits)
    categories = {c: sorted(df[c].dropna().astype(str).unique()) for c in CATEGORICAL_FEATURES}
    return df, ref, categories


def main() -> int:
    if not (PROC / "claims.parquet").exists():
        print("ERROR: data/processed/claims.parquet not found. Run ml/build_dataset.py "
              "then ml/label_carc.py.", file=sys.stderr)
        return 1
    rng = np.random.default_rng(SEED)
    df, ref, categories = load_claims()
    print(f"Loaded {len(df)} claims | rejection rate {df[TARGET].mean():.1%}\n")

    idx_tr, idx_val, idx_te = split(df)
    print(f"split: train {len(idx_tr)} | val {len(idx_val)} | test {len(idx_te)}")

    # ---------------- risk model ----------------
    print("\n  hyperparameters (5-fold CV on train, a claim's copies kept together):")
    best, cv_results, oof_raw, oof_y = select_hyperparameters(df.loc[idx_tr], ref, categories)
    tree = {k: best[k] for k in ("num_leaves", "min_data_in_leaf")}
    print(f"  selected: copies={best['masked_copies']} {tree} rounds={best['rounds']}")

    y_tr = df.loc[idx_tr, TARGET].astype(int)
    Xa, ya, _ = mask_augment(df.loc[idx_tr], y_tr, rng, best["masked_copies"], ref)
    risk = lgb.train(params_for(tree, ya),
                     lgb.Dataset(prepare(Xa, categories), ya,
                                 categorical_feature=CATEGORICAL_FEATURES),
                     num_boost_round=best["rounds"])

    # Calibrate across the partially-answered states the loop produces, so the displayed
    # percentage is meaningful mid-conversation, not only at the end.
    calibrator = build_calibrator().fit(oof_raw, oof_y)

    # ---------------- CARC reason model ----------------
    carc, classes, carc_metrics = train_carc(df, idx_tr, idx_val, idx_te, ref, categories, rng)
    print(f"\n  CARC model ({len(classes)} classes, {carc_metrics['carc_training_claims']} "
          f"coded rejections): top-1 {carc_metrics['carc_top1']:.1%}  "
          f"top-3 {carc_metrics['carc_top3']:.1%}  (random {1 / len(classes):.1%})")

    # ---------------- persist, then fit the bands on the real loop ----------------
    MODELS.mkdir(exist_ok=True)
    risk.save_model(str(MODELS / "risk_model.txt"))
    carc.save_model(str(MODELS / "carc_model.txt"))
    (MODELS / "calibrator.pkl").write_bytes(pickle.dumps(calibrator))

    metadata = {
        "seed": SEED,
        "n_claims": int(len(df)),
        "base_rejection_rate": round(float(df[TARGET].mean()), 5),
        "features": FEATURES,
        "askable_features": ASKABLE_FEATURES,
        "context_features": CONTEXT_FEATURES,
        "derived_dependencies": DERIVED_DEPENDENCIES,
        "categorical_features": CATEGORICAL_FEATURES,
        "boolean_features": BOOLEAN_FEATURES,
        "categories": categories,
        "value_priors": value_priors(df.loc[idx_tr]),
        "carc_classes": classes,
        "reference": ref,
        "risk_bands": {"low_max": 0.20, "high_min": 0.40},   # provisional, replaced below
        "prob_floor": PROB_FLOOR,
        "prob_ceil": PROB_CEIL,
        "hyperparameters": {"selected": best, "cv_grid": cv_results},
    }
    (MODELS / "metadata.json").write_text(json.dumps(metadata, indent=2))

    # Imported here: the simulation drives the real engine, which loads these artefacts.
    from backend.predictor import Predictor
    from backend.services.adaptive import MAX_QUESTIONS, MIN_QUESTIONS, AdaptiveEngine
    from ml.simulate import summarise, tune_bands, verdict, walk_claims

    engine = AdaptiveEngine(Predictor(MODELS))
    print("\n  fitting risk bands by simulating the question loop on validation...")
    tuned, band_report = tune_bands(walk_claims(engine, df.loc[idx_val]),
                                    base_rate=float(df.loc[idx_tr, TARGET].mean()))
    bands = {k: tuned[k] for k in ("low_max", "high_min")}
    engine.p.bands = bands
    engine.min_gain = tuned["min_info_gain"]
    print(f"  bands: low <= {bands['low_max']:.2f}, high >= {bands['high_min']:.2f}; "
          f"stop below {tuned['min_info_gain']} bits")

    test_walks = walk_claims(engine, df.loc[idx_te])
    product = summarise([verdict(w, tuned) for w in test_walks])
    metrics = {**model_metrics(risk, calibrator, df.loc[idx_te], ref, categories,
                               bands["high_min"]),
               **carc_metrics, "risk_model_trees": int(best["rounds"])}

    metadata["risk_bands"] = bands
    metadata["loop"] = {"min_questions": MIN_QUESTIONS, "max_questions": MAX_QUESTIONS,
                        "min_info_gain": tuned["min_info_gain"]}
    metadata["band_tuning"] = band_report
    metadata["metrics"] = metrics
    metadata["product_test"] = product
    (MODELS / "metadata.json").write_text(json.dumps(metadata, indent=2))

    gate = "PASS" if 0.70 <= metrics["test_accuracy_full"] <= 0.85 else "OUTSIDE 70-85% GATE"
    print(f"\n  TEST  AUC full {metrics['test_auc_full']:.3f}  partial "
          f"{metrics['test_auc_partial']:.3f}  Brier {metrics['test_brier_full']:.3f}")
    print(f"        at high band (p >= {bands['high_min']:.2f}): accuracy "
          f"{metrics['test_accuracy_full']:.1%}  precision {metrics['test_precision']:.1%}  "
          f"recall {metrics['test_recall']:.1%}  (majority {metrics['majority_class_baseline']:.1%})")
    print(f"        accuracy gate: {gate}")
    b = product["bands"]
    print(f"  VERDICTS on test: low {b['low']['share']:.0%} (rejected {b['low']['rejection_rate']:.0%}) | "
          f"medium {b['medium']['share']:.0%} | high {b['high']['share']:.0%} "
          f"(rejected {b['high']['rejection_rate']:.0%})")
    print(f"        rejections flagged {product['rejections_flagged']:.0%} | questions "
          f"approved {product['mean_questions_approved']} vs rejected "
          f"{product['mean_questions_rejected']} | {product['distinct_question_orders']} "
          f"distinct orders")
    print(f"\n  saved to {MODELS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
