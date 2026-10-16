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
from ml.features import BILL_FEATURES, PATIENT_FEATURES
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


def test_only_supplied_fields_get_points(predictor):
    for answers, players in verdict_states(predictor, n=10):
        assert set(predictor.attribute(answers, players).points) == set(players)


def test_exact_attribution_matches_brute_force_shapley(predictor):
    """Average each field's marginal contribution over every ordering of the fields —
    the definition of a Shapley value — and compare."""
    from itertools import permutations

    answers = dict(RISKY_CLAIM)
    players = [*BILL_FEATURES, "pre_auth_obtained", "documents_complete"]
    fixed = {k: v for k, v in answers.items() if k not in players}
    totals = {f: 0.0 for f in players}
    orders = list(permutations(players))
    for order in orders:
        state = dict(fixed)
        before = predictor.predict(state)
        for f in order:
            state[f] = answers[f]
            after = predictor.predict(state)
            totals[f] += (after - before) * 100 / len(orders)
            before = after
    points = predictor.attribute(answers, players).points
    for f in players:
        assert points[f] == pytest.approx(totals[f], abs=1e-9)


def test_a_field_listed_twice_is_counted_once(predictor):
    answers = dict(RISKY_CLAIM)
    once = predictor.attribute(answers, [*BILL_FEATURES, "pre_auth_obtained"])
    twice = predictor.attribute(answers, [*BILL_FEATURES, "pre_auth_obtained", "pre_auth_obtained"])
    assert twice.points == once.points
    assert sum(twice.points.values()) == pytest.approx(
        (twice.probability - twice.base_probability) * 100, abs=1e-9)


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
