"""Run the live question loop over held-out claims, as a user would meet it.

Two jobs. On the validation split it chooses the risk bands; on the test split it
reports what users actually get: which verdict each claim ends on, after how many
questions, and how often that verdict is right. Model metrics such as AUC describe the
classifier; these describe the product.

The question a claim is asked next never depends on the bands, only *when* the loop
stops does. So each claim is walked once to the question cap (`AdaptiveEngine.trajectory`)
and then scored under any band setting with `backend.services.adaptive.stop_reason`, the
same function the live loop calls. Tuning therefore sees exactly the stopping behaviour
users get, at no extra cost per setting.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backend.services.adaptive import (MIN_QUESTIONS, AdaptiveEngine, TrajectoryStep,
                                       stop_reason)
from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES, PATIENT_FEATURES, TARGET

# A verdict has to mean what it says. "Looks ready to submit" must be right for at least
# this share of claims it is given to; "high risk" must be more likely rejected than not.
MIN_LOW_BAND_APPROVAL = 0.85
MIN_HIGH_BAND_REJECTION = 0.50
MIN_BAND_CLAIMS = 15
# A typical claim, before anything about it is known, must not already read as a verdict.
MIN_PRIOR_IN_MEDIUM = 1.0

# An average check should stay short; a setting that settles more claims only by asking
# everyone more questions is not chosen past this.
QUESTION_BUDGET = 4.5

LOW_GRID = np.round(np.arange(0.08, 0.26, 0.01), 2)
HIGH_GRID = np.round(np.arange(0.30, 0.71, 0.01), 2)
# 0.0 means "never stop for low gain": only a settled band or the cap ends a check.
GAIN_GRID = [0.0, 0.0005, 0.001, 0.002, 0.003, 0.004, 0.006, 0.008, 0.012]


@dataclass
class Walk:
    steps: list[TrajectoryStep]
    order: list[str]
    rejected: bool
    typical_probability: float    # the patient alone, before the bill is entered


@dataclass
class Verdict:
    n_answered: int
    probability: float
    band: str
    reason: str
    order: tuple[str, ...]
    rejected: bool


def _python(v: object) -> object:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v.item() if isinstance(v, np.generic) else v


def walk_claims(engine: AdaptiveEngine, claims: pd.DataFrame) -> list[Walk]:
    walks = []
    for row in claims.to_dict("records"):
        truth = {f: _python(row[f]) for f in ASKABLE_FEATURES + CONTEXT_FEATURES}
        context = {f: truth[f] for f in CONTEXT_FEATURES}
        steps, order = engine.trajectory(truth, context)
        typical = engine.p.predict({f: truth[f] for f in PATIENT_FEATURES})
        walks.append(Walk(steps, order, bool(row[TARGET]), typical))
    return walks


def band_of(p: float, bands: dict) -> str:
    if p <= bands["low_max"]:
        return "low"
    if p >= bands["high_min"]:
        return "high"
    return "medium"


def verdict(walk: Walk, bands: dict) -> Verdict:
    """The verdict this claim gets under `bands`, which also carries `min_info_gain`."""
    for s in walk.steps:
        band = band_of(s.probability, bands)
        reason = stop_reason(s.n_answered, band, s.best_gain, bands["min_info_gain"])
        if reason:
            return Verdict(s.n_answered, s.probability, band, reason,
                           tuple(walk.order[:s.n_answered]), walk.rejected)
    raise AssertionError("a trajectory always ends on a stopping step")


def summarise(verdicts: list[Verdict]) -> dict:
    n = len(verdicts)
    rejected = [v for v in verdicts if v.rejected]
    approved = [v for v in verdicts if not v.rejected]
    bands = {}
    for b in ("low", "medium", "high"):
        in_band = [v for v in verdicts if v.band == b]
        bands[b] = {
            "claims": len(in_band),
            "share": round(len(in_band) / n, 4),
            "rejection_rate": round(float(np.mean([v.rejected for v in in_band])), 4)
            if in_band else None,
        }
    return {
        "claims": n,
        "bands": bands,
        "rejections_flagged": round(float(np.mean([v.band != "low" for v in rejected])), 4),
        "rejections_in_high_band": round(float(np.mean([v.band == "high" for v in rejected])), 4),
        "mean_questions": round(float(np.mean([v.n_answered for v in verdicts])), 2),
        "mean_questions_approved": round(float(np.mean([v.n_answered for v in approved])), 2),
        "mean_questions_rejected": round(float(np.mean([v.n_answered for v in rejected])), 2),
        "share_at_minimum": round(float(np.mean([v.n_answered == MIN_QUESTIONS
                                                 for v in verdicts])), 4),
        "share_at_cap": round(float(np.mean([v.reason == "question_limit_reached"
                                             for v in verdicts])), 4),
        "distinct_question_orders": len({v.order for v in verdicts}),
    }


def tune_bands(walks: list[Walk], base_rate: float) -> tuple[dict, dict]:
    """The most decisive bands and stopping gain whose verdicts are still reliable.

    Among settings where (a) "low" ends below the average claim's risk and "high" starts
    above it, so the label never contradicts the number beside it, (b) a typical claim,
    before its bill is entered, reads as "medium" for every patient, (c) the low band is
    right at least MIN_LOW_BAND_APPROVAL of the time, (d) the high band means more
    likely rejected than not and (e) an average check stays within QUESTION_BUDGET
    questions, pick the one that settles the most claims as low or high; ties go to
    catching more rejections, then to asking fewer questions.
    """
    priors = np.array([w.typical_probability for w in walks])
    best_key, best = None, None
    for gain in GAIN_GRID:
        for lo in LOW_GRID:
            for hi in HIGH_GRID:
                if hi - lo < 0.10 or not lo < base_rate < hi:
                    continue
                if np.mean((priors > lo) & (priors < hi)) < MIN_PRIOR_IN_MEDIUM:
                    continue
                bands = {"low_max": float(lo), "high_min": float(hi),
                         "min_info_gain": float(gain)}
                vs = [verdict(w, bands) for w in walks]
                questions = float(np.mean([v.n_answered for v in vs]))
                if questions > QUESTION_BUDGET:
                    continue
                low = [v.rejected for v in vs if v.band == "low"]
                high = [v.rejected for v in vs if v.band == "high"]
                if len(low) < MIN_BAND_CLAIMS or 1 - np.mean(low) < MIN_LOW_BAND_APPROVAL:
                    continue
                if len(high) < MIN_BAND_CLAIMS or np.mean(high) < MIN_HIGH_BAND_REJECTION:
                    continue
                decisive = (len(low) + len(high)) / len(vs)
                flagged = np.mean([v.band != "low" for v in vs if v.rejected])
                key = (round(decisive, 4), round(float(flagged), 4), -round(questions, 3))
                if best_key is None or key > best_key:
                    best_key, best = key, bands
    if best is None:
        raise RuntimeError("no band setting meets the reliability constraints; "
                           "the model is too weak to give trustworthy verdicts")
    report = {
        "constraints": {
            "bands_straddle_base_rate": round(base_rate, 4),
            "question_budget": QUESTION_BUDGET,
            "min_low_band_approval": MIN_LOW_BAND_APPROVAL,
            "min_high_band_rejection": MIN_HIGH_BAND_REJECTION,
            "min_band_claims": MIN_BAND_CLAIMS,
            "min_prior_in_medium": MIN_PRIOR_IN_MEDIUM,
        },
        "typical_probability_range": [round(float(priors.min()), 4),
                                       round(float(priors.max()), 4)],
        "validation": summarise([verdict(w, best) for w in walks]),
    }
    return best, report
