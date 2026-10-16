#!/usr/bin/env python3
"""Download the Synthea sample bundle the published model was built from, and verify it.

    uv run python scripts/fetch_synthea.py

Synthea only publishes this bundle under a "latest" URL, so the file behind it can change
at any time. The SHA-256 below is the bundle the committed dataset and models were built
from; a different download is refused rather than silently producing different numbers.
Only needed to rebuild the dataset — the trained artefacts and data/processed/claims.parquet
are committed.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"

URL = ("https://synthetichealth.github.io/synthea-sample-data/downloads/latest/"
       "synthea_sample_data_csv_latest.zip")
SHA256 = "d61417b551e5b0997c33851b339c157421751f0ea68c18ea686ceb1850907c35"


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    archive = RAW / "synthea.zip"
    print(f"Downloading {URL}")
    with urllib.request.urlopen(URL, timeout=120) as response:  # noqa: S310 — fixed https URL
        archive.write_bytes(response.read())

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != SHA256:
        archive.unlink()
        print(f"ERROR: bundle checksum {digest} does not match the pinned {SHA256}.\n"
              "       Synthea has republished its sample data, so a rebuild would not\n"
              "       reproduce the committed models. The committed artefacts still work.",
              file=sys.stderr)
        return 1

    with zipfile.ZipFile(archive) as z:
        for member in z.namelist():
            # Flat CSVs only; refuse anything that would land outside data/raw.
            name = Path(member).name
            if not name.endswith(".csv") or name != member:
                continue
            (RAW / name).write_bytes(z.read(member))
    archive.unlink()
    print(f"Verified and extracted to {RAW.relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
