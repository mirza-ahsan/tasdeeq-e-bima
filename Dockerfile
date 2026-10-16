# Backend: FastAPI + the committed, trained models.
#
# The image ships the exact artefacts behind the published numbers rather than
# retraining during the build, so what is deployed is what was evaluated and rehearsed.
# The test suite runs as a build stage; a failing test fails the build.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv

# Dependencies first, exactly as uv.lock pins them, so code changes reuse this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./backend
COPY ml ./ml
COPY data/carc_codes.json ./data/carc_codes.json
COPY models ./models
RUN uv sync --frozen --no-dev

# ---- tests: run against the very files the runtime image ships ------------------
FROM base AS test
COPY tests ./tests
COPY data/processed/claims.parquet ./data/processed/claims.parquet
RUN uv sync --frozen --extra dev && uv run --frozen --no-sync pytest -q \
 && touch /tmp/tests-passed

# ---- runtime ------------------------------------------------------------------
FROM base AS runtime
# Depending on a file the test stage writes makes BuildKit run that stage.
COPY --from=test /tmp/tests-passed /tmp/tests-passed

RUN useradd --create-home --uid 1001 app \
 && mkdir -p /data && chown app:app /data
USER app

ENV PATH="/app/.venv/bin:$PATH" FEEDBACK_DB_PATH=/data/feedback.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

# DASHSCOPE_* and QWEN_MODEL are injected at runtime. Never bake .env into an image.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
