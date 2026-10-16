"""Model serving — probability, per-field attribution, and the likely CARC code.

Loads the trained artefacts once and answers questions about partially-completed
claims. Unknown fields are genuinely unknown (NaN), which the model was explicitly
trained to handle; nothing is imputed behind the user's back. Derived facts come from
`ml.features.derive_frame`, the same function training used.

Attribution is exact Shapley values of the *calibrated* probability — the number on
screen — over the fields the user answered, with "not yet known" as the reference for
each field. The model was trained on masked claims, so a field being unknown is a state
it handles natively rather than an extrapolation. Two properties follow by construction:
each field's points carry the sign of its real effect, and the points add up exactly to
the distance between the starting estimate and the current one. The previous version
rescaled log-odds SHAP values by a ratio whose denominator could approach zero; it
produced contributions of several hundred points and labelled one in twenty-five the
wrong way round.
"""

from __future__ import annotations

import json
import math
import pickle
from dataclasses import dataclass
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.calibration import PROB_CEIL, PROB_FLOOR
from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES, derive_frame, prepare

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "models"

EXACT_SHAPLEY_MAX_PLAYERS = 10   # 2^10 = 1,024 predictions, one batched call
SAMPLED_PERMUTATIONS = 256


@dataclass
class Attribution:
    """How the answers moved the estimate away from where it started."""

    base_probability: float               # context only, nothing answered
    probability: float                    # everything answered so far
    points: dict[str, float]              # feature -> signed percentage points


class Predictor:
    def __init__(self, models_dir: Path = MODELS):
        self.dir = models_dir
        self.meta = json.loads((models_dir / "metadata.json").read_text())
        self.risk = lgb.Booster(model_file=str(models_dir / "risk_model.txt"))
        self.carc = lgb.Booster(model_file=str(models_dir / "carc_model.txt"))
        self.calibrator = pickle.loads((models_dir / "calibrator.pkl").read_bytes())
        self.ref = self.meta["reference"]
        self.categories = self.meta["categories"]
        self.carc_classes = self.meta["carc_classes"]
        self.bands = self.meta["risk_bands"]

    # -- feature assembly ---------------------------------------------------

    def derived_frame(self, answer_sets: list[dict]) -> pd.DataFrame:
        raw = pd.DataFrame(answer_sets, columns=ASKABLE_FEATURES + CONTEXT_FEATURES)
        return derive_frame(raw, self.ref)

    def derived(self, answers: dict) -> dict:
        """Every model input for one claim, derived facts included, NaN as None."""
        row = self.derived_frame([answers]).iloc[0].to_dict()
        return {k: (None if _missing(v) else v) for k, v in row.items()}

    def build_frame(self, answer_sets: list[dict]) -> pd.DataFrame:
        return prepare(self.derived_frame(answer_sets), self.categories)

    # -- prediction ---------------------------------------------------------

    def predict_many(self, answer_sets: list[dict]) -> np.ndarray:
        """Calibrated, clamped rejection probabilities, batched in one model call."""
        if not answer_sets:
            return np.array([])
        raw = self.risk.predict(self.build_frame(answer_sets))
        return np.clip(self.calibrator.predict(raw), PROB_FLOOR, PROB_CEIL)

    def predict(self, answers: dict) -> float:
        return float(self.predict_many([answers])[0])

    def risk_band(self, p: float, bands: dict | None = None) -> str:
        bands = bands or self.bands
        if p <= bands["low_max"]:
            return "low"
        if p >= bands["high_min"]:
            return "high"
        return "medium"

    # -- attribution --------------------------------------------------------

    def attribute(self, answers: dict, players: list[str]) -> Attribution:
        """Shapley values of the calibrated probability over `players`.

        Every field not in `players` (the patient context) is held fixed, so the
        starting estimate is the claim as it stood before the first question.
        """
        players = list(dict.fromkeys(players))   # a field counted twice would break additivity
        fixed = {k: v for k, v in answers.items() if k not in players}
        n = len(players)
        if n == 0:
            p = self.predict(fixed)
            return Attribution(p, p, {})
        if n <= EXACT_SHAPLEY_MAX_PLAYERS:
            return self._exact(fixed, answers, players)
        return self._sampled(fixed, answers, players)

    def _exact(self, fixed: dict, answers: dict, players: list[str]) -> Attribution:
        n = len(players)
        coalitions = [{**fixed, **{players[i]: answers[players[i]]
                                   for i in range(n) if mask >> i & 1}}
                      for mask in range(1 << n)]
        v = self.predict_many(coalitions)
        weight = [math.factorial(s) * math.factorial(n - s - 1) / math.factorial(n)
                  for s in range(n)]
        points = {}
        for i, feat in enumerate(players):
            bit = 1 << i
            total = 0.0
            for mask in range(1 << n):
                if not mask & bit:
                    total += weight[mask.bit_count()] * (v[mask | bit] - v[mask])
            points[feat] = total * 100
        return Attribution(float(v[0]), float(v[-1]), points)

    def _sampled(self, fixed: dict, answers: dict, players: list[str]) -> Attribution:
        """Permutation-sampled Shapley values, for claims with many answered fields."""
        rng = np.random.default_rng(0)
        orders = [rng.permutation(len(players)) for _ in range(SAMPLED_PERMUTATIONS)]
        chains = []
        for order in orders:
            state = dict(fixed)
            chains.append(dict(state))
            for i in order:
                state[players[i]] = answers[players[i]]
                chains.append(dict(state))
        v = self.predict_many(chains).reshape(len(orders), len(players) + 1)
        points = {f: 0.0 for f in players}
        for row, order in zip(v, orders):
            for step, i in enumerate(order):
                points[players[i]] += (row[step + 1] - row[step]) * 100 / len(orders)
        return Attribution(float(v[0, 0]), float(v[0, -1]), points)

    # -- reason code --------------------------------------------------------

    def predict_carc(self, answers: dict, top_n: int = 3) -> list[tuple[str, float]]:
        probs = self.carc.predict(self.build_frame([answers]))[0]
        order = np.argsort(probs)[::-1][:top_n]
        return [(self.carc_classes[i], float(probs[i])) for i in order]


def _missing(v: object) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


_predictor: Predictor | None = None


def get_predictor() -> Predictor:
    """Process-wide singleton; the models load once."""
    global _predictor
    if _predictor is None:
        _predictor = Predictor()
    return _predictor

