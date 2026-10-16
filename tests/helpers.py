"""Shared test helpers. Kept out of conftest.py, which pytest imports under its own name."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.services.adaptive import MAX_QUESTIONS, AdaptiveEngine, Step
from ml.features import CONTEXT_FEATURES

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "data" / "processed" / "claims.parquet"


def python_value(v):
    """A dataframe cell as the plain Python value an API client would send."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    return v.item() if hasattr(v, "item") else v


def run_loop(engine: AdaptiveEngine, truth) -> tuple[Step, list[str]]:
    """Drive the loop to completion, answering from `truth`."""
    truth = {k: python_value(v) for k, v in dict(truth).items()}
    answers = {f: truth[f] for f in CONTEXT_FEATURES}
    order: list[str] = []
    for _ in range(MAX_QUESTIONS + 2):  # guard against a non-terminating loop
        step = engine.next_step(answers)
        if step.done:
            return step, order
        order.append(step.question.feature)
        answers[step.question.feature] = truth[step.question.feature]
    pytest.fail("loop did not terminate within the question cap")
