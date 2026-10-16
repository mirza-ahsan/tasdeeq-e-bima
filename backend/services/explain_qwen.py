"""Plain-language explanation layer, powered by Qwen via DashScope.

Contract with the rest of the system:
    Qwen NARRATES a result that the LightGBM models and the rule checks already computed.
    It never invents a probability, a rejection reason, a CARC code, or a field.

Everything Qwen is allowed to say is passed to it explicitly in the payload, and what it
says back is checked before anyone sees it: every number in the reply must appear in the
payload, the stated risk must be the computed one, and the reply must stay two short
sentences. Anything else — a failed call, a timeout, a reply that breaks a rule, or the
per-minute call budget being spent — falls back to a deterministic template, so a live
demo never breaks on stage and a bad generation never reaches a clerk.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Literal

from openai import OpenAI

from backend.config import DashScopeConfig, load_dashscope_config

logger = logging.getLogger(__name__)

# One attempt plus one retry at 8 s each stays well inside nginx's 60 s proxy timeout, so
# a stalled connection still reaches the fallback instead of surfacing as a 504.
TIMEOUT_S = 8.0
MAX_RETRIES = 1
# Every result view could otherwise trigger a fresh, billed call. Results are cached per
# claim state upstream; this caps what is left, across all users.
CALLS_PER_MINUTE = 30

MAX_WORDS = 60

# ---------------------------------------------------------------------------
# Data carried between the model layer and the explanation layer
# ---------------------------------------------------------------------------


@dataclass
class FlaggedField:
    """One field the risk model blamed, from the Shapley attribution."""

    label: str  # human-readable, e.g. "Pre-authorisation"
    value: str  # the answer given, e.g. "No, not obtained"
    effect: Literal["increases_risk", "reduces_risk"]
    contribution_points: float  # signed, in percentage points of the probability


@dataclass
class ClaimPrediction:
    """The complete, already-computed result. This is the ONLY thing Qwen sees."""

    rejection_probability: float  # 0.0 - 1.0, computed and calibrated by the model
    risk_band: Literal["low", "medium", "high"]
    carc_code: str | None  # real X12 code, e.g. "197"; None when no reason is shown
    carc_reason: str | None  # our plain-English reading of that code
    staff_action: str | None  # what to do about it
    flagged_fields: list[FlaggedField] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # failed rule checks, already worded

    @property
    def percent(self) -> int:
        return round(self.rejection_probability * 100)

    def to_payload(self) -> dict:
        return {
            "rejection_probability_percent": self.percent,
            "risk_band": self.risk_band,
            "likely_rejection_reason": (
                {"carc_code": self.carc_code, "reason": self.carc_reason,
                 "staff_action": self.staff_action}
                if self.carc_code else None),
            "rule_check_issues": self.issues,
            "flagged_fields": [
                {**asdict(f), "contribution_points": round(f.contribution_points, 1)}
                for f in self.flagged_fields],
        }


@dataclass
class Explanation:
    text: str
    source: Literal["qwen", "fallback"]
    model: str | None = None


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a claims-desk assistant for a private clinic in Pakistan.

You will receive a JSON object describing a claim-rejection risk result that has \
ALREADY been calculated by a separate statistical model and a set of insurer rule \
checks. Your only job is to restate that result in plain English for clinic reception \
staff who are not insurance experts.

Rules you must never break:
1. State the rejection risk exactly as rejection_probability_percent, written with a \
percent sign (for example "62%"). Never recalculate, round differently, or describe it \
as a fraction or in words.
2. Never use any other number unless it appears, digit for digit, in the JSON.
3. Never invent a rejection reason or a CARC code. Refer only to likely_rejection_reason \
and rule_check_issues. Do not mention CARC codes by number.
4. Never mention any claim field that is not present in flagged_fields or rule_check_issues.
5. Never give medical advice and never comment on whether the treatment was appropriate.
6. Never tell staff to fix something that is already correct. A field whose effect is \
"reduces_risk" is working in the claim's favour — it is not a problem to solve.

Write exactly two sentences, at most 45 words in total.

If risk_band is "high" or "medium" and there is a likely_rejection_reason or a \
rule_check_issue or a flagged field with effect "increases_risk":
- Sentence 1: state the rejection risk and the single main reason for it.
- Sentence 2: give the one concrete action staff should take before submitting, \
taken from staff_action when it is present.

If risk_band is "medium" and nothing increases the risk:
- Sentence 1: state the risk and that nothing stands out as a problem.
- Sentence 2: say a quick review before submitting is still worthwhile, because not \
every common rejection reason has been ruled out.

If risk_band is "low":
- Sentence 1: say the rejection risk is low, give the percentage, and name the \
strongest field supporting that if there is one.
- Sentence 2: say the claim looks ready to submit. Do not invent a task.

Plain English. No jargon, no markdown, no bullet points, no preamble, no greeting."""


@lru_cache(maxsize=4)
def _client(api_key: str, base_url: str, workspace_id: str | None) -> OpenAI:
    """OpenAI-compatible client pointed at DashScope, built once per configuration.

    Workspace-scoped keys additionally require the X-DashScope-WorkSpace header,
    which the OpenAI SDK carries via default_headers.
    """
    headers = {"X-DashScope-WorkSpace": workspace_id} if workspace_id else None
    return OpenAI(api_key=api_key, base_url=base_url, default_headers=headers,
                  timeout=TIMEOUT_S, max_retries=MAX_RETRIES)


def _build_client(config: DashScopeConfig) -> OpenAI:
    return _client(config.api_key, config.base_url, config.workspace_id)


class _CallBudget:
    """Sliding one-minute window across all callers."""

    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def take(self) -> bool:
        now = time.monotonic()
        with self.lock:
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.per_minute:
                return False
            self.calls.append(now)
            return True


_budget = _CallBudget(CALLS_PER_MINUTE)

# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Spelled-out quantities would slip past a digit check ("two-thirds", "sixty percent").
_NUMBER_WORDS = re.compile(
    r"\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|half|halves|thirds?|quarters?|"
    r"percent|per cent)\b", re.IGNORECASE)


def _numbers_in(text: str) -> set[str]:
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUMBER.finditer(text)}


def validate(text: str, prediction: ClaimPrediction) -> str | None:
    """Why `text` may not be shown, or None if it keeps every rule we can check."""
    payload = json.dumps(prediction.to_payload(), ensure_ascii=False)
    allowed = _numbers_in(payload) | {str(prediction.percent)}
    stray = _numbers_in(text) - allowed
    if stray:
        return f"states numbers not in the result: {sorted(stray)}"
    if f"{prediction.percent}%" not in text.replace(" %", "%"):
        return "does not state the computed risk"
    if _NUMBER_WORDS.search(text):
        return "describes a quantity in words"
    if len(text.split()) > MAX_WORDS:
        return "too long"
    if "*" in text or "#" in text or "\n-" in text:
        return "contains formatting"
    return None


# ---------------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------------


def _sentence(s: str) -> str:
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


def fallback_text(prediction: ClaimPrediction) -> str:
    """Deterministic two-sentence explanation in the same plain English as the prompt."""
    pct = prediction.percent
    drivers = [f for f in prediction.flagged_fields if f.effect == "increases_risk"]
    helpers = [f for f in prediction.flagged_fields if f.effect == "reduces_risk"]

    if prediction.risk_band == "low":
        support = f", helped most by {helpers[0].label.lower()} ({helpers[0].value})" if helpers else ""
        return f"The rejection risk is low at {pct}%{support}. The claim looks ready to submit."

    if prediction.carc_reason:
        reason = prediction.carc_reason.rstrip(".")
        reason = reason[0].lower() + reason[1:]
        action = prediction.staff_action or "Resolve this before submitting."
        return f"This claim has a {pct}% chance of rejection, most likely because {reason}. {_sentence(action)}"

    if prediction.issues:
        issue = prediction.issues[0].rstrip(".")
        return (f"This claim has a {pct}% chance of rejection. The main concern: "
                f"{issue[0].lower() + issue[1:]}; resolve it before submitting.")

    if drivers:
        top = drivers[0]
        return (f"This claim has a {pct}% chance of rejection, driven mainly by "
                f"{top.label.lower()} ({top.value}). Confirm that detail before submitting.")

    return (f"The rejection risk is {pct}%, and nothing stands out as a problem. A quick "
            "review before submitting is still worthwhile, because not every common "
            "rejection reason has been ruled out.")


def explain(
    prediction: ClaimPrediction,
    config: DashScopeConfig | None = None,
) -> Explanation:
    """Turn a computed prediction into two sentences of plain-language advice."""
    config = config or load_dashscope_config()

    if not config.is_configured:
        logger.info("DASHSCOPE_API_KEY is not set; using template fallback.")
        return Explanation(text=fallback_text(prediction), source="fallback")
    if not _budget.take():
        logger.warning("Qwen call budget spent for this minute; using template fallback.")
        return Explanation(text=fallback_text(prediction), source="fallback")

    try:
        response = _build_client(config).chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": json.dumps(prediction.to_payload(), ensure_ascii=False)},
            ],
            temperature=0.2,
            max_tokens=160,
        )
        text = " ".join((response.choices[0].message.content or "").split())
        problem = validate(text, prediction) if text else "empty reply"
        if problem:
            logger.warning("Qwen reply rejected (%s); using template fallback.", problem)
            return Explanation(text=fallback_text(prediction), source="fallback")
        return Explanation(text=text, source="qwen", model=config.model)

    except Exception as exc:  # noqa: BLE001 — demo must never hard-fail here
        # str(exc) from the OpenAI SDK reports status/message, not the API key.
        logger.warning("Qwen explanation failed (%s); using template fallback.", type(exc).__name__)
        logger.debug("Qwen failure detail: %s", exc)
        return Explanation(text=fallback_text(prediction), source="fallback")
