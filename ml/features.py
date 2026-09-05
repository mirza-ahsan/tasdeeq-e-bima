"""Shared feature contract between training, the adaptive engine and the API.

One definition, imported everywhere, so the question flow can never drift out of
sync with what the model was trained on.
"""

from __future__ import annotations

# Fields the clinic staff member is actually asked about, one at a time.
ASKABLE_FEATURES = [
    "procedure_category",
    "diagnosis_category",
    "insurer_tpa",
    "plan_tier",
    "pre_auth_obtained",
    "documents_complete",
    "days_since_treatment",
    "claim_amount_pkr",
    "provider_network_status",
    "patient_policy_active",
    "is_emergency",
    "diagnosis_procedure_match",
    "is_duplicate_submission",
    "prior_claims_30d",
]

# Known from the patient record before the conversation starts — never asked.
CONTEXT_FEATURES = [
    "patient_age",
    "patient_gender",
]

# Computed from answers. Each is unknown until every one of its inputs is known.
DERIVED_DEPENDENCIES = {
    "pre_auth_required_for_procedure": ["procedure_category", "claim_amount_pkr"],
    "amount_vs_procedure_median":      ["claim_amount_pkr", "procedure_category"],
    "days_vs_insurer_filing_limit":    ["days_since_treatment", "insurer_tpa"],
    "insurer_filing_limit_days":       ["insurer_tpa"],
}
DERIVED_FEATURES = list(DERIVED_DEPENDENCIES)

FEATURES = ASKABLE_FEATURES + CONTEXT_FEATURES + DERIVED_FEATURES

CATEGORICAL_FEATURES = [
    "procedure_category",
    "diagnosis_category",
    "insurer_tpa",
    "plan_tier",
    "pre_auth_obtained",
    "documents_complete",
    "provider_network_status",
    "patient_gender",
]

BOOLEAN_FEATURES = [
    "patient_policy_active",
    "is_emergency",
    "diagnosis_procedure_match",
    "is_duplicate_submission",
    "pre_auth_required_for_procedure",
]

TARGET = "is_rejected"
CARC_TARGET = "carc_code"
