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
    ("Aaj News, “More pain than relief for KP health card holders” (Baker Tilly audit), 22 Nov 2022",
     "english.aaj.tv/news/30304857"),
    ("Business Daily Africa, “Rural private hospitals reject SHA cover over Sh22bn debt”, 20 Feb 2025",
     "businessdailyafrica.com/bd/corporate/health/rural-private-hospitals-reject-sha-cover-over-sh22bn-debt-4934984"),
    ("American Medical Association, 2024 Prior Authorization Physician Survey",
     "ama-assn.org/press-center/ama-press-releases/ama-survey-indicates-prior-authorization-wreaks-havoc-patient-care"),
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

        dict(eyebrow="The problem", title="Claims get rejected for paperwork, not medicine", body=f"""
            <div class="split">
              <div>{ul([
                  "Weeks after treatment, the claim bounces: <b>no pre-authorisation</b>, a <b>missed "
                  "deadline</b>, <b>missing documents</b>",
                  "The clinic reworks it and waits again for its money",
                  "Small clinics can’t absorb that — they stop taking insured patients",
              ])}</div>
              <aside class="card">
                <div class="k">Pre-authorisation</div>
                <p>Insurer approval needed <b>before</b> a planned treatment. Miss it and correct care
                can still go unpaid.<sup>6</sup></p>
              </aside>
            </div>""", sources=[6]),

        dict(eyebrow="The scale", title="A global problem, hardest where insurance is new", body=f"""
            <div class="grid3">
              {fact("United States", "15%", "of claims to private insurers denied first time", "1")}
              {fact("United States", "$25.7B", "spent by providers fighting denials in 2023", "1")}
              {fact("United States", "86%", "of denials potentially avoidable", "2")}
              {fact("India", "71%", "of health-claim value actually paid, FY 2023-24", "3")}
              {fact("Pakistan · KP Sehat Card", "5–8 months", "payment waits, blamed on incomplete documents", "4")}
              {fact("Kenya", "600+", "rural private hospitals stopped accepting the national cover", "5")}
            </div>""", sources=[1, 2, 3, 4, 5]),

        dict(eyebrow="Our solution", title="A spell-checker for insurance claims", body="""
            <div class="steps">
              <div><span>1</span><b>Photograph the papers</b><p>Qwen reads them; the clerk confirms</p></div>
              <div><span>2</span><b>Answer what they can’t</b><p>Usually one to three questions</p></div>
              <div><span>3</span><b>Fix before sending</b><p>Chance of rejection, the cause, the fix</p></div>
            </div>""", image="documents.png"),

        dict(eyebrow="Business case", title="Everyone in the claim wins", body=f"""
            <div class="cols">
              <div class="card"><div class="k">Clinics</div>{ul(["Paid sooner", "Less rework", "Predictable cash flow"])}</div>
              <div class="card"><div class="k">Insurers &amp; TPAs</div>{ul(["Cleaner claims", "Fewer appeals", "Lower admin cost"])}</div>
              <div class="card"><div class="k">Patients</div>{ul(["Clinics keep accepting their insurance", "Care isn’t held up by paperwork"])}</div>
            </div>
            <p class="note"><b>How it could earn (proposed):</b> per-clinic subscription · licence to TPAs for
            their provider panel · integration with national e-claims schemes</p>"""),

        dict(eyebrow="How it works", title="Reads the papers, asks the rest, says what to fix", body=f"""
            {ul([
                "Fills in what is printed; asks only what the papers can’t settle",
                "Cross-checks the papers: a missing approval, an approval below the bill",
                "Risk updates after every answer; stops once settled",
                "The cause, the official rejection code, the fix — and a one-page printout",
            ])}
            <p class="inline-stats"><b>{v['mean_questions']:.1f}</b> questions on average ·
            <b>{v['distinct_question_orders']}</b> different question orders</p>""",
             image="result-documents.png"),

        dict(eyebrow="Responsible AI", title="Qwen reads and explains. The model decides.", body="""
            <div class="flow">
              <div class="card accent"><div class="k">Qwen-VL · reads</div><p>Fills in only what is printed;
                the clerk confirms every value</p></div>
              <div class="card"><div class="k">Model + rule tables · decide</div><p>The chance of rejection,
                the verdict, the reason, the checks</p></div>
              <div class="card accent"><div class="k">Qwen · explains</div><p>One plain sentence from the
                finished result</p></div>
            </div>
            <ul class="tight">
              <li>Pre-authorisation is <b>never</b> taken from a document — it is always asked</li>
              <li>Any reply quoting a number it wasn’t given is <b>discarded</b> for a fixed template</li>
              <li>No network? Type the bill; the template still works — the demo never breaks</li>
            </ul>"""),

        dict(eyebrow="Results", title="Tested on claims it never saw", body=f"""
            <div class="stats">
              {stat(pct(1 - b['low']['rejection_rate']), "of claims it cleared were accepted")}
              {stat(pct(v['rejections_flagged']), "of rejections flagged before submission")}
              {stat(pct(b['high']['rejection_rate']), f"of “high risk” claims were rejected (average {pct(base)})")}
              {stat(f"{m['test_auc_full']:.2f}", f"AUC with every answer known ({m['test_auc_partial']:.2f} mid-check)")}
            </div>
            <p class="note">{v['claims']} held-out simulated claims, used once. Verdict thresholds were fitted on a
            separate split, not chosen by hand.</p>"""),

        dict(eyebrow="Honest about it", title="Limits today, and what comes next", body=f"""
            <div class="cols two">
              <div class="card"><div class="k">What it does not do</div>{ul([
                  "Train on real claims — none are public; records are simulated, rules and codes are real",
                  "Replace staff — it is a second check",
                  "Read every photo perfectly — each value shows its quote to check",
                  "Submit claims or learn by itself",
                  "Protect real patient data yet — no login",
              ])}</div>
              <div class="card accent"><div class="k">What we’d build next</div>{ul([
                  "Pilot with a clinic or TPA on real claims",
                  "Per-insurer rules and models",
                  "Urdu and regional-language interface",
                  "Login, encryption, links to billing systems",
              ])}</div>
            </div>"""),

        dict(kind="title", body="""
            <div class="eyebrow">Claim Check · Tasdeeq-e-Bima</div>
            <h1 class="hero closing">Catch the rejection before you submit.</h1>
            <p class="lede">We’re looking for a <b>partner clinic or TPA</b> to pilot with real claims.</p>
            <p class="team">Mirza Ahsan Baig · Safee Akmal<span>Thank you</span></p>
            """, image="result-clean-dark.png"),

        dict(eyebrow="Sources", title="Where the figures come from", body="<ol class='refs'>" + "".join(
            f"<li>{e(t)}<br><span>{e(u)}</span></li>" for t, u in SOURCES) + "</ol>"
            + f"<p class='note small'>Our own results: models/metadata.json and docs/model-card.md in the "
              f"project repository ({meta['n_claims']:,} simulated claims, test split of {v['claims']}).</p>"),
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
        foot = "" if kind in ("title", "divider") else f'<div class="foot">{i}</div>'
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
