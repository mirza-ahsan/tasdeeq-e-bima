"""The two scripted claims used in the walkthrough.

One should come out risky for a specific, nameable reason; the other should come out
clean, to prove the tool does not simply flag everything. Both go through the same
question loop as a blank check — only the answers are scripted. The tests import these,
so the demo the script narrates is the demo the suite checks.

The bill (procedure and amount) and the patient are entered up front, as on a real
claim; every other field is answered only if the engine asks for it.
"""

from __future__ import annotations

# A laceration sutured in a minor-surgery room: needs pre-authorisation at any price,
# and nobody asked the insurer first.
RISKY_CLAIM = {
    "patient_age": 44,
    "patient_gender": "F",
    "procedure_category": "minor_surgery",
    "claim_amount_pkr": 85_000,
    "diagnosis_category": "injury_trauma",
    "insurer_tpa": "Peymaan Health",
    "plan_tier": "standard",
    "pre_auth_obtained": "no",
    "documents_complete": "complete",
    "days_since_treatment": 12,
    "provider_network_status": "in_network",
    "patient_policy_active": True,
    "is_emergency": False,
    "is_duplicate_submission": False,
    "prior_claims_30d": 1,
}

# A routine consultation for a chest infection, filed promptly, papers in order.
CLEAN_CLAIM = {
    "patient_age": 29,
    "patient_gender": "F",
    "procedure_category": "consultation_exam",
    "claim_amount_pkr": 2_500,
    "diagnosis_category": "respiratory_infection",
    "insurer_tpa": "Aftab Health Assurance",
    "plan_tier": "corporate",
    "documents_complete": "complete",
    "days_since_treatment": 3,
    "provider_network_status": "in_network",
    "patient_policy_active": True,
    "is_emergency": False,
    "is_duplicate_submission": False,
    "prior_claims_30d": 0,
}

DEMO_CLAIMS = {"risky": RISKY_CLAIM, "clean": CLEAN_CLAIM}
