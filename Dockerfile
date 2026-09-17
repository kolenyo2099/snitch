# --- frontend build ---
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
# ci, not install: a drifted lockfile must fail the build, not silently re-resolve
# the dependency tree of a reproducibility-sensitive image.
RUN npm ci --silent
COPY frontend/ ./
RUN npm run build

# --- runtime ---
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
# The frozen set pins every transitive dependency to a version that passed the
# test suite; requirements.txt remains the loose floor for local dev.
COPY requirements-frozen.txt .
RUN pip install --no-cache-dir --default-timeout=120 --retries=10 -r requirements-frozen.txt
COPY snitch/ ./snitch/
COPY recipes/ ./recipes/
COPY config.yaml README.md BUILD.md ./
COPY --from=ui /ui/dist ./frontend/dist
# Build-time reference check (spec §19): an unresolvable citation fails the image.
# Builds with no network access must opt out explicitly:
#   docker build --build-arg VERIFY_REFERENCES=0 .
ARG VERIFY_REFERENCES=1
RUN if [ "$VERIFY_REFERENCES" = "1" ]; then python -m snitch.recipes; fi
# Run as a fixed non-root uid (1000, the usual first user on Linux hosts) so the
# bind-mounted ./data stays writable without chown gymnastics on either platform.
RUN useradd --uid 1000 --create-home snitch && mkdir -p /data && chown snitch /data
USER snitch
ENV SNITCH_DATA_DIR=/data
CMD ["uvicorn", "snitch.api:app", "--host", "0.0.0.0", "--port", "8000"]
