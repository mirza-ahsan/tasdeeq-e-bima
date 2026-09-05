"""Phase 4 tests — the behaviour the demo depends on.

These assert the claims we make about the engine on stage: that it terminates, that
clean claims resolve quickly, that a missing pre-authorisation is surfaced and named,
and above all that the question order genuinely changes between claims. That last one
is what separates this from a form with a model bolted on.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.services.adaptive import (MAX_QUESTIONS, AdaptiveEngine, Step)
from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES, TARGET

CONTEXT = {"patient_age": 44, "patient_gender": "F"}

CLEAN_CLAIM = {
    **CONTEXT,
    "procedure_category": "consultation_exam",
    "diagnosis_category": "respiratory_infection",
    "insurer_tpa": "Aftab Health Assurance",
    "plan_tier": "corporate",
    "pre_auth_obtained": "not_required",
    "documents_complete": "complete",
    "days_since_treatment": 3,
    "claim_amount_pkr": 4500,
    "provider_network_status": "in_network",
    "patient_policy_active": True,
    "is_emergency": False,
    "diagnosis_procedure_match": True,
    "is_duplicate_submission": False,
    "prior_claims_30d": 0,
}

RISKY_CLAIM = {
    **CONTEXT,
    "procedure_category": "minor_surgery",
    "diagnosis_category": "injury_trauma",
    "insurer_tpa": "Peymaan Health",
    "plan_tier": "basic",
    "pre_auth_obtained": "no",
    "documents_complete": "partial",
    "days_since_treatment": 12,
    "claim_amount_pkr": 185000,
    "provider_network_status": "in_network",
    "patient_policy_active": True,
    "is_emergency": False,
    "diagnosis_procedure_match": True,
    "is_duplicate_submission": False,
    "prior_claims_30d": 1,
}


@pytest.fixture(scope="module")
def engine() -> AdaptiveEngine:
    return AdaptiveEngine()


def run_loop(engine: AdaptiveEngine, truth: dict) -> tuple[Step, list[str]]:
    """Drive the loop to completion, answering from `truth`."""
    answers = {f: truth[f] for f in CONTEXT_FEATURES if f in truth}
    order: list[str] = []
    for _ in range(MAX_QUESTIONS + 2):  # guard against a non-terminating loop
        step = engine.next_step(answers)
        if step.done:
            return step, order
        order.append(step.question.feature)
        answers[step.question.feature] = truth[step.question.feature]
    pytest.fail("loop did not terminate within the question cap")


# --- termination and basic contract ---------------------------------------

def test_loop_always_terminates(engine):
    df = pd.read_parquet("data/processed/claims.parquet").sample(40, random_state=3)
    for _, row in df.iterrows():
        step, order = run_loop(engine, row)
        assert step.done
        assert len(order) <= MAX_QUESTIONS


def test_never_asks_the_same_question_twice(engine):
    df = pd.read_parquet("data/processed/claims.parquet").sample(40, random_state=5)
    for _, row in df.iterrows():
        _, order = run_loop(engine, row)
        assert len(order) == len(set(order)), f"repeated question in {order}"


def test_only_asks_askable_features(engine):
    _, order = run_loop(engine, RISKY_CLAIM)
    assert set(order) <= set(ASKABLE_FEATURES)


def test_information_gain_is_never_negative(engine):
    """Mutual information is non-negative by construction. A negative value means the
    baseline has drifted away from the marginalised probability again."""
    for answers in ({}, {"pre_auth_obtained": "no"}, CONTEXT):
        gains = engine.information_gains(answers)
        assert gains, "expected some unanswered features"
        assert min(gains.values()) >= -1e-9, f"negative gain: {gains}"


# --- the demo claims -------------------------------------------------------

def test_clean_claim_resolves_quickly(engine):
    step, order = run_loop(engine, CLEAN_CLAIM)
    assert len(order) <= 3, f"clean claim took {len(order)} questions: {order}"
    assert step.risk_band == "low"
    assert step.stop_reason == "confident_low_risk"


def test_risky_claim_takes_more_questions_than_clean(engine):
    clean_step, clean_order = run_loop(engine, CLEAN_CLAIM)
    risky_step, risky_order = run_loop(engine, RISKY_CLAIM)
    assert len(risky_order) > len(clean_order), (
        f"risky took {len(risky_order)}, clean took {len(clean_order)} — "
        "the loop is not adapting to difficulty")
    assert risky_step.probability > clean_step.probability


def test_missing_pre_auth_is_surfaced_and_blamed(engine):
    """The headline demo moment: the tool asks about pre-auth, then names it."""
    _, order = run_loop(engine, RISKY_CLAIM)
    assert "pre_auth_obtained" in order

    contributions = engine.p.explain(RISKY_CLAIM)
    blamed = [c.feature for c in contributions if c.effect == "increases_risk"]
    assert "pre_auth_obtained" in blamed, f"pre-auth not blamed; got {blamed}"

    codes = [code for code, _ in engine.p.predict_carc(RISKY_CLAIM)]
    assert "197" in codes, f"CARC 197 not in top predictions: {codes}"


def test_question_order_differs_between_claims(engine):
    """The adaptiveness claim itself. If these two orders match, the engine is a form."""
    _, clean_order = run_loop(engine, CLEAN_CLAIM)
    _, risky_order = run_loop(engine, RISKY_CLAIM)
    assert clean_order != risky_order, (
        f"identical question order for both claims: {clean_order}")


def test_order_varies_across_many_real_claims(engine):
    """Adaptiveness at scale, not just on two hand-picked claims."""
    df = pd.read_parquet("data/processed/claims.parquet").sample(60, random_state=7)
    orders = {tuple(run_loop(engine, row)[1]) for _, row in df.iterrows()}
    assert len(orders) >= 15, f"only {len(orders)} distinct question orders across 60 claims"


# --- stopping rule ---------------------------------------------------------

def test_never_stops_before_minimum_questions(engine):
    step = engine.next_step(CONTEXT)
    assert not step.done, "stopped before asking anything"


def test_probability_stays_within_clamped_range(engine):
    df = pd.read_parquet("data/processed/claims.parquet").sample(30, random_state=9)
    floor, ceil = engine.p.meta["prob_floor"], engine.p.meta["prob_ceil"]
    for _, row in df.iterrows():
        step, _ = run_loop(engine, row)
        assert floor <= step.probability <= ceil
