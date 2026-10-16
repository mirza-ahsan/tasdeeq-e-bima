#!/usr/bin/env python3
"""Fail if a fresh rebuild no longer reproduces the committed data and metrics.

    uv run python scripts/check_drift.py --baseline /path/to/committed/metadata.json

Run after the pipeline (fetch, build, label, train) in a disposable checkout, with the
committed metadata.json copied aside first as the baseline. Two gates:

  * the labelled dataset must match the committed one exactly — every step that builds it
    is deterministic, so any difference is a real change;
  * the headline metrics must match within a tolerance. LightGBM is bit-reproducible on
    one platform but not promised across CPUs and compilers, so a few thousandths of AUC
    of drift is allowed; the old unstable sort moved it by seven hundredths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

TOLERANCE = {
    "test_auc_full": 0.01,
    "test_auc_partial": 0.01,
    "test_accuracy_full": 0.02,
    "carc_top1": 0.03,
}
VERDICT_TOLERANCE = 0.05   # share of claims per band


def frame_digest(df: pd.DataFrame) -> str:
    return hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()


def committed_dataset() -> pd.DataFrame:
    blob = subprocess.run(["git", "show", "HEAD:data/processed/claims.parquet"],
                          cwd=ROOT, capture_output=True, check=True).stdout
    tmp = ROOT / "data" / "processed" / ".committed_claims.parquet"
    tmp.write_bytes(blob)
    try:
        return pd.read_parquet(tmp)
    finally:
        tmp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", type=Path, required=True,
                        help="the committed models/metadata.json, copied aside before training")
    args = parser.parse_args()

    failures = []
    rebuilt = pd.read_parquet(ROOT / "data" / "processed" / "claims.parquet")
    if frame_digest(rebuilt) != frame_digest(committed_dataset()):
        failures.append("data/processed/claims.parquet differs from the committed dataset")

    base = json.loads(args.baseline.read_text())
    new = json.loads((ROOT / "models" / "metadata.json").read_text())
    for key, tol in TOLERANCE.items():
        a, b = base["metrics"][key], new["metrics"][key]
        status = "ok" if abs(a - b) <= tol else "DRIFT"
        print(f"  {key:<22} committed {a:.4f}  rebuilt {b:.4f}  ({status}, tolerance {tol})")
        if status != "ok":
            failures.append(f"{key} moved {b - a:+.4f}")
    for band in ("low", "medium", "high"):
        a = base["product_test"]["bands"][band]["share"]
        b = new["product_test"]["bands"][band]["share"]
        if abs(a - b) > VERDICT_TOLERANCE:
            failures.append(f"{band}-band share moved {b - a:+.3f}")
    if base["risk_bands"] != new["risk_bands"]:
        failures.append(f"risk bands changed: {base['risk_bands']} -> {new['risk_bands']}")

    if failures:
        print("\nDRIFT: the rebuild does not reproduce the committed results:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    print("\nReproduced: dataset identical, metrics within tolerance.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
