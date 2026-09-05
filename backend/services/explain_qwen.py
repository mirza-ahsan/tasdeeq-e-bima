"""Part C — plain-language explanation layer, powered by Qwen via DashScope.

Contract with the rest of the system:
    Qwen NARRATES a result that the LightGBM models already computed.
    It never invents a probability, a rejection reason, a CARC code, or a field.

Everything Qwen is allowed to say is passed to it explicitly in the payload.
If the call fails for any reason, we fall back to a deterministic template so a
live demo never breaks on stage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Literal

from openai import OpenAI

from backend.config import DashScopeConfig, load_dashscope_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data carried between the model layer and the explanation layer
# ---------------------------------------------------------------------------


@dataclass
class FlaggedField:
    """One field the risk model blamed, straight from SHAP."""

    label: str  # human-readable, e.g. "Pre-authorisation"
    value: str  # the answer given, e.g. "Not obtained"
    effect: Literal["increases_risk", "reduces_risk"]
    contribution_points: float  # signed SHAP contribution, in percentage points


@dataclass
class ClaimPrediction:
    """The complete, already-computed result. This is the ONLY thing Qwen sees."""

    rejection_probability: float  # 0.0 - 1.0, computed and calibrated by the model
    risk_band: Literal["low", "medium", "high"]
    carc_code: str  # real X12 code, e.g. "197"
    carc_description: str  # official X12 wording for that code
    flagged_fields: list[FlaggedField] = field(default_factory=list)

    def to_payload(self) -> dict:
        return {
            "rejection_probability_percent": round(self.rejection_probability * 100),
            "risk_band": self.risk_band,
            "carc_code": self.carc_code,
            "carc_description": self.carc_description,
            "flagged_fields": [asdict(f) for f in self.flagged_fields],
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
ALREADY been calculated by a separate statistical model. Your only job is to restate \
that result in plain English for clinic reception staff who are not insurance experts.

Rules you must never break:
1. Never invent, recalculate, round differently, or hedge the rejection probability. \
Use the number exactly as given.
2. Never invent a rejection reason or a CARC code. Refer only to the CARC code and \
description provided to you.
3. Never mention any claim field that is not present in flagged_fields.
4. Never give medical advice and never comment on whether the treatment was appropriate.
5. If something is not in the JSON, say nothing about it. Do not guess.
6. Never tell staff to fix something that is already correct. A field whose effect is \
"reduces_risk" is working in the claim's favour — it is not a problem to solve.

Write exactly two sentences.

If at least one field has effect "increases_risk":
- Sentence 1: state the rejection risk and name the single field that is driving it.
- Sentence 2: give the one concrete action the staff member should take before submitting.

If no field has effect "increases_risk", the claim looks clean:
- Sentence 1: say the rejection risk is low and name the strongest field supporting that.
- Sentence 2: say the claim looks ready to submit. Do not invent a task.

Plain English. No jargon, no markdown, no bullet points, no preamble, no greeting. \
Maximum 45 words total."""


def _build_client(config: DashScopeConfig) -> OpenAI:
    """OpenAI-compatible client pointed at DashScope.

    Workspace-scoped keys additionally require the X-DashScope-WorkSpace header,
    which the OpenAI SDK carries via default_headers.
    """
    headers: dict[str, str] = {}
    if config.workspace_id:
        headers["X-DashScope-WorkSpace"] = config.workspace_id

    return OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        default_headers=headers or None,
        timeout=20.0,
        max_retries=2,
    )


def _fallback_text(prediction: ClaimPrediction) -> str:
    """Deterministic template used when Qwen is unavailable."""
    pct = round(prediction.rejection_probability * 100)
    drivers = [f for f in prediction.flagged_fields if f.effect == "increases_risk"]
    if drivers:
        top = drivers[0]
        return (
            f"This claim has a {pct}% chance of rejection, driven mainly by "
            f"{top.label.lower()} ({top.value}). "
            f"Likely reason CARC {prediction.carc_code}: {prediction.carc_description} "
            f"— resolve this before submitting."
        )
    return (
        f"This claim has a {pct}% chance of rejection. "
        f"Closest matching reason is CARC {prediction.carc_code}: "
        f"{prediction.carc_description}."
    )


def explain(
    prediction: ClaimPrediction,
    config: DashScopeConfig | None = None,
) -> Explanation:
    """Turn a computed prediction into 1-2 sentences of plain-language advice."""
    config = config or load_dashscope_config()

    if not config.is_configured:
        logger.warning("DASHSCOPE_API_KEY is not set; using template fallback.")
        return Explanation(text=_fallback_text(prediction), source="fallback")

    try:
        client = _build_client(config)
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(prediction.to_payload(), ensure_ascii=False),
                },
            ],
            temperature=0.2,
            max_tokens=160,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("Qwen returned an empty message")
        return Explanation(text=text, source="qwen", model=config.model)

    except Exception as exc:  # noqa: BLE001 — demo must never hard-fail here
        # str(exc) from the OpenAI SDK reports status/message, not the API key.
        logger.warning("Qwen explanation failed (%s); using template fallback.", type(exc).__name__)
        logger.debug("Qwen failure detail: %s", exc)
        return Explanation(text=_fallback_text(prediction), source="fallback")
