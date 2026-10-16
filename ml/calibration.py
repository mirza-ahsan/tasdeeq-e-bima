"""Shared probability calibration and post-processing.

Every path that produces a probability — training metrics, the model card, and the API
— imports from here, so the number a judge sees is the number we evaluated.

Two decisions live here.

**Clamping.** Calibration can push extreme scores to 0 or 1. Claims scored exactly 1.0
were not rejected every time, so the saturation was genuine overconfidence rather than a
display quirk. We clamp to a defensible range.

**Centred isotonic rather than plain isotonic or Platt.** Isotonic regression is the
better-calibrated of the two classical choices, and Platt's compressed distribution
flattened the information gains until the engine asked nearly everyone the same thing.
But plain isotonic is a staircase. Its wide flat steps froze the risk figure across
several answers that did move the model, and put the band boundaries on the edge of a
step, where a claim's verdict flipped on a hair's difference in score: a clean
consultation sat at 20.1% through four good answers, against a low band ending at 20%.

Centred isotonic regression (Oron & Flournoy, 2017) keeps isotonic's fitted value for
each block of tied predictions but places it at the block's centre and interpolates
linearly between neighbouring blocks. The block means — and so the calibration — are
unchanged; the steps between them become slopes, so every answer that moves the model
moves the number on screen.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

PROB_FLOOR = 0.02
PROB_CEIL = 0.97


class CenteredIsotonic:
    """Monotone, piecewise-linear calibrator through isotonic block centres."""

    def fit(self, raw: np.ndarray, y: np.ndarray) -> "CenteredIsotonic":
        raw = np.asarray(raw, dtype=float)
        y = np.asarray(y, dtype=float)
        order = np.argsort(raw, kind="mergesort")
        r = raw[order]
        fitted = IsotonicRegression(out_of_bounds="clip").fit(r, y[order]).predict(r)

        # Runs of equal fitted value are isotonic's blocks; each becomes one knot.
        edges = np.flatnonzero(np.diff(fitted)) + 1
        starts = np.r_[0, edges]
        stops = np.r_[edges, len(r)]
        xs = np.array([r[a:b].mean() for a, b in zip(starts, stops)])
        ys = fitted[starts]
        # Hold the end values flat beyond the outermost centres.
        self.x_ = np.r_[r[0], xs, r[-1]]
        self.y_ = np.r_[ys[0], ys, ys[-1]]
        keep = np.r_[True, np.diff(self.x_) > 0]
        self.x_, self.y_ = self.x_[keep], self.y_[keep]
        return self

    def predict(self, raw: np.ndarray) -> np.ndarray:
        return np.interp(np.asarray(raw, dtype=float), self.x_, self.y_)


def build_calibrator() -> CenteredIsotonic:
    return CenteredIsotonic()


def predict_proba(booster, calibrator, X) -> np.ndarray:
    """Calibrated, clamped probability of rejection."""
    return np.clip(calibrator.predict(booster.predict(X)), PROB_FLOOR, PROB_CEIL)
