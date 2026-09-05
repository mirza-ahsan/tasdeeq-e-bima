# Backend: FastAPI + the trained models.
#
# Models are gitignored, so they are trained during the build. That keeps the image
# self-contained and reproducible; the whole pipeline takes about two minutes.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 curl unzip \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY ml ./ml
COPY backend ./backend
COPY data/carc_codes.json ./data/carc_codes.json

RUN pip install --no-cache-dir -e .

# Fetch Synthea, build the dataset, train. Kept in one layer so the intermediate
# CSVs (63MB) do not persist in the image.
RUN mkdir -p data/raw data/processed models \
 && curl -sL -o /tmp/synthea.zip \
      https://synthetichealth.github.io/synthea-sample-data/downloads/latest/synthea_sample_data_csv_latest.zip \
 && unzip -q /tmp/synthea.zip -d data/raw \
 && python ml/build_dataset.py \
 && python ml/label_carc.py \
 && python ml/train.py \
 && rm -rf data/raw /tmp/synthea.zip

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD curl -fsS http://localhost:8000/api/health || exit 1

# DASHSCOPE_* and QWEN_MODEL are injected at runtime. Never bake .env into an image.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
