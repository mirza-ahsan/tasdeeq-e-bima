#!/usr/bin/env python3
"""Render the sample claim documents the demo reads.

    uv run python scripts/make_samples.py

Writes frontend/public/samples/<set>/<document>.jpg from the HTML below, through headless
Chromium. Everything on them is fictional — clinic, patient, insurer, reference numbers —
and every page is watermarked SAMPLE so nobody mistakes one for a real record.

Dates are set relative to the day this runs, because the check measures days since
treatment against the insurer's filing window. Nigheban Health Board allows 90 days, so a
set rendered today stays inside the window for weeks; re-run this before a demo if it has
been a while.

The sets are built to show specific behaviour:
  surgery-no-approval  a bill and a discharge summary, no pre-authorisation letter: the
                       check asks for approval and flags it as missing from the papers
  surgery-approved     the same surgery with an approval letter, but for less than was
                       billed: the cross-document check catches the gap
"""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "frontend" / "public" / "samples"
WIDTH, HEIGHT = 1000, 1300

TODAY = date.today()
TREATED = TODAY - timedelta(days=12)
DISCHARGED = TREATED + timedelta(days=1)
APPROVED = TREATED - timedelta(days=4)


def d(day: date) -> str:
    return day.strftime("%d-%b-%Y")


CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { width: 1000px; height: 1300px; padding: 64px 72px; font: 17px/1.5 "DejaVu Sans", Arial,
       sans-serif; color: #1d2421; background: #fff; position: relative; overflow: hidden; }
.mark { position: absolute; top: 560px; left: 80px; transform: rotate(-28deg); font-size: 150px;
        font-weight: 700; color: rgba(200, 30, 30, .07); letter-spacing: 12px; }
header { display: flex; justify-content: space-between; border-bottom: 3px solid #1f5c4d;
         padding-bottom: 18px; }
.org { font-size: 26px; font-weight: 700; color: #1f5c4d; }
.sub { font-size: 14px; color: #5b6662; }
h1 { font-size: 22px; letter-spacing: 3px; margin: 34px 0 22px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px 40px; margin-bottom: 28px; }
.grid div span { color: #5b6662; }
table { width: 100%; border-collapse: collapse; margin-top: 8px; }
th, td { text-align: left; padding: 11px 8px; border-bottom: 1px solid #d9dedb; }
th { font-size: 14px; color: #5b6662; font-weight: 600; }
td.r, th.r { text-align: right; }
tr.total td { font-weight: 700; border-top: 2px solid #1d2421; border-bottom: none; }
.note { margin-top: 28px; padding: 14px 18px; background: #f3f5f4; font-size: 15px; }
.sign { margin-top: 60px; font-size: 15px; color: #5b6662; }
footer { position: absolute; bottom: 40px; left: 72px; right: 72px; font-size: 12px;
         color: #8a9490; border-top: 1px solid #d9dedb; padding-top: 10px; }
"""

CLINIC = """<header><div><div class="org">Al-Noor Family Clinic</div>
<div class="sub">Model Town, Lahore · Day-care surgery and outpatients</div></div>
<div class="sub" style="text-align:right">Panel provider<br>Nigheban Health Board</div></header>"""

FOOT = "<footer>SAMPLE DOCUMENT — fictional clinic, patient and insurer, made for the Claim Check demo.</footer>"


def page(body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body><div class='mark'>SAMPLE</div>{body}{FOOT}</body></html>")


PATIENT = """<div><span>Patient:</span> Sana Tariq</div>
<div><span>Age / Sex:</span> 44 years / Female</div>"""

BILL = page(f"""{CLINIC}
<h1>ITEMISED BILL</h1>
<div class="grid">
{PATIENT}
<div><span>Invoice no.:</span> ANC-4471</div>
<div><span>Date of service:</span> {d(TREATED)}</div>
<div><span>Insurer:</span> Nigheban Health Board</div>
<div><span>Plan:</span> Standard</div>
<div><span>Panel status:</span> In network</div>
<div><span>Admission type:</span> Elective</div>
</div>
<div><span style="color:#5b6662">Diagnosis:</span> Gallstones (cholelithiasis)</div>
<table>
<tr><th>Item</th><th class="r">Amount (PKR)</th></tr>
<tr><td>Laparoscopic cholecystectomy — surgeon's fee</td><td class="r">48,000</td></tr>
<tr><td>Operating theatre and anaesthesia</td><td class="r">22,000</td></tr>
<tr><td>Day-care bed, nursing and medicines</td><td class="r">15,000</td></tr>
<tr class="total"><td>TOTAL</td><td class="r">85,000</td></tr>
</table>
<div class="note">Pre-authorisation reference: ____________</div>
<div class="sign">Billing desk, Al-Noor Family Clinic</div>""")

DISCHARGE = page(f"""{CLINIC}
<h1>DISCHARGE SUMMARY</h1>
<div class="grid">
{PATIENT}
<div><span>Admitted:</span> {d(TREATED)}</div>
<div><span>Discharged:</span> {d(DISCHARGED)}</div>
<div><span>Admission type:</span> Elective</div>
<div><span>Consultant:</span> General surgery</div>
</div>
<p><b>Diagnosis:</b> Symptomatic gallstones (cholelithiasis).</p>
<p style="margin-top:14px"><b>Procedure:</b> Laparoscopic cholecystectomy under general
anaesthesia. Uneventful recovery.</p>
<p style="margin-top:14px"><b>Condition at discharge:</b> Stable. Tolerating diet, wounds clean.</p>
<p style="margin-top:14px"><b>Follow-up:</b> Surgical outpatients in 10 days.</p>
<div class="sign">Signed: Consultant surgeon, Al-Noor Family Clinic</div>""")

APPROVAL = page(f"""<header><div><div class="org">Nigheban Health Board</div>
<div class="sub">Pre-authorisation unit · Lahore</div></div>
<div class="sub" style="text-align:right">Approval ref.<br><b style="color:#1d2421">NHB-PA-48213</b></div></header>
<h1>PRE-AUTHORISATION APPROVAL</h1>
<div class="grid">
{PATIENT}
<div><span>Provider:</span> Al-Noor Family Clinic</div>
<div><span>Plan:</span> Standard</div>
<div><span>Date of approval:</span> {d(APPROVED)}</div>
<div><span>Valid until:</span> {d(APPROVED + timedelta(days=30))}</div>
</div>
<table>
<tr><th>Approved procedure</th><th class="r">Approved amount (PKR)</th></tr>
<tr><td>Laparoscopic cholecystectomy (elective)</td><td class="r">70,000</td></tr>
</table>
<div class="note">Charges above the approved amount are not covered unless a revised
approval is issued before treatment.</div>
<div class="sign">Authorised officer, Pre-authorisation unit</div>""")

SETS = {
    "surgery-no-approval": {"bill": BILL, "discharge-summary": DISCHARGE},
    "surgery-approved": {"bill": BILL, "pre-auth-approval": APPROVAL},
}


def render(html: str, chrome: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src, png = Path(tmp) / "doc.html", Path(tmp) / "doc.png"
        src.write_text(html)
        subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                        f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={png}", src.as_uri()],
                       check=True, capture_output=True)
        out = io.BytesIO()
        Image.open(png).convert("RGB").save(out, "JPEG", quality=82, optimize=True)
        return out.getvalue()


def main() -> int:
    chrome = next((c for c in ("chromium", "chromium-browser", "google-chrome") if shutil.which(c)), None)
    if not chrome:
        print("chromium not found")
        return 1
    for name, docs in SETS.items():
        folder = OUT / name
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("*.jpg"):
            old.unlink()
        for doc, html in docs.items():
            path = folder / f"{doc}.jpg"
            path.write_bytes(render(html, chrome))
            print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB")
    print(f"  treated {d(TREATED)} ({(TODAY - TREATED).days} days ago)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
