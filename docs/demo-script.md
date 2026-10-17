# Demo Script — under 3 minutes

Two claims, one honesty line, one closing point. Get the data-provenance line in **before**
anyone asks it; volunteering it lands far better than defending it.

**Before you start:** API and web running, browser at `http://localhost:3000`, and
`uv run python scripts/test_dashscope.py` passing (the masthead should read
"Model online · Qwen on"). Have `docs/model-card.md` open in a second tab in case a judge wants
numbers. Both demos **autoplay** one answer every ~1.6 s; the **Pause demo** button stops
it if you need to talk longer, and you can click the highlighted answer yourself instead.

---

## 0:00 — The problem (20 seconds)

> "When a Pakistani private clinic treats an insured patient, it bills the insurer or TPA
> afterwards. A lot of those claims come back rejected — and mostly not for clinical
> reasons. Missing pre-authorisation. A filing deadline passed. Documents left out.
> The clinic finds out weeks later, and small clinics don't have billing specialists.
>
> This catches it before you submit."

*On screen: the landing page. The example card on the right replays a real check.*

---

## 0:20 — The risky claim (60 seconds)

Click **Risky example**.

> "The clerk enters what's on the bill: a minor surgery, 85,000 rupees. The tool already knows
> something the clerk might not — this procedure needs pre-authorisation, at any price. So
> that's the first thing it asks."

*Point at the bill strip and at "Early estimate: 22%".*

> "Before any question it's at 22% — most surgeries do have their approval. And it shows that as
> an early estimate, not a verdict: no claim is judged on fewer than three answers."

When "No, not obtained" is answered, the red banner appears:

> "That one answer moved it 45 points — and it says so right away, while the clerk can still fix
> it, not at the end."

**Point at the info-gain line** above the next question:

> "Every question after that is picked by expected information gain — how much it would reduce
> the uncertainty about *this* claim. That's the selection criterion, not a script."

When it stops after the third question, on the verdict:

> "High risk of rejection, 67%. And it says why."

**Point at the waterfall:**

> "A typical claim for this patient is 27%. Pre-authorisation adds 41 points. These are exact
> Shapley values — they add up to the number on screen, they don't just rank."

**Point at the CARC block and the action box:**

> "The reason is CARC 197, a real X12 industry code, with the official wording. And one thing
> to do: get written pre-authorisation and attach it."

**Point at the quote:**

> "That sentence is Qwen. It only words the result — if it ever states a number it wasn't given,
> the reply is thrown away and a template is used. Read the caption."

---

## 1:20 — The clean claim (30 seconds)

Click **New check** (top right), then **Clean example**.

> "The obvious question is whether it just flags everything."

> "A 2,500-rupee consultation. Pre-authorisation isn't needed, so it never asks. Three
> questions, 18%, looks ready to submit — and **no** reason code. The reason model is only
> trained on rejected claims, so naming a reason for a healthy claim would be inventing a
> problem. It says so."

*Optional, if there's time: point at "Insurer rules" — "Not checked" means the loop never needed
that answer to reach a verdict.*

---

## 1:50 — The data, before anyone asks (25 seconds)

> "You should ask whether this data is real. It isn't, and it can't be — there is no public
> dataset anywhere of real clinic claims paired with their outcomes. Insurers treat that as
> private.
>
> So we built ours from two real pieces: **Synthea**, the open clinical simulator, for the
> patient records, and the real **X12 CARC** code list for the reasons. Only the labelling rules
> are ours, and we wrote down exactly where that line is.
>
> That's also why accuracy is 82% and not 99%. On data we labelled, a near-perfect score would
> only prove the model memorised our rules."

---

## 2:15 — Close (20 seconds)

On a result, click **This prediction is wrong**, type a short note, **Log correction**.

> "When staff disagree, that's logged as a labelled example. We're not claiming live
> self-improvement — real systems close that loop from insurer remittance files over weeks.
>
> It's a second check for clinic staff, not a replacement for their judgement. And it's
> trustworthy because it shows its reasoning instead of handing over a number."

---

## Prepared answers

| If they ask | Say |
|---|---|
| "Is it really adaptive?" | Yes — across 362 held-out claims it used 55 different question orders, and the first question alone depends on the bill. The two claims you just saw were asked different things. |
| "What's your baseline?" | 76% of test claims were accepted, so "approve everything" scores 76%. We're at 82% at the high-risk line — but the honest numbers are AUC 0.85 and the verdicts: 89% of claims we clear are accepted, 56% of claims we flag high are rejected, against a 24% base rate. |
| "How did you choose the thresholds?" | Not by hand. They're fitted on a validation split by simulating the actual question loop, with written constraints, and the test split is used once, for the report. |
| "Why not deep learning?" | Published work on claim denial finds neural nets underperform trees on tabular claims. Trees also handle half-answered claims natively, which the question loop needs. |
| "What does Qwen actually do?" | Only the last step. It receives the computed probability, the drivers, the rule-check issues and the reason code as JSON, and words them. Its reply is checked number by number before display. |
| "How accurate is the reason code?" | 83% top-1 across 13 codes, on claims it never trained on — high because our labels follow our rules consistently, so treat it as an upper bound. And it only ever names a reason the claim actually shows. |
| "Would this work on real data?" | The architecture would. It needs a partner clinic's real outcomes, parsed 835 remittance files, and per-insurer rule tables. That's written down. |
| "Can it be reproduced?" | Yes: the dataset builds byte-for-byte from a checksum-pinned bundle, and CI rebuilds it weekly. |

## If something breaks

- **Qwen unreachable** — the explanation falls back to a template automatically, and the
  caption says so. Keep going; nothing else is affected.
- **API not running** — the masthead reads "Risk service offline — retrying", and the landing
  page shows the start command. Restart it; the page recovers on its own.
- **"This check has expired"** — the API restarted mid-demo and lost the session. Click
  **Start again**.
- **The risk doesn't move after an answer** — that is real: an expected answer (policy active,
  documents complete) changes little. The panel says "no change". Point at it rather than
  apologising for it.
