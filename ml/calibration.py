"""Shared probability post-processing.

Isotonic regression saturates: it maps the top bin to exactly 1.0 and the bottom
bin to exactly 0.0. On the test split, claims scored 1.0 were actually rejected only
33% of the time, so the saturation is genuine overconfidence rather than a display
quirk. We clamp to a defensible range.

Every path that produces a probability — training metrics, the model card, and the
API — imports from here, so the number a judge sees is the number we evaluated.
"""

from __future__ import annotations

import numpy as np

PROB_FLOOR = 0.02
PROB_CEIL = 0.97


def predict_proba(booster, calibrator, X) -> np.ndarray:
    """Calibrated, clamped probability of rejection."""
    return np.clip(calibrator.predict(booster.predict(X)), PROB_FLOOR, PROB_CEIL)
