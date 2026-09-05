#!/usr/bin/env python3
"""Standalone end-to-end check for the DashScope / Qwen connection.

Run this BEFORE wiring Qwen into the adaptive question flow:

    uv run python scripts/test_dashscope.py

Three checks:
  1. All four environment variables resolve (key presence only — never its value).
  2. A raw chat completion reaches the endpoint and comes back.
  3. The real explanation layer produces advice from a sample prediction.

This script never prints, logs, or echoes the API key.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running directly as `python scripts/test_dashscope.py` from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import load_dashscope_config
from backend.services.explain_qwen import (
    ClaimPrediction,
    FlaggedField,
    _build_client,
    explain,
)

SAMPLE = ClaimPrediction(
    rejection_probability=0.72,
    risk_band="high",
    carc_code="197",
    carc_description="Precertification/authorization/notification/pre-treatment absent.",
    flagged_fields=[
        FlaggedField(
            label="Pre-authorisation",
            value="Not obtained",
            effect="increases_risk",
            contribution_points=31.4,
        ),
        FlaggedField(
            label="Days since treatment",
            value="46 days",
            effect="increases_risk",
            contribution_points=12.1,
        ),
        FlaggedField(
            label="Documents complete",
            value="Yes",
            effect="reduces_risk",
            contribution_points=-6.8,
        ),
    ],
)


def main() -> int:
    print("=" * 62)
    print("DashScope / Qwen connectivity check")
    print("=" * 62)

    config = load_dashscope_config()

    print("\n[1/3] Environment variables")
    for name, value in config.status().items():
        print(f"      {name:<24} {value}")

    if not config.is_configured:
        print("\n  FAIL: DASHSCOPE_API_KEY is not set.")
        print("  Copy .env.example to .env and fill in your key, then re-run.")
        return 1

    print("\n[2/3] Raw chat completion")
    try:
        client = _build_client(config)
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "user", "content": "Reply with the single word: OK"}
            ],
            max_tokens=10,
            temperature=0.0,
        )
        reply = (response.choices[0].message.content or "").strip()
        usage = response.usage
        print(f"      model    : {response.model}")
        print(f"      reply    : {reply!r}")
        if usage:
            print(f"      tokens   : {usage.prompt_tokens} in / {usage.completion_tokens} out")
        print("      PASS")
    except Exception as exc:  # noqa: BLE001
        print(f"      FAIL: {type(exc).__name__}: {exc}")
        print("\n  Common causes:")
        print("   - DASHSCOPE_BASE_URL region does not match where the key was issued")
        print("     (intl:     https://dashscope-intl.aliyuncs.com/compatible-mode/v1)")
        print("     (mainland: https://dashscope.aliyuncs.com/compatible-mode/v1)")
        print("   - Key is workspace-scoped but DASHSCOPE_WORKSPACE_ID is blank")
        print(f"   - Model {config.model!r} not enabled on this account")
        return 1

    print("\n[3/3] Explanation layer (strict prompt, sample prediction)")
    result = explain(SAMPLE, config=config)
    print(f"      source   : {result.source}")
    print(f"      model    : {result.model or '-'}")
    print(f"      text     : {result.text}")
    if result.source == "fallback":
        print("      WARN: fell back to the template — Qwen call did not succeed.")
        return 1
    print("      PASS")

    print("\n" + "=" * 62)
    print("All checks passed. Qwen is ready to wire into the adaptive flow.")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
