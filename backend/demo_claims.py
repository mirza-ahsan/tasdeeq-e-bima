"""The two scripted claims used in the walkthrough.

One should come out risky for a specific, nameable reason; the other should come out
clean, to prove the tool does not simply flag everything. Refined in Phase 7 once the
demo script is written.
"""

from __future__ import annotations

CONTEXT = {"patient_age": 44, "patient_gender": "F"}

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

DEMO_CLAIMS = {"risky": RISKY_CLAIM, "clean": CLEAN_CLAIM}
