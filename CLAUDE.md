# CLAUDE.md — Tasdeeq-e-Bima (Claim Check)

Pre-submission claim rejection checker for Pakistani private clinics. The clerk enters
the patient and the bill; an information-gain engine then asks the few paperwork
questions that matter for *this* claim. A calibrated LightGBM model gives the rejection
probability, exact Shapley values say which field is driving it, rule checks surface the
insurer policy facts (pre-auth, filing deadline, tariff, coverage), a second model names
the likely X12 CARC code, and Qwen (DashScope) restates the computed result in plain
English. Hackathon demo, by Mirza Ahsan Baig and Safee Akmal.

## Layout

```
ml/features.py        THE feature contract: askable/context/derived fields, business rules,
                      derive_frame() and prepare(). Labeller, training masking and the API
                      all call these — never re-implement a derived feature elsewhere.
ml/build_dataset.py   Synthea CSVs -> claims_base.parquet (deterministic, checksum-gated)
ml/label_carc.py      synthetic process fields + probabilistic CARC labelling
ml/train.py           grouped-CV selection, out-of-fold calibration, CARC model, bands
ml/simulate.py        runs the live question loop over held-out claims (bands + product metrics)
ml/calibration.py     centred isotonic calibrator + clamp
ml/evaluate.py        regenerates docs/model-card.md and cross-checks metadata.json
backend/main.py       FastAPI: sessions (bounded, TTL), validation, rate limits, result cache
backend/predictor.py  model serving + exact Shapley attribution of the calibrated probability
backend/services/adaptive.py      question bank, info-gain engine, stop rule, validation
backend/services/checks.py        deterministic insurer-rule checks
backend/services/explain_qwen.py  Qwen prompt, output validator, call budget, fallback
models/               committed artefacts + metadata.json (metrics, priors, bands, loop)
data/processed/claims.parquet     committed labelled dataset (tests and evaluation use it)
frontend/             Next.js 16 App Router + Tailwind v4 (see frontend/AGENTS.md)
frontend/app/page.tsx state machine: landing -> intake -> check -> verdict; demo autoplay
frontend/app/api/[...path]/route.ts  same-origin proxy to API_URL (read at request time)
frontend/components/  RiskPanel, Trajectory, Waterfall, Ledger, Checks, QuestionCard, ...
docs/build_deck.py    deck built from models/metadata.json + docs/screenshots/
docs/build_pitch.py   stage pitch (docs/pitch.pdf): business case first, outside figures cited
scripts/              fetch_synthea, check_drift (CI gate), screenshots, test_dashscope
```

## Commands

```bash
uv sync --frozen --all-extras             # Python 3.12 env exactly as uv.lock pins it
uv run pytest -q                          # offline; never calls Qwen or touches feedback.db
uv run pytest -q -m live                  # one real DashScope call (needs .env)
uv run uvicorn backend.main:app --reload --port 8000 --no-proxy-headers
uv run python scripts/test_dashscope.py   # Qwen end-to-end check before a demo
cd frontend && npm ci && npm run dev      # UI on :3000, proxies /api to API_URL (:8000)
cd frontend && npm run typecheck && npm run lint && npm test   # typecheck runs next typegen first
uvx ruff@0.14.0 check backend ml tests scripts --select F,E9
```

Retraining (artefacts are committed; only needed if the pipeline changes):

```bash
uv run python scripts/fetch_synthea.py    # pinned bundle, SHA-256 verified
uv run python ml/build_dataset.py && uv run python ml/label_carc.py
uv run python ml/train.py                 # ~3 min; prints test + verdict metrics
uv run python ml/evaluate.py              # rewrites docs/model-card.md, checks metadata
```

After retraining: re-run `ml/evaluate.py`; update the figures in README.md, docs/*.md and
IMPLEMENTATION_PLAN.md from `models/metadata.json`; re-check the demo claims in
`backend/demo_claims.py` (tests pin their behaviour); then, with the app running,
`uv run --with playwright python scripts/screenshots.py --url <prod build>` and
`uv run --extra deck python docs/build_deck.py` and `uv run python docs/build_pitch.py`
(both decks read metadata themselves).

## Conventions

- **Commits:** single-line Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`,
  `test:`, `build:`, `chore:`, `ci:`). Author is always `mirza-ahsan
  <ahsan.17april@gmail.com>`. **Never** add a `Co-Authored-By` trailer or any Claude
  attribution.
- Match surrounding style: module docstrings explain *why*; comments are sparse and
  record decisions and the bug they prevent, not mechanics.
- Never commit `.env` or credential CSVs. Never print, log or echo the DashScope key —
  presence only. `.mcp.json` is local tooling and is not tracked.

## Guardrails (do not break)

1. **Qwen explains, never decides.** It sees only the computed payload; `validate()`
   rejects any reply stating a number not in that payload, and the fallback is shown.
2. **One feature contract** (`ml/features.py`). Training and serving must share
   `derive_frame` and `prepare`, including the explicit `__unknown__` category.
3. **Pre-authorisation:** never offer "not required" when it was required; ask it first
   when required; imply it when not. No claim needing it may be cleared without it.
4. **Only real CARC codes**, with official X12 wording kept verbatim (13 codes; 6 is age,
   7 is gender). **A reason is a fact:** the API names a code only if `defect_codes()`
   finds that defect in the claim, and never calls a claim "ready to submit" while one is
   present (low band → `fix_first`). tests/test_adaptive.py pins `defect_codes` to the
   labeller's conditions on every claim.
5. **Honest numbers.** Selection uses train (CV) and validation only; test is reported
   once. Bands must straddle the base rate, keep "low" ≥ 85% approved and "high" ≥ 50%
   rejected (ml/simulate.py). Every published figure must match metadata.json.
6. **The demo survives a dead network** via the template fallback.

## Frontend design rules

- Colour has four meanings only: brand (actions/focus), verdict bands (always icon + label),
  attribution polarity (`--up` raises / `--down` lowers), and nothing else. Text never
  wears a data colour. Tokens live in `app/globals.css`, light and dark both validated.
- A band word is a verdict: below `min_questions` the panel shows "Early estimate".
- Every number on screen comes from the API; never hardcode bands, limits or metrics.
- Letter shortcuts only fire while focus is inside the question card; each new question,
  editor and verdict takes focus; the page has one persistent `aria-live` region.
- Number inputs refuse malformed values with a message — never strip characters.
- Check layouts at 390px and 1440px, light and dark — `scrollWidth` must equal the viewport.

## Security rules

- Rate limits key on the client address. Behind our proxies only the **rightmost**
  X-Forwarded-For hop is trusted (backend `client_key`, frontend `lib/visitor.ts`); the
  leftmost entry is attacker-controlled. Trust is opt-in on both sides
  (`TRUST_PROXY_HEADERS`, `TRUST_FORWARDED_FOR`), uvicorn runs with `--no-proxy-headers`,
  and nginx must *set* the header (`$remote_addr`), never pass a client's through.
- Errors never echo submitted values (custom 422 handler); `NaN`/`Infinity` must stay 422.

## Gotchas

- `tests/conftest.py` blanks `DASHSCOPE_API_KEY` and points `FEEDBACK_DB_PATH` at a temp
  file before `backend` is imported. Shared helpers live in `tests/helpers.py`.
- The band simulation relies on question choice being independent of the bands; if the
  engine's `choose()` ever looks at the band, `ml/simulate.py` becomes wrong
  (tests/test_adaptive.py::test_band_simulation_matches_the_live_loop guards this).
- Isotonic plateaus and LightGBM's categorical-NaN handling both made answers look like
  they "did nothing"; see ml/calibration.py and `UNKNOWN` in ml/features.py before
  changing either.
- `frontend/AGENTS.md` / `frontend/CLAUDE.md` are written by `next dev`; keep them tracked.
- This Next.js version differs from older ones — read `frontend/node_modules/next/dist/docs/`
  before using an unfamiliar API.
- Docker is installed (29.9 + Compose); `docker compose --env-file .env up --build`
  builds both images (the API image runs the test suite) and serves on localhost:3000.
