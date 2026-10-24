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
from datetime import date
from typing import TYPE_CHECKING, Literal

from backend.services.adaptive import value_label
from ml.features import (AMOUNT_EXCESSIVE_RATIO, ELECTIVE_CATEGORIES, PRE_AUTH_CATEGORIES,
                         TIER_EXCLUSIONS)

if TYPE_CHECKING:
    from backend.services.documents import Reading

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


# ---------------------------------------------------------------------------
# Documents: the same kind of deterministic rule, applied to what was read from them.
# The KP Sehat Card audit blamed late payments on incomplete documents, so these look
# for exactly that: a missing paper, an approval for less than was billed, papers that
# disagree about the patient or the dates. They inform the clerk; they never change the
# model's estimate, and they never name a reason code (a reason is a fact, and only
# `defect_codes` decides those).
# ---------------------------------------------------------------------------

# Procedures billed as day-care or inpatient work, whose claims need treatment notes.
NEEDS_DISCHARGE_SUMMARY = PRE_AUTH_CATEGORIES
DATE_SLACK_DAYS = 3     # an admission a day or two before the billed date is normal


def _day(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def required_documents_check(answers: dict, derived: dict, reading: "Reading") -> Check:
    have = {d.type for d in reading.documents}
    need = {"bill": "the itemised bill"}
    if answers.get("procedure_category") in NEEDS_DISCHARGE_SUMMARY:
        need["discharge_summary"] = "a discharge summary"
    if derived.get("pre_auth_required_for_procedure") == 1:
        need["pre_auth_letter"] = "the pre-authorisation approval"
    missing = [label for kind, label in need.items() if kind not in have]
    if missing:
        return Check("doc_required", "Required documents", "warn",
                     "Not among the uploaded documents: " + ", ".join(missing)
                     + ". Attach " + ("it" if len(missing) == 1 else "them") + " to the claim.")
    return Check("doc_required", "Required documents", "ok",
                 "Everything this claim needs is among the documents.")


def approved_amount_check(answers: dict, reading: "Reading") -> Check | None:
    letter = next((d for d in reading.documents
                   if d.type == "pre_auth_letter" and d.amount_pkr), None)
    billed = answers.get("claim_amount_pkr")
    if letter is None or billed is None:
        return None
    if billed > letter.amount_pkr:
        return Check("doc_amount", "Within the approved amount", "issue",
                     f"Billed PKR {billed:,} but approved for PKR {letter.amount_pkr:,}: "
                     f"PKR {billed - letter.amount_pkr:,} above the approval. Ask the insurer "
                     "for a revised approval first.")
    return Check("doc_amount", "Within the approved amount", "ok",
                 f"Billed PKR {billed:,}, within the PKR {letter.amount_pkr:,} approved.")


def same_patient_check(answers: dict, reading: "Reading") -> Check | None:
    age, sex = answers.get("patient_age"), answers.get("patient_gender")
    clashes = []
    for d in reading.documents:
        if d.patient_age is not None and age is not None and d.patient_age != age:
            clashes.append(f"the {d.label.lower()} says {d.patient_age} years, the claim {age}")
        if d.patient_gender and sex and d.patient_gender != sex:
            clashes.append(f"the {d.label.lower()} says "
                           f"{value_label('patient_gender', d.patient_gender).lower()}")
    checked = any(d.patient_age is not None or d.patient_gender for d in reading.documents)
    if clashes:
        return Check("doc_patient", "Same patient throughout", "issue",
                     "The documents disagree: " + "; ".join(clashes) + ".")
    if checked:
        return Check("doc_patient", "Same patient throughout", "ok",
                     "Age and sex agree across the documents and the claim.")
    return None


def dates_check(reading: "Reading") -> Check | None:
    by_type = {d.type: _day(d.date) for d in reading.documents if _day(d.date)}
    treated = by_type.get("bill")
    if treated is None:
        return None
    approved = by_type.get("pre_auth_letter")
    if approved and approved > treated:
        return Check("doc_dates", "Dates agree", "issue",
                     f"The approval is dated {approved:%d %b %Y}, after the treatment on "
                     f"{treated:%d %b %Y}. Pre-authorisation has to come first.")
    admitted = by_type.get("discharge_summary")
    if admitted and abs((admitted - treated).days) > DATE_SLACK_DAYS:
        return Check("doc_dates", "Dates agree", "warn",
                     f"The discharge summary is dated {admitted:%d %b %Y}, the bill "
                     f"{treated:%d %b %Y}. Check that they are for the same stay.")
    if approved or admitted:
        compared = " and ".join(w for w, d in (("approval", approved), ("discharge", admitted)) if d)
        return Check("doc_dates", "Dates agree", "ok",
                     f"The treatment and {compared} dates are consistent.")
    return None


def document_checks(answers: dict, derived: dict, reading: "Reading") -> list[Check]:
    checks = [required_documents_check(answers, derived, reading),
              approved_amount_check(answers, reading),
              same_patient_check(answers, reading),
              dates_check(reading)]
    return [c for c in checks if c is not None]


def blocks_submission(check: Check) -> bool:
    """Whether this check keeps a claim from being called ready to submit: any problem,
    and a required paper missing from the uploads (a warning, since the clinic may hold
    it, but the claim is not ready until it is attached)."""
    return check.status == "issue" or (check.id == "doc_required" and check.status == "warn")
