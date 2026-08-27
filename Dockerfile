# --- frontend build ---
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --silent || npm install --silent
COPY frontend/ ./
RUN npm run build

# --- runtime ---
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 -r requirements.txt
COPY terrawatch/ ./terrawatch/
COPY recipes/ ./recipes/
COPY config.yaml README.md BUILD.md ./
COPY --from=ui /ui/dist ./frontend/dist
# Build-time reference check (spec §19): an unresolvable citation fails the image.
# Builds with no network access must opt out explicitly:
#   docker build --build-arg VERIFY_REFERENCES=0 .
ARG VERIFY_REFERENCES=1
RUN if [ "$VERIFY_REFERENCES" = "1" ]; then python -m terrawatch.recipes; fi
ENV TW_DATA_DIR=/data
CMD ["uvicorn", "terrawatch.api:app", "--host", "0.0.0.0", "--port", "8000"]
