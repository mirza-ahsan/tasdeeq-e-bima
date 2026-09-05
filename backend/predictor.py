"""Model serving — probability, per-field attribution, and the likely CARC code.

Loads the Phase 3 artefacts once and answers questions about partially-completed
claims. Unknown fields are genuinely unknown (NaN), which the model was explicitly
trained to handle; nothing is imputed behind the user's back.
"""

from __future__ import annotations

import json
import pickle
import warnings
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.calibration import PROB_CEIL, PROB_FLOOR
from ml.features import (BOOLEAN_FEATURES, CATEGORICAL_FEATURES, DERIVED_DEPENDENCIES,
                         FEATURES)

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"


@dataclass
class FieldContribution:
    feature: str
    value: object
    shap_value: float          # log-odds contribution
    contribution_points: float # signed, in percentage points of the final probability

    @property
    def effect(self) -> str:
        return "increases_risk" if self.contribution_points > 0 else "reduces_risk"


class Predictor:
    def __init__(self, models_dir: Path = MODELS):
        self.dir = models_dir
        self.meta = json.loads((models_dir / "metadata.json").read_text())
        self.risk = lgb.Booster(model_file=str(models_dir / "risk_model.txt"))
        self.carc = lgb.Booster(model_file=str(models_dir / "carc_model.txt"))
        self.calibrator = pickle.loads((models_dir / "calibrator.pkl").read_bytes())
        self.ref = self.meta["reference"]
        self.carc_classes = self.meta["carc_classes"]
        self.threshold = self.meta["decision_threshold"]
        self.bands = self.meta["risk_bands"]

    # -- feature assembly ---------------------------------------------------

    def _derive(self, row: dict) -> dict:
        """Fill derived features, but only when every input they need is known.

        This mirrors the masking rule used in training: a derived feature is unknown
        as soon as any of its inputs is unknown. Diverging here would put the model
        in a state it never saw.
        """
        known = lambda k: row.get(k) is not None  # noqa: E731
        for feat, deps in DERIVED_DEPENDENCIES.items():
            if not all(known(d) for d in deps):
                row[feat] = None
                continue
            if feat == "insurer_filing_limit_days":
                row[feat] = self.ref["insurer_filing_limit_days"].get(row["insurer_tpa"])
            elif feat == "days_vs_insurer_filing_limit":
                limit = self.ref["insurer_filing_limit_days"].get(row["insurer_tpa"])
                row[feat] = round(row["days_since_treatment"] / limit, 3) if limit else None
            elif feat == "amount_vs_procedure_median":
                med = self.ref["procedure_median_amount_pkr"].get(row["procedure_category"])
                row[feat] = round(row["claim_amount_pkr"] / med, 3) if med else None
            elif feat == "pre_auth_required_for_procedure":
                row[feat] = (row["procedure_category"] in self.ref["pre_auth_categories"]
                             or row["claim_amount_pkr"] > self.ref["pre_auth_amount_threshold_pkr"])
        return row

    def build_frame(self, answer_sets: list[dict]) -> pd.DataFrame:
        """Assemble a model-ready frame from one or more partial answer dicts."""
        rows = [self._derive(dict(a)) for a in answer_sets]
        df = pd.DataFrame(rows).reindex(columns=FEATURES)
        for c in CATEGORICAL_FEATURES:
            df[c] = pd.Categorical(df[c], categories=self.meta["categories"][c])
        for c in FEATURES:
            if c not in CATEGORICAL_FEATURES:
                df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        return df

    # -- prediction ---------------------------------------------------------

    def predict_many(self, answer_sets: list[dict]) -> np.ndarray:
        """Calibrated, clamped rejection probabilities. Batched — the information-gain
        step needs roughly a hundred of these per question."""
        if not answer_sets:
            return np.array([])
        raw = self.risk.predict(self.build_frame(answer_sets))
        return np.clip(self.calibrator.predict(raw), PROB_FLOOR, PROB_CEIL)

    def predict(self, answers: dict) -> float:
        return float(self.predict_many([answers])[0])

    def risk_band(self, p: float) -> str:
        if p <= self.bands["low_max"]:
            return "low"
        if p >= self.bands["high_min"]:
            return "high"
        return "medium"

    # -- attribution --------------------------------------------------------

    @cached_property
    def _explainer(self):
        import shap
        warnings.filterwarnings("ignore", category=UserWarning, module="shap")
        return shap.TreeExplainer(self.risk)

    def explain(self, answers: dict, top_n: int = 4) -> list[FieldContribution]:
        """Per-claim SHAP attribution, restricted to fields the user actually answered.

        SHAP works in log-odds. We re-express each contribution in percentage points by
        allocating the gap between this claim's probability and the base rate in
        proportion to the SHAP values. That is an approximation, but it keeps the
        numbers on the same scale as the headline percentage the user sees.
        """
        frame = self.build_frame([answers])
        sv = self._explainer.shap_values(frame)
        sv = sv[1] if isinstance(sv, list) else sv
        shap_row = np.asarray(sv).reshape(len(FEATURES))

        p = float(self.predict_many([answers])[0])
        p_base = self.meta["base_rejection_rate"]
        total = float(shap_row.sum())
        delta = (p - p_base) * 100

        out = []
        for feat, s in zip(FEATURES, shap_row):
            if answers.get(feat) is None:
                continue  # never blame a field the user has not answered
            points = (s / total) * delta if abs(total) > 1e-6 else 0.0
            out.append(FieldContribution(feat, answers[feat], float(s), float(points)))

        out.sort(key=lambda c: abs(c.contribution_points), reverse=True)
        return out[:top_n]

    # -- reason code --------------------------------------------------------

    def predict_carc(self, answers: dict, top_n: int = 3) -> list[tuple[str, float]]:
        probs = self.carc.predict(self.build_frame([answers]))[0]
        order = np.argsort(probs)[::-1][:top_n]
        return [(self.carc_classes[i], float(probs[i])) for i in order]


_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    """Process-wide singleton; the models are ~6MB and load once."""
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor
