"""Phase 3 — train and calibrate the risk model and the CARC reason model.

    python ml/train.py

The critical detail here is MASKING AUGMENTATION. The adaptive question loop asks
one question at a time, so at prediction time most fields are still unknown. A model
trained only on complete rows behaves erratically on half-empty ones. We therefore
train on copies of each claim with random subsets of the askable fields masked out,
covering the whole trajectory from "nothing answered" to "everything answered".

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
from sklearn.metrics import accuracy_score, f1_score, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import train_test_split

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ml.calibration import (PROB_CEIL, PROB_FLOOR, build_calibrator,  # noqa: E402
                            predict_proba)
from ml.features import (  # noqa: E402
    ASKABLE_FEATURES, BOOLEAN_FEATURES, CARC_TARGET, CATEGORICAL_FEATURES,
    CONTEXT_FEATURES, DERIVED_DEPENDENCIES, DERIVED_FEATURES, FEATURES, TARGET,
)

PROC = ROOT / "data" / "processed"
MODELS = ROOT / "models"

SEED = 42
N_MASKED_COPIES = 3      # augmented partial-information copies per training claim
N_QUANTILE_BINS = 8      # candidate values per numeric feature, for information gain

# Risk band cut-points, calibrated in Phase 4 by simulating the whole question loop over
# held-out claims rather than taken straight from a quantile. The raw validation quantiles
# are stored alongside as val_probability_quantiles for reference.
#
# low_max was originally p25 (0.177), which sat *below* the model's own no-information
# prediction (0.188) — almost no claim could ever reach the low band, so the loop ran to
# the question cap on 62% of claims. high_min must stay at 0.40: dropping it to 0.30 makes
# risky claims stop as fast as clean ones and erases the adaptive behaviour entirely.
RISK_BAND_LOW_MAX = 0.20
RISK_BAND_HIGH_MIN = 0.40


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce dtypes so LightGBM handles categoricals natively and NaN means unknown."""
    X = df[FEATURES].copy()
    for c in CATEGORICAL_FEATURES:
        X[c] = X[c].astype("category")
    for c in BOOLEAN_FEATURES:
        # object dtype so a masked value can be NaN rather than coerced to False
        X[c] = X[c].astype("float64")
    return X


def mask_augment(X: pd.DataFrame, y: pd.Series, rng: np.random.Generator,
                 n_copies: int = N_MASKED_COPIES) -> tuple[pd.DataFrame, pd.Series]:
    """Return the original rows plus n_copies partially-masked variants of each.

    The number of masked fields is drawn uniformly from 0..len(askable), so the model
    sees the full range of states the question loop passes through, including the
    opening state where nothing has been answered yet.
    """
    frames, targets = [X], [y]
    n_askable = len(ASKABLE_FEATURES)

    for _ in range(n_copies):
        Xc = X.copy()
        n_rows = len(Xc)
        n_mask = rng.integers(0, n_askable + 1, size=n_rows)

        # Build a boolean mask matrix: which askable field is hidden in which row.
        order = np.argsort(rng.random((n_rows, n_askable)), axis=1)
        hidden = order < n_mask[:, None]

        for j, feat in enumerate(ASKABLE_FEATURES):
            rows = hidden[:, j]
            if rows.any():
                Xc.loc[Xc.index[rows], feat] = np.nan

        # A derived field is unknown as soon as any of its inputs is unknown.
        for feat, deps in DERIVED_DEPENDENCIES.items():
            dep_idx = [ASKABLE_FEATURES.index(d) for d in deps if d in ASKABLE_FEATURES]
            if not dep_idx:
                continue
            rows = hidden[:, dep_idx].any(axis=1)
            if rows.any():
                Xc.loc[Xc.index[rows], feat] = np.nan

        frames.append(Xc)
        targets.append(y)

    Xa = pd.concat(frames, ignore_index=True)
    for c in CATEGORICAL_FEATURES:
        Xa[c] = Xa[c].astype("category")
    return Xa, pd.concat(targets, ignore_index=True)


def value_priors(df: pd.DataFrame) -> dict:
    """Candidate values and their prior frequencies, consumed by the Phase 4 info-gain step."""
    priors: dict[str, dict] = {}
    for feat in ASKABLE_FEATURES:
        s = df[feat].dropna()
        if feat in CATEGORICAL_FEATURES:
            vc = s.value_counts(normalize=True)
            priors[feat] = {"kind": "categorical",
                            "values": [str(v) for v in vc.index],
                            "probs": [round(float(p), 5) for p in vc.values]}
        elif feat in BOOLEAN_FEATURES:
            p_true = float(s.astype(bool).mean())
            priors[feat] = {"kind": "boolean", "values": [True, False],
                            "probs": [round(p_true, 5), round(1 - p_true, 5)]}
        else:
            qs = np.linspace(0, 1, N_QUANTILE_BINS + 1)[1:-1]
            edges = np.unique(np.quantile(s, qs))
            reps = np.unique(np.quantile(s, np.linspace(0.5 / N_QUANTILE_BINS,
                                                        1 - 0.5 / N_QUANTILE_BINS,
                                                        N_QUANTILE_BINS)))
            priors[feat] = {"kind": "numeric",
                            "values": [float(round(v, 3)) for v in reps],
                            "probs": [round(1 / len(reps), 5)] * len(reps),
                            "bin_edges": [float(round(e, 3)) for e in edges]}
    return priors


def main() -> int:
    rng = np.random.default_rng(SEED)
    src = PROC / "claims.parquet"
    if not src.exists():
        print(f"ERROR: {src} not found. Run ml/build_dataset.py then ml/label_carc.py.",
              file=sys.stderr)
        return 1

    df = pd.read_parquet(src)
    print(f"Loaded {len(df)} claims | rejection rate {df[TARGET].mean():.1%}\n")

    X, y = prepare(df), df[TARGET].astype(int)

    X_tr, X_tmp, y_tr, y_tmp, idx_tr, idx_tmp = train_test_split(
        X, y, df.index, test_size=0.2, stratify=y, random_state=SEED)
    X_val, X_te, y_val, y_te, idx_val, idx_te = train_test_split(
        X_tmp, y_tmp, idx_tmp, test_size=0.5, stratify=y_tmp, random_state=SEED)
    print(f"split: train {len(X_tr)} | val {len(X_val)} | test {len(X_te)}")

    # ---------------- risk model ----------------
    X_tr_a, y_tr_a = mask_augment(X_tr, y_tr, rng)
    X_val_a, y_val_a = mask_augment(X_val, y_val, rng)
    print(f"masking augmentation: train {len(X_tr)} -> {len(X_tr_a)} rows\n")

    risk = lgb.train(
        {"objective": "binary", "metric": "auc", "learning_rate": 0.03,
         "num_leaves": 15, "min_data_in_leaf": 20, "feature_fraction": 0.85,
         "bagging_fraction": 0.85, "bagging_freq": 1, "lambda_l2": 1.0,
         # Denials are the minority class. Without this the model maximises accuracy
         # by predicting "approved" for everything and recall collapses.
         "scale_pos_weight": float((1 - y_tr.mean()) / y_tr.mean()),
         "verbosity": -1, "seed": SEED},
        lgb.Dataset(X_tr_a, y_tr_a, categorical_feature=CATEGORICAL_FEATURES),
        num_boost_round=900,
        valid_sets=[lgb.Dataset(X_val_a, y_val_a, categorical_feature=CATEGORICAL_FEATURES)],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    print(f"risk model: {risk.best_iteration} trees")

    # Calibrate across the same partial-information distribution the loop produces,
    # so the displayed percentage is meaningful mid-conversation, not only at the end.
    calibrator = build_calibrator().fit(risk.predict(X_val_a), y_val_a)

    def p(Xd):
        return predict_proba(risk, calibrator, Xd)

    # Decision threshold tuned on validation, not left at 0.5. With a minority
    # positive class, 0.5 is simply the wrong operating point.
    p_val = p(X_val_a)
    grid = np.round(np.arange(0.10, 0.71, 0.01), 2)
    threshold = float(max(grid, key=lambda t: f1_score(y_val_a, (p_val >= t).astype(int))))

    p_full = p(X_te)
    X_te_a, y_te_a = mask_augment(X_te, y_te, rng)
    p_part = p(X_te_a)

    full_auc = roc_auc_score(y_te, p_full)
    full_acc = accuracy_score(y_te, p_full >= threshold)
    part_auc = roc_auc_score(y_te_a, p_part)
    part_acc = accuracy_score(y_te_a, p_part >= threshold)
    prec, rec, f1, _ = precision_recall_fscore_support(
        y_te, (p_full >= threshold).astype(int), average="binary", zero_division=0)
    majority = float(max(y_te.mean(), 1 - y_te.mean()))

    print(f"\n  decision threshold (tuned on val): {threshold:.2f}")
    print(f"  TEST (fully answered)   accuracy {full_acc:.1%}  AUC {full_auc:.3f}")
    print(f"    precision {prec:.1%}  recall {rec:.1%}  F1 {f1:.3f}")
    print(f"  TEST (partial answers)  accuracy {part_acc:.1%}  AUC {part_auc:.3f}")
    print(f"  majority-class baseline: {majority:.1%}  (lift {full_acc - majority:+.1%})")
    gate = "PASS" if 0.70 <= full_acc <= 0.85 else "OUTSIDE 70-85% GATE"
    print(f"  accuracy gate: {gate}")

    # Operating points for the UI and for the Phase 4 stopping rule, derived from the
    # actual validation distribution rather than guessed constants.
    qs = {f"p{int(q*100)}": round(float(np.quantile(p_val, q)), 4)
          for q in (0.10, 0.25, 0.50, 0.75, 0.90, 0.95)}

    # ---------------- CARC reason model ----------------
    rej = df[df[TARGET]].copy()
    classes = sorted(rej[CARC_TARGET].unique())
    cls_idx = {c: i for i, c in enumerate(classes)}
    Xr = prepare(rej)
    yr = rej[CARC_TARGET].map(cls_idx).astype(int)

    Xr_tr, Xr_te, yr_tr, yr_te = train_test_split(
        Xr, yr, test_size=0.2, stratify=yr, random_state=SEED)
    Xr_tr_a, yr_tr_a = mask_augment(Xr_tr, yr_tr, rng)

    carc = lgb.train(
        {"objective": "multiclass", "num_class": len(classes), "metric": "multi_logloss",
         "learning_rate": 0.06, "num_leaves": 15, "min_data_in_leaf": 20,
         "feature_fraction": 0.85, "lambda_l2": 1.0, "verbosity": -1, "seed": SEED},
        lgb.Dataset(Xr_tr_a, yr_tr_a, categorical_feature=CATEGORICAL_FEATURES),
        num_boost_round=300,
    )
    probs = carc.predict(Xr_te)
    top1 = accuracy_score(yr_te, probs.argmax(axis=1))
    top3 = np.mean([yr_te.iloc[i] in np.argsort(r)[-3:] for i, r in enumerate(probs)])
    print(f"\n  CARC model ({len(classes)} classes, {len(rej)} rejected claims)")
    print(f"    top-1 {top1:.1%}   top-3 {top3:.1%}   (random baseline {1/len(classes):.1%})")

    # ---------------- persist ----------------
    MODELS.mkdir(exist_ok=True)
    risk.save_model(str(MODELS / "risk_model.txt"))
    carc.save_model(str(MODELS / "carc_model.txt"))
    (MODELS / "calibrator.pkl").write_bytes(pickle.dumps(calibrator))

    # Reference values the API needs to compute derived features from raw answers.
    proc_medians = df.groupby("procedure_category", observed=True).claim_amount_pkr.median()
    filing_limits = df.groupby("insurer_tpa", observed=True).insurer_filing_limit_days.first()

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
        "categories": {c: [str(v) for v in X[c].cat.categories] for c in CATEGORICAL_FEATURES},
        "value_priors": value_priors(df),
        "carc_classes": classes,
        "reference": {
            "procedure_median_amount_pkr": {k: float(v) for k, v in proc_medians.items()},
            "insurer_filing_limit_days": {k: int(v) for k, v in filing_limits.items()},
            "pre_auth_amount_threshold_pkr": 50000,
            "pre_auth_categories": ["minor_surgery", "oncology_therapy", "dialysis",
                                    "imaging", "nursing_hospice"],
        },
        "decision_threshold": threshold,
        "risk_bands": {"low_max": RISK_BAND_LOW_MAX, "high_min": RISK_BAND_HIGH_MIN},
        "val_probability_quantiles": qs,
        "prob_floor": PROB_FLOOR,
        "prob_ceil": PROB_CEIL,
        "metrics": {
            "test_accuracy_full": round(float(full_acc), 4),
            "test_precision": round(float(prec), 4),
            "test_recall": round(float(rec), 4),
            "test_f1": round(float(f1), 4),
            "majority_class_baseline": round(majority, 4),
            "test_auc_full": round(float(full_auc), 4),
            "test_accuracy_partial": round(float(part_acc), 4),
            "test_auc_partial": round(float(part_auc), 4),
            "carc_top1": round(float(top1), 4),
            "carc_top3": round(float(top3), 4),
            "risk_model_trees": int(risk.best_iteration),
        },
    }
    (MODELS / "metadata.json").write_text(json.dumps(metadata, indent=2))
    print(f"\n  saved to {MODELS.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
