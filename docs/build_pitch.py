"""Build the pitch deck: business case first, then the product, as one PDF.

    uv run python docs/build_pitch.py

docs/build_deck.py is the technical deck. This one is for the stage: few words per slide,
the problem and the business case before the product, and a numbered source for every
outside figure so a judge can check it. Our own figures are read from models/metadata.json
at build time, like the technical deck, so the two cannot disagree with the model. Outside
figures were checked against the cited pages on 2026-10-17; re-check before reusing them.
Rendered as HTML through headless Chromium. The fonts (Geist, Instrument Serif; both OFL)
are vendored in docs/fonts: loading them from Google Fonts silently fell back to a system
serif whenever the request was slow, and the headlines no longer fit.
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent

SOURCES = [
    ("Premier Inc., “Claims Adjudication Costs Providers $25.7 Billion”, Feb 2025 (2023 data)",
     "premierinc.com/newsroom/blog/claims-adjudication-costs-providers-25-7-billion"),
    ("Becker’s Hospital Review, on the Change Healthcare 2020 Revenue Cycle Denials Index "
     "(102M claims, 1,500 hospitals)",
     "beckershospitalreview.com/finance/86-of-denials-are-potentially-avoidable-strategies-to-better-prevent-manage-denials"),
    ("Outlook Business, on the IRDAI Annual Report 2023-24, 30 Dec 2024",
     "outlookbusiness.com/economy-and-policy/health-insurers-disallowed-claims-worth-rs-15100-crore-during-fy24"),
    ("Jubilee Insurance Kenya, CoverBora brochure (pre-authorisation terms)",
     "jubileeinsurance.com/ke/wp-content/uploads/2024/01/CoverBora-Brochure.pdf"),
    ("Business Daily Africa, “Jubilee Insurance, Nairobi Hospital in debt row”, June 2020",
     "businessdailyafrica.com/bd/news/jubilee-insurance-nairobi-hospital-in-debt-row-2293534"),
]


def pct(x: float) -> str:
    return f"{x:.0%}"


def e(s: str) -> str:
    return html.escape(s, quote=False)


def ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def stat(value: str, label: str, ref: str = "") -> str:
    sup = f"<sup>{ref}</sup>" if ref else ""
    return f'<div class="stat"><div class="v">{value}</div><div class="l">{label}{sup}</div></div>'


def fact(where: str, value: str, label: str, ref: str) -> str:
    return (f'<div class="fact"><div class="k">{where}</div><div class="v">{value}</div>'
            f'<div class="l">{label}<sup>{ref}</sup></div></div>')


def cite(*refs: int) -> str:
    return "Sources: " + " · ".join(f"[{r}] {e(SOURCES[r - 1][0].split(',')[0])}" for r in refs)


def slides() -> list[dict]:
    meta = json.loads((ROOT / "models" / "metadata.json").read_text())
    m, v = meta["metrics"], meta["product_test"]
    b = v["bands"]
    base = meta["base_rejection_rate"]

    return [
        dict(kind="title", body="""
            <div class="eyebrow">Bano Qabil AI Hackathon</div>
            <h1 class="hero">Claim Check</h1>
            <p class="lede">Catch the insurance-claim rejection <em>before</em> the claim is sent.</p>
            <p class="team">Mirza Ahsan Baig · Safee Akmal<span>Tasdeeq-e-Bima</span></p>
            """, image="landing.png"),

        # --- the problem ---------------------------------------------------------
        dict(eyebrow="The problem", title="Claims get rejected for paperwork, not medicine",
             body=f"""
            <p class="lede-row">No pre-authorisation · missing documents · missed deadlines</p>
            <div class="stats">
              {stat("15%", "of US private-insurer claims denied first time", "1")}
              {stat("$25.7B", "a year spent by US providers fighting denials", "1")}
              {stat("86%", "of denials potentially avoidable", "2")}
              {stat("71%", "of India’s health-claim value actually paid", "3")}
            </div>
            <p class="note">Small clinics wait weeks, rework the claim — or stop taking insured patients.</p>""",
             sources=[1, 2, 3]),

        dict(eyebrow="The problem · Case study", title="Jubilee Health Insurance, Kenya", body="""
            <table class="map">
              <tr><th>A claim can be rejected for…</th><th>Claim Check catches it before sending</th></tr>
              <tr><td>No pre-authorisation (due 48 h before admission)<sup>4</sup></td><td>Asked first, every time</td></tr>
              <tr><td>Incomplete documents</td><td>Flags missing papers</td></tr>
              <tr><td>Discharge summary doesn’t match the approval</td><td>Cross-checks the papers</td></tr>
              <tr><td>Clinic not on the plan’s panel</td><td>Asks panel status</td></tr>
              <tr><td>Service not covered by the plan</td><td>Plan-coverage check</td></tr>
              <tr><td>Filed too late</td><td>Filing-deadline check</td></tr>
            </table>
            <p class="note">2020: Nairobi Hospital suspended Jubilee over a claimed <b>KSh 357 million</b>
            in unpaid claims.<sup>5</sup></p>""", sources=[4, 5]),

        # --- the solution --------------------------------------------------------
        dict(eyebrow="The solution", title="A spell-checker for insurance claims", body="""
            <div class="steps">
              <div><span>1</span><b>Photograph the papers</b><p>Qwen reads them; the clerk confirms</p></div>
              <div><span>2</span><b>Answer 3–4 questions</b><p>Only what the papers can’t settle</p></div>
              <div><span>3</span><b>Fix before sending</b><p>Risk, the cause, the fix, a printout</p></div>
            </div>""", image="documents.png"),

        dict(eyebrow="The solution · Why trust it", title="Qwen reads and explains. The model decides.",
             body=f"""
            <div class="flow">
              <div class="card accent"><div class="k">Qwen-VL reads</div><p>Every value shown with its source</p></div>
              <div class="card"><div class="k">Model + rules decide</div><p>Risk, reason code, checks</p></div>
              <div class="card accent"><div class="k">Qwen explains</div><p>One sentence, verified</p></div>
            </div>
            <div class="stats three">
              {stat(pct(1 - b['low']['rejection_rate']), "of claims it cleared were accepted")}
              {stat(pct(v['rejections_flagged']), "of rejections caught before sending")}
              {stat(f"{v['mean_questions']:.1f}", "questions per claim")}
            </div>
            <p class="note small">Measured on {v['claims']} simulated claims the model never saw.</p>"""),

        # --- the business ---------------------------------------------------------
        dict(eyebrow="The business · Who gains", title="Everyone in the claim wins", body=f"""
            <div class="cols">
              <div class="card"><div class="k">Clinics</div>{ul(["Paid sooner", "Less rework", "Steady cash flow"])}</div>
              <div class="card"><div class="k">Insurers &amp; TPAs</div>{ul(["Cleaner claims", "Fewer appeals", "Lower admin cost"])}</div>
              <div class="card"><div class="k">Patients</div>{ul(["Clinics keep accepting insurance", "No paperwork delays"])}</div>
            </div>
            <p class="note">Costs little to run: a small model on an ordinary server, a few short Qwen calls per claim.</p>"""),

        dict(eyebrow="The business · How it earns", title="Two revenue streams", body="""
            <div class="cols two">
              <div class="card accent"><div class="k">TPA licence</div>{tpa}</div>
              <div class="card"><div class="k">Clinic subscription</div>{clinic}</div>
            </div>
            <p class="note"><b>Next:</b> a 3-month pilot with a TPA or clinic group — rejections measured
            before and after.</p>""".replace("{tpa}", ul([
                "One contract covers every clinic on the panel",
                "Built into their portal, with their own rules",
                "Fee per claim checked, or per panel",
            ])).replace("{clinic}", ul([
                "Free: a few checks a month",
                "Paid: about PKR 3,000–5,000 a month (proposed)",
                "Costs less than one rejected claim",
            ]))),

        dict(eyebrow="References", title="Sources", body="<ol class='refs'>" + "".join(
            f"<li>{e(t)}<br><span>{e(u)}</span></li>" for t, u in SOURCES) + "</ol>"
            + f"<p class='note small'>Our results: models/metadata.json and docs/model-card.md in the project "
              f"repository ({meta['n_claims']:,} simulated claims; {v['claims']} held out for testing). "
              "Prices are proposals to be tested in a pilot.</p>"),
    ]

CSS = """
@font-face { font-family:Geist; src:url(fonts/Geist.woff2) format('woff2'); font-weight:100 900; }
@font-face { font-family:'Instrument Serif'; src:url(fonts/InstrumentSerif-Regular.woff2) format('woff2'); }
@font-face { font-family:'Instrument Serif'; font-style:italic;
             src:url(fonts/InstrumentSerif-Italic.woff2) format('woff2'); }
@page { size: 1280px 720px; margin: 0; }
* { box-sizing: border-box; margin: 0; padding: 0; }
:root { --ink:#0E1412; --ink2:#3E4945; --muted:#6B7571; --brand:#0B5D4B; --brand-soft:#E3EEEA;
        --paper:#F6F6F3; --card:#FFFFFF; --rule:#E1E3DD; }
body { font-family: Geist, Inter, system-ui, sans-serif; color: var(--ink); background: var(--paper); }
.slide { width:1280px; height:720px; padding:64px 80px 56px; position:relative; overflow:hidden;
         page-break-after: always; background: var(--paper); display:flex; flex-direction:column; }
.content { flex:1; display:flex; flex-direction:column; justify-content:center; padding-bottom:24px; }
.eyebrow { font-size:14px; letter-spacing:.14em; text-transform:uppercase; color:var(--brand);
           font-weight:600; margin-bottom:14px; }
h2 { font-family:'Instrument Serif', Georgia, serif; font-weight:400; font-size:60px; line-height:1.05;
     letter-spacing:-.01em; margin-bottom:36px; max-width:1000px; }
.title .lede { max-width:420px; }
.title.with-image .content { width:470px; }
.title .hero { font-size:92px; }
.hero { font-family:'Instrument Serif', Georgia, serif; font-weight:400; font-size:112px; line-height:.95;
        letter-spacing:-.02em; }
.title .hero.closing { font-size:64px; line-height:1.04; }
.lede { font-size:26px; line-height:1.35; color:var(--ink2); margin-top:28px; max-width:520px; }
.lede em { font-family:'Instrument Serif', Georgia, serif; font-size:30px; color:var(--brand); }
.team { position:absolute; bottom:56px; left:80px; font-size:16px; font-weight:500; }
.team span { display:block; color:var(--muted); font-weight:400; margin-top:4px; }
ul { list-style:none; }
ul li { position:relative; padding-left:30px; font-size:26px; line-height:1.35; color:var(--ink2);
        margin-bottom:18px; }
ul li::before { content:''; position:absolute; left:4px; top:14px; width:10px; height:10px;
                border-radius:50%; background:var(--brand); }
ul.tight li { font-size:20px; margin-bottom:10px; }
b { color:var(--ink); font-weight:600; }
sup { font-size:.6em; color:var(--brand); font-weight:600; margin-left:2px; }
.split { display:grid; grid-template-columns: 1.5fr 1fr; gap:56px; align-items:start; }
.stack { display:flex; flex-direction:column; gap:20px; }
.card { background:var(--card); border:1px solid var(--rule); border-radius:18px; padding:26px 28px; }
.card.accent { border-color:var(--brand); background:var(--brand-soft); }
.card .k { font-size:13px; letter-spacing:.12em; text-transform:uppercase; color:var(--brand);
           font-weight:600; margin-bottom:12px; }
.card p { font-size:21px; line-height:1.4; color:var(--ink2); }
.card p + p { margin-top:12px; }
.card ul li { font-size:21px; margin-bottom:10px; }
.card .big { font-family:'Instrument Serif', Georgia, serif; font-size:68px; line-height:1; margin-bottom:12px; }
.grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; }
.fact { background:var(--card); border:1px solid var(--rule); border-radius:16px; padding:20px 24px; }
.fact .k { font-size:12px; letter-spacing:.12em; text-transform:uppercase; color:var(--brand); font-weight:600; }
.fact .v { font-family:'Instrument Serif', Georgia, serif; font-size:56px; line-height:1.05; margin-top:8px; }
.fact .l { font-size:17px; line-height:1.35; color:var(--ink2); margin-top:6px; }
.inline-stats { font-size:19px; color:var(--muted); margin-top:10px; }
.inline-stats b { font-family:'Instrument Serif', Georgia, serif; font-size:30px; color:var(--brand); font-weight:400; }
.mt { margin-top: 30px; }
.lede-row { font-size:24px; color:var(--ink2); margin:-14px 0 30px; }
.stats.three { grid-template-columns:repeat(3,1fr); margin-top:26px; }
.case { display:grid; grid-template-columns: 0.9fr 1.1fr; gap:36px; align-items:start; }
.case .k { font-size:13px; letter-spacing:.12em; text-transform:uppercase; color:var(--brand); font-weight:600; margin-bottom:12px; }
.path { padding-left:26px; } .path li { font-size:19px; line-height:1.4; color:var(--ink2); margin-bottom:11px; padding-left:4px; }
.path li::marker { color:var(--brand); font-weight:600; }
.map { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--rule); border-radius:16px; overflow:hidden; }
.map th { text-align:left; font-size:12.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--brand); padding:12px 16px; border-bottom:1px solid var(--rule); }
.map td { font-size:17px; padding:9px 16px; border-bottom:1px solid var(--rule); color:var(--ink2); }
.map td:last-child { color:var(--ink); font-weight:500; }
.map tr.todo td { color:var(--muted); font-weight:400; font-style:italic; }
.small { font-size:15px !important; color:var(--muted) !important; }
.stats { display:grid; grid-template-columns:repeat(4,1fr); gap:20px; }
.stat { background:var(--card); border:1px solid var(--rule); border-radius:18px; padding:32px 28px; }
.stat .v { font-family:'Instrument Serif', Georgia, serif; font-size:76px; line-height:1; color:var(--brand); }
.stat .l { font-size:19px; line-height:1.35; color:var(--ink2); margin-top:12px; }
.stack .stat { padding:22px 26px; } .stack .stat .v { font-size:56px; }
.note { font-size:21px; line-height:1.45; color:var(--ink2); margin-top:32px; max-width:1050px; }
.cols { display:grid; grid-template-columns:repeat(3,1fr); gap:22px; }
.cols.two { grid-template-columns:1fr 1fr; gap:24px; }
.flow { display:grid; grid-template-columns:repeat(3,1fr); gap:22px; margin-bottom:34px; }
.steps { display:flex; flex-direction:column; gap:26px; margin-top:6px; }
.steps div { display:grid; grid-template-columns:52px 1fr; column-gap:18px; }
.steps span { grid-row:span 2; width:44px; height:44px; border-radius:50%; background:var(--brand);
              color:#fff; font-weight:600; font-size:20px; display:flex; align-items:center; justify-content:center; }
.steps b { font-size:25px; } .steps p { font-size:19px; color:var(--muted); margin-top:4px; }
.with-image .content { width:440px; }
.with-image ul li { font-size:22px; margin-bottom:14px; }
.with-image h2 { font-size:52px; }
.shot { position:absolute; right:80px; top:160px; width:640px; height:400px; border-radius:16px; overflow:hidden;
        border:1px solid var(--rule); box-shadow:0 30px 60px -20px rgba(14,20,18,.25); }
.shot img { width:100%; height:100%; display:block; }
.sources { position:absolute; left:80px; right:80px; bottom:26px; font-size:12.5px; color:var(--muted); }
.foot { position:absolute; right:80px; bottom:26px; font-size:12.5px; color:var(--muted); }
.divider { background:var(--brand); justify-content:center; }
.divider .eyebrow { color:#BFE0D5; } .divider h2 { color:#fff; font-size:96px; }
.refs { padding-left:24px; } .refs li { font-size:15.5px; line-height:1.35; margin-bottom:9px; color:var(--ink2); }
.refs span { font-size:13px; color:var(--muted); }
"""


def render(deck: list[dict]) -> str:
    pages = []
    for i, s in enumerate(deck, 1):
        kind = s.get("kind", "normal")
        img = (f'<div class="shot"><img src="screenshots/{s["image"]}"></div>' if s.get("image") else "")
        foot = "" if kind in ("title", "divider") else f'<div class="foot">Claim Check · Mirza Ahsan Baig &amp; Safee Akmal · {i}</div>'
        src = f'<div class="sources">{cite(*s["sources"])}</div>' if s.get("sources") else ""
        if kind == "title":
            inner = f'<div class="content">{s["body"]}</div>{img}'
        elif kind == "divider":
            inner = f'<div class="eyebrow">{s["eyebrow"]}</div><h2>{s["title"]}</h2>'
        else:
            inner = (f'<div class="content"><div class="eyebrow">{s["eyebrow"]}</div>'
                     f'<h2>{s["title"]}</h2>{s["body"]}</div>{img}')
        cls = f'slide {kind}' + (" with-image" if s.get("image") else "")
        pages.append(f'<section class="{cls}">{inner}{src}{foot}</section>')
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Claim Check — pitch</title>'
            f'<style>{CSS}</style></head>'
            f'<body>{"".join(pages)}</body></html>')


def main() -> int:
    deck = slides()
    html_path, pdf_path = DOCS / "pitch.html", DOCS / "pitch.pdf"
    html_path.write_text(render(deck))
    chrome = next((c for c in ("chromium", "chromium-browser", "google-chrome") if shutil.which(c)), None)
    if not chrome:
        print("  chromium not found — open docs/pitch.html and print to PDF")
        return 1
    subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                    "--virtual-time-budget=10000", f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                   check=True, capture_output=True)
    print(f"  {pdf_path.name}: {pdf_path.stat().st_size // 1024} KB, {len(deck)} slides")
    return 0


if __name__ == "__main__":
    sys.exit(main())
