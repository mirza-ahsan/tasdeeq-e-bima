"""Build the pitch deck as both .pptx and .pdf from one content source.

    uv run --extra deck python docs/build_deck.py

The slides are defined once in `slides()`. Every figure on them is read from
models/metadata.json when the deck is built, so the deck cannot quote a number the
committed model does not produce; the product images are docs/screenshots/, captured from
the running app by scripts/screenshots.py. The PPTX is written with python-pptx; the PDF
is the same content rendered as HTML through headless Chromium.
"""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import sys
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

DOCS = Path(__file__).resolve().parent
ROOT = DOCS.parent
SHOTS = DOCS / "screenshots"

INK = RGBColor(0x0E, 0x14, 0x12)
INK_SOFT = RGBColor(0x3E, 0x49, 0x45)
MUTED = RGBColor(0x5F, 0x6A, 0x66)
BRAND = RGBColor(0x0B, 0x5D, 0x4B)
PAPER = RGBColor(0xF6, 0xF6, 0xF3)
RULE = RGBColor(0xE1, 0xE3, 0xDD)


def pct(x: float) -> str:
    return f"{x:.0%}"


def slides() -> list[dict]:
    meta = json.loads((ROOT / "models" / "metadata.json").read_text())
    m, v, b = meta["metrics"], meta["product_test"], meta["product_test"]["bands"]
    bands = meta["risk_bands"]
    n_codes = len(meta["carc_classes"])
    low_ok = 1 - b["low"]["rejection_rate"]
    mean_q = (v["mean_questions_approved"] + v["mean_questions_rejected"]) / 2

    return [
        dict(eyebrow="BANO QABIL AI HACKATHON", headline="Claim Check",
             body=["Catch the rejection before you submit the claim.",
                   "",
                   "A pre-submission check for Pakistani private clinics: how likely a",
                   "claim is to be rejected, which field is the problem, and what to fix."],
             image="landing.png",
             note="Mirza Ahsan Baig and Safee Akmal · Tasdeeq-e-Bima"),

        dict(eyebrow="THE PROBLEM", headline="Rejections are paperwork, not medicine",
             body=["A clinic treats an insured patient, then bills the insurer or TPA.",
                   "A large share of those claims come back rejected — for a missing",
                   "pre-authorisation, a filing deadline passed, documents left out.",
                   "",
                   "The clinic finds out weeks later. Small clinics have no billing",
                   "specialist to catch it. Cash flow becomes unpredictable, and clinics",
                   "eventually stop accepting insurance patients at all."],
             note="A financial-inclusion problem as much as a healthcare one"),

        dict(eyebrow="HOW IT WORKS", headline="Read the papers. Ask what matters.",
             body=["1.  The clerk photographs the bill and its papers, and Qwen-VL reads",
                   "     what is printed on them; the clerk confirms every value (or types",
                   "     the bill instead).",
                   "2.  The tool asks only the questions the papers cannot settle,",
                   "     choosing each by expected information gain.",
                   "3.  It returns the risk, the fields driving it, the likely X12 CARC",
                   "     reason code, and the one thing to do before submitting.",
                   "",
                   "When the procedure needs pre-authorisation, that is always the",
                   "first question — a claim that needed it is never cleared without it."],
             image="documents.png",
             note="Calibrated LightGBM · exact Shapley attribution · Qwen and Qwen-VL on Alibaba Cloud DashScope"),

        dict(eyebrow="THE DIFFERENTIATOR", headline="It asks, it doesn't just collect",
             body=["Most tools here are a form with a model bolted on, or a fixed",
                   "decision tree. This one computes, after every answer, how much each",
                   "remaining question would reduce its uncertainty about this claim,",
                   "and asks that one.",
                   "",
                   f"It stops as soon as the verdict is settled: never before {meta['loop']['min_questions']}",
                   f"questions, never after {meta['loop']['max_questions']}."],
             stats=[(str(v["distinct_question_orders"]), f"question orders across {v['claims']} test claims"),
                    (f"{mean_q:.1f}", "questions on average"),
                    ("6", "insurer rules checked on every claim")]),

        dict(eyebrow="THE VERDICT", headline="Every number traces back",
             body=["The risk is a calibrated probability, with bands fitted by",
                   "simulating the real question loop.",
                   "",
                   "Shapley attribution names the fields driving it, and the points add",
                   "up exactly from a typical claim to this one.",
                   "",
                   "Rule checks say what the clerk may not know: whether pre-approval",
                   "was needed, how much of the filing window is left, and whether the",
                   "papers agree — an approval for less than was billed, a missing one.",
                   "",
                   "Qwen only words the result. Any reply with a number it was not",
                   "given is rejected and replaced by a template."],
             image="result-risky.png"),

        dict(eyebrow="RESULTS", headline="On claims the model never saw",
             body=[f"Only {pct(meta['base_rejection_rate'])} of claims are rejected, so blindly approving",
                   f"everything already scores {pct(m['majority_class_baseline'])}. AUC and verdict",
                   "quality are the honest headline.",
                   "",
                   f"Claims cleared as low risk: {pct(b['low']['share'])} of all, {pct(low_ok)} of them accepted.",
                   f"Claims flagged high: {pct(b['high']['share'])} of all, {pct(b['high']['rejection_rate'])} of them rejected.",
                   f"Accuracy at the high-risk line: {m['test_accuracy_full']:.1%}.",
                   f"Reason code: {m['carc_top1']:.0%} top-1 across {n_codes} real codes."],
             stats=[(f"{m['test_auc_full']:.2f}", "ROC AUC, fully answered"),
                    (pct(low_ok), "of cleared claims accepted"),
                    (pct(v["rejections_flagged"]), "of rejections flagged in advance")],
             note=f"Held-out test split, used once · low ≤ {pct(bands['low_max'])}, high ≥ {pct(bands['high_min'])}"),

        dict(eyebrow="THE DATA", headline="No, the data is not real. It cannot be.",
             body=["No public dataset exists anywhere of real clinic claims paired with",
                   "their outcomes. Insurers treat that as private business information.",
                   "",
                   "So we built ours from two real pieces:",
                   "     Synthea — open clinical simulator, real SNOMED CT codes",
                   "     X12 CARC — the real industry rejection-reason standard",
                   "",
                   "Only the labelling logic is ours, and it is written down.",
                   "A near-perfect score on self-generated data would only prove",
                   "the model had memorised our rules."],
             note="docs/data-provenance.md · docs/model-card.md"),

        dict(eyebrow="BUILT TO BE CHECKED", headline="Reproducible, and audited",
             body=["The dataset builds byte-for-byte from a checksum-pinned Synthea",
                   "bundle; CI rebuilds it weekly and fails on any drift.",
                   "",
                   "Hyperparameters chosen by cross-validation, bands on validation,",
                   "the test split reported once — as the verdicts users would get.",
                   "",
                   "An independent audit's findings are fixed and pinned by tests:",
                   "input validation, verdict gating, attribution that adds up, and an",
                   "explanation layer that cannot state a number it was not given."],
             note="When staff disagree, one click logs a labelled correction. Nothing retrains live."),

        dict(eyebrow="WHAT'S NEXT", headline="What production would need",
             body=["1.  Real historical claims with real adjudication outcomes, from a",
                   "     partner clinic or TPA",
                   "2.  Parsed 835 remittance advice, for the codes insurers actually sent",
                   "3.  Retraining as outcomes accumulate, with human corrections as labels",
                   "4.  Per-insurer models and rule tables — they differ by payer",
                   "",
                   "The system supports all four. It cannot access the data they need."],
             note="Python · LightGBM · FastAPI · Next.js · Qwen on DashScope · containerised"),
    ]


def paragraphs(lines: list[str]) -> list[str]:
    """Join the hand-wrapped lines of a slide body into flowing paragraphs ("" stays a gap),
    so the text re-wraps to whatever column the slide gives it. A numbered item absorbs its
    indented continuation lines; any other indented line is its own paragraph."""
    out: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if not stripped:
            out.append("")
        elif not out or out[-1] == "" or stripped[:2].rstrip(".").isdigit():
            out.append(stripped)
        elif ln.startswith(" ") and not out[-1][:2].rstrip(".").isdigit():
            out.append(stripped)
        else:
            out[-1] = f"{out[-1]} {stripped}"
    return out


def _txt(frame, text, size, color, bold=False, font="Arial", space_after=0):
    frame.text = text
    p = frame.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    p.space_after = Pt(space_after)
    r = p.runs[0]
    r.font.size = Pt(size)
    r.font.color.rgb = color
    r.font.bold = bold
    r.font.name = font
    return p


def build_pptx(path: Path, deck: list[dict]) -> None:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    for i, s in enumerate(deck):
        slide = prs.slides.add_slide(blank)
        slide.background.fill.solid()
        slide.background.fill.fore_color.rgb = PAPER

        _txt(slide.shapes.add_textbox(Inches(0.9), Inches(0.65), Inches(11), Inches(0.3))
             .text_frame, s["eyebrow"], 11, BRAND, bold=True)
        _txt(slide.shapes.add_textbox(Inches(0.85), Inches(1.0), Inches(11.6), Inches(1.0))
             .text_frame, s["headline"], 40 if i == 0 else 32, INK, font="Georgia")

        line = slide.shapes.add_connector(1, Inches(0.9), Inches(2.15), Inches(12.4), Inches(2.15))
        line.line.color.rgb = RULE
        line.line.width = Emu(9525)

        image = s.get("image")
        body_w = Inches(5.6) if image else Inches(7.6)
        tb = slide.shapes.add_textbox(Inches(0.9), Inches(2.45), body_w, Inches(3.9))
        tf = tb.text_frame
        tf.word_wrap = True
        for j, ln in enumerate(paragraphs(s.get("body", []))):
            p = tf.paragraphs[0] if j == 0 else tf.add_paragraph()
            p.text = ln
            p.space_after = Pt(3)
            if p.runs:
                r = p.runs[0]
                r.font.size = Pt(14 if image else 15)
                r.font.color.rgb = INK_SOFT
                r.font.name = "Arial"

        if image and (SHOTS / image).exists():
            slide.shapes.add_picture(str(SHOTS / image), Inches(6.85), Inches(2.45),
                                     width=Inches(5.6))

        for k, (value, label) in enumerate(s.get("stats", [])):
            top = Inches(2.5 + k * 1.3)
            _txt(slide.shapes.add_textbox(Inches(9.0), top, Inches(3.4), Inches(0.7)).text_frame,
                 value, 34, BRAND, bold=True)
            _txt(slide.shapes.add_textbox(Inches(9.05), top + Inches(0.62), Inches(3.4), Inches(0.4))
                 .text_frame, label, 11, MUTED)

        if s.get("note"):
            _txt(slide.shapes.add_textbox(Inches(0.9), Inches(6.65), Inches(11.5), Inches(0.4))
                 .text_frame, s["note"], 11, MUTED)
        _txt(slide.shapes.add_textbox(Inches(12.3), Inches(6.65), Inches(0.6), Inches(0.4))
             .text_frame, str(i + 1), 11, MUTED)

    prs.save(path)


def build_html(path: Path, deck: list[dict]) -> None:
    """Same content, laid out for print. Chromium turns this into the PDF."""
    out = []
    for i, s in enumerate(deck):
        stats = "".join(
            f'<div class="stat"><div class="sv">{html.escape(v)}</div>'
            f'<div class="sl">{html.escape(lbl)}</div></div>'
            for v, lbl in s.get("stats", []))
        body = "".join(
            f'<p class="{"gap" if not ln else ""}">{html.escape(ln) or "&nbsp;"}</p>'
            for ln in paragraphs(s.get("body", [])))
        side = (f'<div class="shot"><img src="screenshots/{html.escape(s["image"])}" alt=""></div>'
                if s.get("image") else f'<div class="stats">{stats}</div>')
        out.append(f"""
<section class="slide">
  <div class="eyebrow">{html.escape(s['eyebrow'])}</div>
  <h1 class="{'lead' if i == 0 else ''}">{html.escape(s['headline'])}</h1>
  <hr/>
  <div class="cols {'wide' if s.get('image') else ''}"><div class="body">{body}</div>{side}</div>
  <div class="foot"><span>{html.escape(s.get('note', ''))}</span><span>{i + 1}</span></div>
</section>""")

    path.write_text(f"""<!doctype html><meta charset="utf-8">
<title>Claim Check — deck</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600&family=Instrument+Serif:ital@0;1&display=block">
<style>
  @page {{ size: 13.333in 7.5in; margin: 0; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: Geist, Helvetica, Arial, sans-serif; color: #0e1412; background: #f6f6f3; }}
  .slide {{ width: 13.333in; height: 7.5in; padding: 0.6in 0.9in; position: relative;
            page-break-after: always; display: flex; flex-direction: column; background: #f6f6f3;
            background-image: radial-gradient(55% 45% at 10% 0%, rgba(19,151,112,.10), transparent 70%); }}
  .eyebrow {{ font-size: 10.5pt; font-weight: 600; letter-spacing: .14em; color: #0b5d4b; }}
  h1 {{ font-family: 'Instrument Serif', Georgia, serif; font-weight: 400; font-size: 36pt;
        margin: .14in 0 0; letter-spacing: -.01em; line-height: 1.05; }}
  h1.lead {{ font-size: 54pt; }}
  hr {{ border: 0; border-top: 1px solid #e1e3dd; margin: .26in 0 .3in; }}
  .cols {{ display: flex; gap: .6in; flex: 1; min-height: 0; }}
  .body {{ flex: 1; }}
  .body p {{ font-size: 14.5pt; line-height: 1.5; color: #3e4945; margin: 0 0 2pt; }}
  .cols.wide .body p {{ font-size: 13pt; }}
  .body p.gap {{ height: 10pt; margin: 0; }}
  .stats {{ width: 3.2in; }}
  .stat {{ margin-bottom: .38in; }}
  .sv {{ font-size: 36pt; font-weight: 600; color: #0b5d4b; line-height: 1; letter-spacing: -.02em; }}
  .sl {{ font-size: 10.5pt; color: #5f6a66; margin-top: 6pt; }}
  .shot {{ width: 6.4in; flex: none; }}
  .shot img {{ width: 100%; border-radius: 10px; border: 1px solid #e1e3dd;
               box-shadow: 0 18px 40px -22px rgba(16,24,22,.35); }}
  .foot {{ display: flex; justify-content: space-between; font-size: 10pt; color: #5f6a66; }}
</style>
{''.join(out)}""", encoding="utf-8")


def main() -> int:
    deck = slides()
    pptx_path, html_path, pdf_path = DOCS / "deck.pptx", DOCS / "deck.html", DOCS / "deck.pdf"
    build_pptx(pptx_path, deck)
    build_html(html_path, deck)
    print(f"  {pptx_path.name}: {pptx_path.stat().st_size // 1024} KB, {len(deck)} slides")

    chrome = next((c for c in ("chromium", "chromium-browser", "google-chrome")
                   if shutil.which(c)), None)
    if not chrome:
        print("  chromium not found — deck.pdf not built; open deck.html and print to PDF")
        return 0

    # The virtual-time budget lets the web fonts and screenshots load before printing.
    subprocess.run([chrome, "--headless", "--disable-gpu", "--no-sandbox",
                    "--no-pdf-header-footer", "--virtual-time-budget=10000",
                    f"--print-to-pdf={pdf_path}", html_path.as_uri()],
                   check=True, capture_output=True, timeout=180)
    print(f"  {pdf_path.name}:  {pdf_path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
