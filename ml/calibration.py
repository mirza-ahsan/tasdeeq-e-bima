"""Shared probability calibration and post-processing.

Every path that produces a probability — training metrics, the model card, and the API
— imports from here, so the number a judge sees is the number we evaluated.

Two decisions live here.

**Clamping.** Calibration can push extreme scores to 0 or 1. On the test split, claims
scored exactly 1.0 were actually rejected only a third of the time, so the saturation was
genuine overconfidence rather than a display quirk. We clamp to a defensible range.

**Isotonic rather than Platt scaling.** Both were measured. Platt is smoother — 340
distinct output values against isotonic's 76 — and marginally better at ranking (AUC
0.802 vs 0.799, since isotonic's ties cost a little discrimination). Isotonic still wins
where it matters:

  * better calibrated (Brier 0.1485 vs 0.1523), so the percentage on screen is the
    more trustworthy number, which is the whole point of showing one;
  * far better adaptive behaviour — 65-72 distinct question orders across 200 claims
    versus 15 under Platt. That is the project's core differentiator, and Platt's
    compressed distribution (75% of claims between 0.15 and 0.27) flattens the
    information gains until the engine asks nearly everyone the same thing.

The known cost is plateaus: 33% of claims land on exactly 0.1772, so the risk figure can
sit unchanged across consecutive answers. That is honest — the answer genuinely did not
change the estimate — and the UI should say "no change" rather than pretend to animate.

One finding worth keeping: under Platt, risky claims resolve FASTER than clean ones at
every band setting tried. That is structural rather than a tuning failure. A single bad
answer pushes a claim over the high threshold immediately, whereas certifying a claim as
clean means ruling out every remaining risk factor. Isotonic's plateau happens to sit just
under the low band, which is what lets clean claims stop early.
"""

from __future__ import annotations

import numpy as np
from sklearn.isotonic import IsotonicRegression

PROB_FLOOR = 0.02
PROB_CEIL = 0.97


def build_calibrator() -> IsotonicRegression:
    return IsotonicRegression(out_of_bounds="clip")


def predict_proba(booster, calibrator, X) -> np.ndarray:
    """Calibrated, clamped probability of rejection."""
    return np.clip(calibrator.predict(booster.predict(X)), PROB_FLOOR, PROB_CEIL)
