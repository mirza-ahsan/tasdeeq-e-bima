# Claim Check — written project description

*Bano Qabil AI hackathon submission — Mirza Ahsan Baig and Safee.*

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

A pre-submission check. Clinic staff answer a handful of questions about a claim and get
three things back: how likely it is to be rejected, which specific field is causing that,
and what to do before submitting.

It is a spell-checker for insurance claims.

### Three parts

**A risk model.** Gradient-boosted trees (LightGBM) predict the probability of rejection.
SHAP produces a per-claim attribution, so the tool points at the specific field driving the
risk rather than returning a bare number. A second model predicts which real
[X12 CARC code](https://x12.org/codes/claim-adjustment-reason-codes) — the industry-standard
rejection reason vocabulary — is most likely to apply.

We chose trees over a neural network deliberately. Published comparisons on claim-denial data
find neural networks underperform tree methods on this kind of tabular problem, and trees
give the per-claim attribution the explanation layer needs as a byproduct of training.

**An adaptive question flow.** Rather than a long form, the tool asks one question at a
time. After every answer it computes, for each unanswered field, the mutual information
between the rejection outcome and that field given everything known so far — then asks
whichever would reduce uncertainty most. It stops when it is confident or when no remaining
question would earn its keep.

This is the part that is genuinely different. Insurance chatbots that do adaptive intake
generally run a decision tree; this is information-theoretic. Across 200 held-out claims we
observe **72 distinct question orders**. Clean claims finish in about three questions,
problem claims take longer.

**A plain-language layer, powered by Qwen.** The structured output — probability, flagged
fields, reason code — goes to Qwen through Alibaba Cloud's DashScope OpenAI-compatible API,
which returns two sentences a receptionist can act on.

The constraint here is the important part. Qwen sees only the already-computed result, and
its prompt forbids inventing a probability, a reason code, or a field. It restates; it does
not decide. If the call fails, a deterministic template takes over, so the tool degrades
rather than breaks. A test asserts that no percentage appears in Qwen's output other than
the one the model computed.

## Results

| | |
|---|---|
| ROC AUC | **0.788** (0.733 with partial answers) |
| Accuracy | 77.9% — against a 73.5% majority-class baseline |
| Recall | 67.7% — catches two of every three claims that would be rejected |
| CARC reason code | 68.2% top-1, 79.7% top-3, across 12 codes (random: 8.3%) |
| Questions asked | 3.7 for approved claims, 4.1 for rejected |

Per-code accuracy is highest where the demo depends on it: **92% on CARC 197**
(pre-authorisation absent), 87% on out-of-network, 80% on incomplete documents.

Read accuracy carefully. Only 26.5% of claims are rejected, so a model that blindly approved
everything would already score 73.5%. AUC and recall are the meaningful numbers.

## About the data — the part we want to be asked about

There is no public dataset anywhere of real clinic claims paired with their outcomes.
Insurers and TPAs treat that as private business information; every commercial product in
this space trains on its own private claims history. So we built our dataset, but not from
nothing:

- **Synthea**, the open-source clinical simulator from MITRE, generates the patient records.
  Diagnoses and procedures follow real care pathways and carry real SNOMED CT codes.
- **X12 CARC codes** supply the rejection vocabulary. All twelve codes we use were verified
  against the official published list.

What is ours is the labelling: a probabilistic rules engine that maps believable claim
defects to the matching real reason code, plus a Pakistani context layer (PKR amounts,
fictional insurer names, plan tiers) and the paperwork fields Synthea does not model.

We hold accuracy at 77.9% on purpose. On self-generated data a near-perfect score would only
prove the model had reverse-engineered our own rules — which is not evidence it would work
on real claims. `docs/data-provenance.md` states exactly where the line between real and
simulated falls, and `docs/model-card.md` lists every limitation we know of.

## Honest about the loop

When staff disagree with a prediction, one click logs it as a labelled example. We are not
claiming live self-improvement; nobody in this industry has that. Real products close the
loop by parsing the 835 remittance files insurers return, and retraining as outcomes
accumulate over weeks. Our architecture supports that. The demo logs the correction and says
plainly that it does nothing more.

## What production would need

1. Real historical claims with real adjudication outcomes, from a partner clinic or TPA
2. Parsed 835 remittance advice, to capture the CARC codes insurers actually returned
3. Retraining as those outcomes accumulate, with human corrections as labels
4. Per-insurer models — filing rules and tariffs differ by payer

The system we built supports all four. It simply cannot access the data they require.

## Stack

Python 3.12, LightGBM, SHAP, FastAPI · Next.js 16, TypeScript, Tailwind ·
Qwen (`qwen-plus`) via Alibaba Cloud DashScope. Both services are containerised, and
[`deployment.md`](deployment.md) is a tested runbook for Alibaba Cloud ECS; the demo
runs locally.

Architecture diagram: [`architecture.svg`](architecture.svg).
