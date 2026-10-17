"""Rule checks: the insurer policy facts the tool knows that the clerk may not.

The risk model gives a probability. These give the reasons a clerk can verify and act
on: whether this procedure needed pre-authorisation, how much of this insurer's filing
window has gone, whether the bill is unusually high for the procedure, whether the plan
covers it, whether the diagnosis justifies it, whether it fits the patient. Each is a
deterministic reading of the same rule tables the labeller uses (ml/features.py), never
a model output, so a check can be wrong only if the rule is.

A clerk who already knows pre-authorisation was not obtained learns nothing from being
told so. The value is in the facts they would otherwise have to look up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from backend.services.adaptive import value_label
from ml.features import (AMOUNT_EXCESSIVE_RATIO, ELECTIVE_CATEGORIES, TIER_EXCLUSIONS)

Status = Literal["ok", "warn", "issue", "pending"]

WARN_AMOUNT_RATIO = 1.8
WARN_FILING_SHARE = 0.8


@dataclass
class Check:
    id: str
    label: str
    status: Status
    detail: str


def _proc(answers: dict) -> str:
    return value_label("procedure_category", answers.get("procedure_category")).lower()


def pre_auth_check(answers: dict, derived: dict, ref: dict) -> Check:
    required = derived.get("pre_auth_required_for_procedure")
    proc, amount = answers.get("procedure_category"), answers.get("claim_amount_pkr")
    if required is None:
        return Check("pre_auth", "Pre-authorisation", "pending", "Depends on the procedure and amount.")
    if required == 0:
        return Check("pre_auth", "Pre-authorisation", "ok",
                     f"Not needed for {_proc(answers)} at {value_label('claim_amount_pkr', amount)}.")
    if proc in ref["pre_auth_categories"]:
        why = f"Needed for {_proc(answers)} at any price"
    else:
        why = f"Needed because the claim is over PKR {ref['pre_auth_amount_threshold_pkr']:,}"
    obtained = answers.get("pre_auth_obtained")
    if obtained == "yes":
        return Check("pre_auth", "Pre-authorisation", "ok", f"{why}, and obtained.")
    if obtained == "no":
        return Check("pre_auth", "Pre-authorisation", "issue", f"{why}, and not obtained.")
    return Check("pre_auth", "Pre-authorisation", "pending", f"{why}; not confirmed yet.")


def filing_check(answers: dict, derived: dict) -> Check:
    insurer, days = answers.get("insurer_tpa"), answers.get("days_since_treatment")
    limit = derived.get("insurer_filing_limit_days")
    if insurer is None or limit is None:
        return Check("filing", "Filing deadline", "pending", "Depends on the insurer.")
    limit = int(limit)
    if days is None:
        return Check("filing", "Filing deadline", "pending", f"{insurer} allows {limit} days.")
    share = days / limit
    if share > 1:
        return Check("filing", "Filing deadline", "issue",
                     f"Day {days} of a {limit}-day window: past {insurer}'s deadline.")
    if share > WARN_FILING_SHARE:
        return Check("filing", "Filing deadline", "warn",
                     f"Day {days} of {limit}: file now, the window is nearly closed.")
    return Check("filing", "Filing deadline", "ok", f"Day {days} of {insurer}'s {limit}-day window.")


def amount_check(answers: dict, derived: dict, ref: dict) -> Check:
    proc = answers.get("procedure_category")
    ratio = derived.get("amount_vs_procedure_median")
    median = ref["procedure_median_amount_pkr"].get(proc)
    if ratio is None or median is None:
        return Check("amount", "Amount vs typical", "pending", "Depends on the procedure.")
    typical = f"PKR {int(median):,}"
    if ratio > AMOUNT_EXCESSIVE_RATIO:
        return Check("amount", "Amount vs typical", "issue",
                     f"{ratio:.1f}× the typical {typical} for {_proc(answers)}.")
    if ratio > WARN_AMOUNT_RATIO:
        return Check("amount", "Amount vs typical", "warn",
                     f"{ratio:.1f}× the typical {typical}; expect it to be queried.")
    return Check("amount", "Amount vs typical", "ok", f"In the usual range (typical {typical}).")


def coverage_check(answers: dict) -> Check:
    proc, tier = answers.get("procedure_category"), answers.get("plan_tier")
    if tier is None:
        return Check("coverage", "Plan covers it", "pending", "Depends on the plan tier.")
    plan = value_label("plan_tier", tier)
    if proc in TIER_EXCLUSIONS.get(tier, ()):
        return Check("coverage", "Plan covers it", "issue",
                     f"{plan} plans exclude {_proc(answers)}.")
    if tier == "basic" and proc in ELECTIVE_CATEGORIES:
        emergency = answers.get("is_emergency")
        if emergency is True:
            return Check("coverage", "Plan covers it", "ok",
                         f"Covered: {plan} plans accept {_proc(answers)} in an emergency.")
        unless = "" if emergency is False else ", unless it was an emergency"
        return Check("coverage", "Plan covers it", "warn",
                     f"{plan} plans often refuse elective {_proc(answers)} as not medically "
                     f"necessary{unless}.")
    return Check("coverage", "Plan covers it", "ok", f"{plan} plans cover {_proc(answers)}.")


def diagnosis_check(answers: dict, derived: dict) -> Check:
    match = derived.get("diagnosis_procedure_match")
    if match is None:
        return Check("diagnosis", "Diagnosis fits procedure", "pending", "Depends on the diagnosis.")
    dx = value_label("diagnosis_category", answers.get("diagnosis_category"))
    if match == 1:
        return Check("diagnosis", "Diagnosis fits procedure", "ok", f"{dx} supports {_proc(answers)}.")
    return Check("diagnosis", "Diagnosis fits procedure", "issue",
                 f"{dx} does not usually justify {_proc(answers)}.")


def patient_check(answers: dict, derived: dict) -> Check:
    conflict = derived.get("procedure_patient_conflict")
    if conflict is None:
        return Check("patient", "Procedure fits patient", "pending", "Depends on the patient.")
    if conflict == 1:
        who = f"{int(answers['patient_age'])}-year-old " \
              f"{value_label('patient_gender', answers['patient_gender']).lower()}"
        return Check("patient", "Procedure fits patient", "issue",
                     f"{value_label('procedure_category', answers['procedure_category'])} "
                     f"billed for a {who} patient.")
    return Check("patient", "Procedure fits patient", "ok", "Consistent with the patient's age and gender.")


def build_checks(answers: dict, derived: dict, ref: dict) -> list[Check]:
    return [
        pre_auth_check(answers, derived, ref),
        filing_check(answers, derived),
        amount_check(answers, derived, ref),
        coverage_check(answers),
        diagnosis_check(answers, derived),
        patient_check(answers, derived),
    ]
