# Demo Script — under 3 minutes

Two claims, one honesty line, one closing point. Get the data-provenance line in
**before** anyone asks it; volunteering it lands far better than defending it.

**Before you start:** both processes running, browser at `http://localhost:3000`, and
`uv run python scripts/test_dashscope.py` passing. Have `docs/model-card.md` open in a
second tab in case a judge wants numbers.

---

## 0:00 — The problem (20 seconds)

> "When a Pakistani private clinic treats an insured patient, it bills the insurer or TPA
> afterwards. A lot of those claims come back rejected — and mostly not for clinical
> reasons. Missing pre-authorisation. A filing deadline passed. Documents left out.
> The clinic finds out weeks later, and small clinics don't have billing specialists.
>
> This is a spell-checker for claims. It checks before you submit."

*On screen: the landing page.*

---

## 0:20 — The problem claim (55 seconds)

Click **Example: problem claim**. Answer each question by clicking the option marked
`from demo claim`.

Narrate while clicking:

> "It asks one question at a time. It's not working through a form — after every answer it
> recalculates which remaining question would tell it the most, and asks that one."

**Point at the info-gain figure** on the right of the question header:

> "That number is the expected reduction in uncertainty. That's the actual selection
> criterion, not a fixed script."

When it stops after the fourth question:

> "It stopped on its own — it's confident enough. 66% chance of rejection."

**Point at the flagged fields panel:**

> "And it says why: pre-authorisation missing is worth 33 points of that risk. That's a
> per-claim attribution, not a global feature ranking."

**Point at the CARC block:**

> "The reason is CARC 197, a real X12 industry code, with the official wording. We don't
> invent reason codes."

**Point at the Qwen paragraph:**

> "That's Qwen turning the numbers into something a receptionist can act on. Note the
> caption — it's restating the result. It cannot change the probability, the code, or which
> fields were flagged. The prompt forbids it."

---

## 1:15 — The clean claim (35 seconds)

Click **Check another claim**, then **Example: clean claim**.

> "The obvious question is whether it just flags everything."

Click through the three questions.

> "Three questions instead of four, 18%, low risk — and notice it gives **no** reason code.
> The reason model is trained only on rejected claims, so naming a reason for a healthy
> claim would be inventing a problem. It says so."

---

## 1:50 — The data, before anyone asks (25 seconds)

> "You should ask whether this data is real. It isn't, and it can't be — there is no public
> dataset anywhere of real clinic claims paired with their outcomes, because insurers treat
> that as private business data. Every commercial product in this space trains on its own
> private claims history.
>
> So we built ours from two real pieces: **Synthea**, the open clinical simulator, for the
> patient records, and the real **X12 CARC** code list for the rejection vocabulary. Only
> the labelling logic is ours, and we documented exactly where that line is.
>
> That's also why accuracy is 77.9% and not 99%. On self-generated data, a near-perfect
> score would only prove the model had memorised our own rules."

---

## 2:15 — Close (20 seconds)

Click **This prediction is wrong**.

> "When staff disagree, that's logged as a labelled example. We're not claiming live
> self-improvement — nobody in this industry has that. Real systems close the loop from
> insurer remittance files over weeks.
>
> This is a second check for clinic staff, not a replacement for their judgement. And it's
> trustworthy precisely because it shows its reasoning instead of handing over a number."

---

## Prepared answers

| If they ask | Say |
|---|---|
| "Is it really adaptive?" | Yes — across 200 held-out claims we see **72 distinct question orders**. The two claims you just watched were asked different questions in a different order. |
| "What's your baseline?" | 73.5% of claims are approved, so blind "approve everything" scores 73.5%. We're at 77.9% — but accuracy is the wrong metric here. **AUC 0.788**, recall 67.7%: it catches two of every three claims that would be rejected. |
| "Why not deep learning?" | Published work on claim denial found neural nets underperform trees on tabular claim data. Trees also give per-claim attribution for free, which is what the explanation layer needs. |
| "What does Qwen actually do?" | Only the last step. It receives the computed probability, flagged fields and reason code as JSON and restates them. It never sees the model or the data. |
| "Would this work on real data?" | The architecture would. It needs a partner clinic's real outcomes and parsed 835 remittance files. We've written down exactly what's missing. |
| "How accurate is the reason code?" | 68% top-1, 80% top-3 across 12 codes against an 8.3% random baseline. Strongest on the codes that matter most: 92% on pre-authorisation. |

## If something breaks

- **Qwen unreachable** — the explanation falls back to a template automatically. The caption
  says so. Keep going; nothing else is affected.
- **API not running** — the landing page tells you the command. Restart and reload.
- **Risk figure doesn't move between two answers** — that is real and expected. Calibration
  is a step function, so an answer can genuinely leave the estimate unchanged. The panel
  says "no change". Point at it rather than apologising for it.
