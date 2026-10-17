# Claim Check — written project description

*Bano Qabil AI hackathon submission — Mirza Ahsan Baig and Safee Akmal.*

## The problem

A private clinic in Pakistan treats an insured patient, then bills the insurer or TPA. A
large share of those claims come back rejected — and mostly not because the treatment was
wrong. They are rejected for paperwork: pre-authorisation that was never obtained, a filing
deadline that passed, documents left out of the envelope, a procedure code that does not
match the diagnosis.

The clinic finds out weeks later. Someone has to rework the claim and resubmit it. Cash flow
becomes unpredictable, which is hardest on exactly the small clinics that have no dedicated
billing specialist to catch these errors in the first place.

That makes it a financial inclusion problem as much as a healthcare one. A clinic that keeps
losing money to preventable rejections raises prices, cuts services, or stops accepting
insurance patients — which hurts the people insurance was meant to help.

## What we built

A pre-submission check. The clerk enters the bill — the patient's age and sex, the procedure
and the amount — and answers the few paperwork questions that matter for that claim. They get
back how likely it is to be rejected, which specific field is causing that, which insurer rules
it trips, and what to do before submitting.

It is a spell-checker for insurance claims.

### Four parts

**A risk model.** Gradient-boosted trees (LightGBM), calibrated so that a stated 40% means
roughly four in ten, predict the probability of rejection on a claim that may still be half
answered. Exact Shapley values attribute that probability to the bill and the answers: each
field gets signed points, and the points add up from a typical claim to this one, so the tool
points at the field driving the risk rather than returning a bare number. A second model
predicts which real [X12 CARC code](https://x12.org/codes/claim-adjustment-reason-codes) — the
industry-standard rejection reason vocabulary — is most likely to apply.

We chose trees over a neural network deliberately. Published comparisons on claim-denial data
find neural networks underperform tree methods on this kind of tabular problem, and trees
handle a missing answer natively, which a question-by-question flow depends on.

**An adaptive question flow.** Rather than a long form, the tool asks one question at a time.
After every answer it computes, for each open question, the mutual information between the
rejection outcome and that question's answer, given everything known so far — and asks
whichever would reduce uncertainty most. It stops once the verdict is settled: never before
three questions, never after eight.

This is the part that is genuinely different. Insurance chatbots that do adaptive intake
generally run a decision tree; this is information-theoretic. Across 362 held-out claims it
used **55 different question orders**, and the first question alone depends on the bill.
Where a procedure needs pre-authorisation, that is always asked first: a one-step
information-gain calculation cannot see that the procedure is what makes pre-authorisation
matter, so the rule is structural rather than left to chance.

**Insurer rule checks.** A clerk who knows pre-authorisation was not obtained learns nothing
from being told so. The value is in what they would otherwise look up: whether this procedure
needed it at all, how much of this insurer's filing window has gone, whether the bill is far
above the typical price, whether the plan tier covers it, whether the diagnosis justifies it,
whether it fits the patient. Six such checks run on every claim, from the same rule tables the
training data was labelled with.

**A plain-language layer, powered by Qwen.** The structured result — probability, drivers,
rule-check issues, reason code — goes to Qwen through Alibaba Cloud's DashScope
OpenAI-compatible API, which returns two sentences a receptionist can act on.

The constraint here is the important part. Qwen sees only the already-computed result, and its
reply is checked before anyone sees it: every number in it must appear in the payload, the
stated risk must be the computed one, and it must not describe quantities in words. A reply
that fails is replaced by a deterministic template, as is any reply when the network fails, so
the tool degrades rather than breaks. Calls are capped per minute and cached per claim.

## Results

On a held-out test split of 362 claims, used once, for this report:

| | |
|---|---|
| ROC AUC | **0.849** fully answered (0.763 with questions still open) |
| Cleared as low risk | 63% of claims — 89% of them were accepted |
| Flagged high risk | 26% of claims — 56% of them were rejected (base rate 24%) |
| Rejections flagged in advance | 72% |
| Accuracy at the high-risk line | 81.8% — against a 76.2% "approve everything" baseline |
| Questions per check | 3.6 for accepted claims, 3.7 for rejected |
| CARC reason code | 83% top-1 across 13 codes (random: 8%) |

The verdict thresholds were not chosen by hand. They were fitted on a separate validation split
by simulating the real question loop, under written constraints: a "low" verdict must be right
at least 85% of the time, "high" must mean more likely rejected than not, and both must sit on
either side of the average claim's risk. Hyperparameters were chosen by cross-validation inside
the training split.

Read accuracy carefully. Only 24% of claims are rejected, so a model that blindly approved
everything would already score 76%. Verdict quality and AUC are the meaningful numbers.

## About the data — the part we want to be asked about

There is no public dataset anywhere of real clinic claims paired with their outcomes.
Insurers and TPAs treat that as private business information; every commercial product in
this space trains on its own private claims history. So we built our dataset, but not from
nothing:

- **Synthea**, the open-source clinical simulator from MITRE, generates the patient records.
  Diagnoses and procedures follow real care pathways and carry real SNOMED CT codes. The
  bundle is pinned by checksum, and the dataset builds byte-for-byte from it.
- **X12 CARC codes** supply the rejection vocabulary. All thirteen codes we use were verified
  against the official published list.

What is ours is the labelling: a probabilistic rules engine that maps believable claim defects
to the matching real reason code, with noise, plus a Pakistani context layer (prices anchored to
typical clinic rates, invented insurer names, plan tiers, filing windows) and the paperwork
fields Synthea does not model.

We hold accuracy in the 70–85% band on purpose. On self-generated data a near-perfect score
would only prove the model had reverse-engineered our own rules — which is not evidence it would
work on real claims. `docs/data-provenance.md` states exactly where the line between real and
simulated falls, and `docs/model-card.md` lists every limitation we know of.

## Built to be checked

An independent audit of the first version found that its published metrics could not be
reproduced on another machine, that "pre-authorisation not required" for a procedure that
required it scored as safely as having it, and that the per-field points could explode or
point the wrong way. Each finding is fixed and pinned by a test: the build is deterministic and
checked weekly in CI, contradictory answers are impossible to enter, attribution is exact, and
the API validates every value before the model sees it. The rebuilt model is stronger than the
one it replaced.

## Honest about the loop

When staff disagree with a prediction, one click logs it as a labelled example, with an
optional note. We are not claiming live self-improvement; nobody in this industry has that. Real
products close the loop by parsing the 835 remittance files insurers return, and retraining as
outcomes accumulate over weeks. Our architecture supports that. The demo logs the correction and
says plainly that it does nothing more.

## What production would need

1. Real historical claims with real adjudication outcomes, from a partner clinic or TPA
2. Parsed 835 remittance advice, to capture the CARC codes insurers actually returned
3. Retraining as those outcomes accumulate, with human corrections as labels
4. Per-insurer models and rule tables — filing rules, pre-authorisation lists and tariffs
   differ by payer

The system we built supports all four. It simply cannot access the data they require.

## Stack

Python 3.12, LightGBM, FastAPI · Next.js 16, TypeScript, Tailwind · Qwen (`qwen-plus`) via
Alibaba Cloud DashScope. Both services are containerised, and the images have been built and
run end to end locally with Docker Compose; [`deployment.md`](deployment.md) is the runbook for
Alibaba Cloud ECS, which has not been deployed to yet.

Architecture diagram: [`architecture.svg`](architecture.svg).
