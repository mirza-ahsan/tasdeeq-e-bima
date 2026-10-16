"""Per-field attribution — the "which field is the problem" answer the product promises.

The previous attribution rescaled log-odds SHAP values and produced contributions of
several hundred points, a few percent of them pointing the wrong way. These pin down
the properties that make the numbers on screen trustworthy.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.demo_claims import RISKY_CLAIM
from backend.predictor import Predictor
from ml.features import BILL_FEATURES, CONTEXT_FEATURES, PATIENT_FEATURES
from tests.helpers import CLAIMS, python_value, run_loop


@pytest.fixture(scope="module")
def predictor() -> Predictor:
    from backend.services.adaptive import AdaptiveEngine
    return AdaptiveEngine().p


def verdict_states(predictor, n=30):
    from backend.services.adaptive import AdaptiveEngine
    engine = AdaptiveEngine(predictor)
    for _, row in pd.read_parquet(CLAIMS).sample(n, random_state=4).iterrows():
        _, order = run_loop(engine, row)
        players = [*BILL_FEATURES, *order]
        answers = {f: python_value(row[f]) for f in [*PATIENT_FEATURES, *players]}
        yield answers, players


def test_points_add_up_to_the_change_in_estimate(predictor):
    for answers, players in verdict_states(predictor):
        a = predictor.attribute(answers, players)
        assert a.probability == pytest.approx(predictor.predict(answers))
        assert a.base_probability == pytest.approx(
            predictor.predict({f: answers[f] for f in PATIENT_FEATURES}))
        assert sum(a.points.values()) == pytest.approx(
            (a.probability - a.base_probability) * 100, abs=1e-6)


def test_points_stay_on_the_probability_scale(predictor):
    for answers, players in verdict_states(predictor):
        points = predictor.attribute(answers, players).points
        assert set(points) == set(players), "only fields the user supplied get points"
        assert all(abs(p) <= 100 for p in points.values())


def test_a_field_that_cannot_matter_gets_nothing(predictor):
    """Shapley's null-player property: a duplicate copy of the context is worth zero."""
    answers = {f: RISKY_CLAIM[f] for f in CONTEXT_FEATURES}
    a = predictor.attribute({**answers, "unused": 1}, [*BILL_FEATURES, "unused"])
    assert a.points["unused"] == pytest.approx(0.0, abs=1e-9)


def test_sampled_attribution_agrees_with_exact(predictor, monkeypatch):
    answers = dict(RISKY_CLAIM)
    players = [*BILL_FEATURES, "pre_auth_obtained", "documents_complete",
               "provider_network_status", "plan_tier"]
    exact = predictor.attribute(answers, players).points
    import backend.predictor as module
    monkeypatch.setattr(module, "EXACT_SHAPLEY_MAX_PLAYERS", 2)
    sampled = predictor.attribute(answers, players).points
    for f in players:
        assert sampled[f] == pytest.approx(exact[f], abs=1.5)
