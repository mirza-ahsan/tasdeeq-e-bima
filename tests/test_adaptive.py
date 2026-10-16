"""The question engine — the behaviour the demo depends on.

These assert the claims we make about the engine on stage: that it terminates, that
pre-authorisation is asked exactly when it matters, that a missing one is surfaced and
named, that the question order genuinely changes between claims, and that the training
simulation the bands were fitted on is the loop users actually get.
"""

from __future__ import annotations

import pandas as pd
import pytest

from backend.demo_claims import CLEAN_CLAIM, RISKY_CLAIM
from backend.services.adaptive import MAX_QUESTIONS, MIN_QUESTIONS, AdaptiveEngine, stop_reason
from ml.features import ASKABLE_FEATURES, CONTEXT_FEATURES, derive_frame
from ml.simulate import verdict, walk_claims
from tests.helpers import CLAIMS, run_loop


@pytest.fixture(scope="module")
def engine() -> AdaptiveEngine:
    return AdaptiveEngine()


@pytest.fixture(scope="module")
def claims() -> pd.DataFrame:
    return pd.read_parquet(CLAIMS)


# --- termination and basic contract ---------------------------------------

def test_loop_always_terminates_without_repeating(engine, claims):
    for _, row in claims.sample(40, random_state=3).iterrows():
        step, order = run_loop(engine, row)
        assert step.done
        assert len(order) <= MAX_QUESTIONS
        assert len(order) == len(set(order)), f"repeated question in {order}"
        assert set(order) <= set(ASKABLE_FEATURES)


def test_never_gives_a_verdict_before_the_minimum(engine, claims):
    for _, row in claims.sample(40, random_state=11).iterrows():
        step, order = run_loop(engine, row)
        assert len(order) >= MIN_QUESTIONS or step.stop_reason == "all_questions_answered"


def test_low_information_gain_cannot_cut_a_check_short():
    """Below the minimum the loop keeps asking, however little a question would add."""
    assert stop_reason(1, "medium", best_gain=1e-9) is None
    assert stop_reason(MIN_QUESTIONS, "medium", best_gain=1e-9) == "no_informative_questions_left"


def test_information_gain_is_never_negative(engine):
    """Mutual information is non-negative by construction. A negative value means the
    baseline has drifted away from the marginalised probability again."""
    context = {f: RISKY_CLAIM[f] for f in CONTEXT_FEATURES}
    for answers in (context, {**context, "pre_auth_obtained": "no"},
                    {f: CLEAN_CLAIM[f] for f in CONTEXT_FEATURES}):
        gains = engine.information_gains(answers)
        assert gains, "expected some open questions"
        assert min(gains.values()) >= -1e-9, f"negative gain: {gains}"


def test_probability_stays_within_clamped_range(engine, claims):
    floor, ceil = engine.p.meta["prob_floor"], engine.p.meta["prob_ceil"]
    for _, row in claims.sample(30, random_state=9).iterrows():
        step, _ = run_loop(engine, row)
        assert floor <= step.probability <= ceil


# --- pre-authorisation is asked exactly when it matters --------------------

def test_pre_auth_is_asked_first_when_the_procedure_needs_it(engine):
    _, order = run_loop(engine, RISKY_CLAIM)
    assert order[0] == "pre_auth_obtained"
    step = engine.next_step({f: RISKY_CLAIM[f] for f in CONTEXT_FEATURES})
    assert [o.value for o in step.question.options] == ["yes", "no"], \
        "'not required' must never be offered for a procedure that requires it"


def test_pre_auth_is_never_asked_when_it_was_not_needed(engine):
    _, order = run_loop(engine, CLEAN_CLAIM)
    assert "pre_auth_obtained" not in order


def test_the_amount_alone_can_make_pre_auth_required(engine):
    small = {"patient_age": 30, "patient_gender": "F", "procedure_category": "dental",
             "claim_amount_pkr": 5_000}
    large = {**small, "claim_amount_pkr": 30_000}
    assert "pre_auth_obtained" not in engine.candidates(small)
    assert "pre_auth_obtained" in engine.candidates(large)


def test_staff_believing_pre_auth_was_not_needed_counts_as_not_obtained(engine):
    """The costliest real mistake must not score as safely as having approval."""
    frame = pd.DataFrame([
        {"procedure_category": "minor_surgery", "claim_amount_pkr": 85_000,
         "pre_auth_obtained": "not_required"},
        {"procedure_category": "consultation_exam", "claim_amount_pkr": 2_500,
         "pre_auth_obtained": "yes"},
    ])
    out = derive_frame(frame, engine.p.ref)
    assert out.pre_auth_obtained.tolist() == ["no", "not_required"]


# --- the demo claims -------------------------------------------------------

def test_risky_claim_is_flagged_high_and_blames_pre_auth(engine):
    step, order = run_loop(engine, RISKY_CLAIM)
    assert step.risk_band == "high"
    answers = {f: RISKY_CLAIM[f] for f in [*CONTEXT_FEATURES, *order]}
    points = engine.p.attribute(answers, ["procedure_category", "claim_amount_pkr", *order]).points
    assert max(points, key=points.get) == "pre_auth_obtained"
    codes = [code for code, _ in engine.p.predict_carc(answers)]
    assert codes[0] == "197", f"CARC 197 should lead, got {codes}"


def test_clean_claim_is_cleared(engine):
    step, order = run_loop(engine, CLEAN_CLAIM)
    assert step.risk_band == "low"
    assert step.stop_reason == "confident_low_risk"
    assert len(order) <= 4


def test_question_order_differs_between_claims(engine):
    """The adaptiveness claim itself. If these two orders match, the engine is a form."""
    _, clean_order = run_loop(engine, CLEAN_CLAIM)
    _, risky_order = run_loop(engine, RISKY_CLAIM)
    assert clean_order != risky_order


def test_order_varies_across_many_real_claims(engine, claims):
    orders = {tuple(run_loop(engine, row)[1])
              for _, row in claims.sample(60, random_state=7).iterrows()}
    assert len(orders) >= 10, f"only {len(orders)} distinct question orders across 60 claims"


# --- the simulation the bands were fitted on is the real loop ---------------

def test_band_simulation_matches_the_live_loop(engine, claims):
    """Bands are chosen by walking each claim once and scoring the walk under every
    setting. That is only valid if the scored verdict is exactly what next_step gives."""
    tuned = {**engine.p.bands, "min_info_gain": engine.min_gain}
    sample = claims.sample(25, random_state=21)
    for walk, (_, row) in zip(walk_claims(engine, sample), sample.iterrows()):
        simulated = verdict(walk, tuned)
        step, order = run_loop(engine, row)
        assert (simulated.n_answered, simulated.band, simulated.reason) == \
               (step.n_answered, step.risk_band, step.stop_reason)
        assert list(simulated.order) == order


# --- reasons are facts, and the labeller and the API agree on them -----------

def test_defect_rules_match_the_labeller_on_every_claim(engine, claims):
    """The API names a reason only from `defect_codes`; the labeller assigns codes from
    its own conditions. If the two ever disagreed, the tool could cite a reason the data
    never contained, or miss one it did."""
    import ml.label_carc as labeller
    from ml.features import ASKABLE_FEATURES as ASK, defect_codes

    raw = claims[ASK + list(CONTEXT_FEATURES)].copy()
    derived = derive_frame(raw, engine.p.ref)
    conditions = labeller.build_conditions(derived.copy())
    male = (derived.patient_gender == "M").to_numpy()
    for i, row in enumerate(derived.to_dict("records")):
        expected = []
        for code, column, _ in labeller.RULES:
            if conditions[column].iloc[i]:
                code = "7" if code == "6" and male[i] else code
                if code not in expected:
                    expected.append(code)
        assert defect_codes(row) == expected, f"claim {i}: {defect_codes(row)} vs {expected}"
