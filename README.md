# TerraWatch

Self-hosted satellite change monitoring. Draw an area, pick what kind of change you care
about, and TerraWatch watches it — raising explained, citable alerts whenever new
Sentinel-1 or Sentinel-2 imagery crosses your threshold.

**[BUILD.md](BUILD.md) is the specification and the progress tracker.** Start there.

## Run it

```bash
docker compose up -d --build
```

API at <http://localhost:8000/api/v1/health>.

## Develop

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements.txt
```

```bash
.venv/bin/python -m pytest tests -q
```

Run the API and a worker locally:

```bash
.venv/bin/uvicorn terrawatch.api:app --reload
```

```bash
.venv/bin/python -m terrawatch.worker
```

Verify every recipe citation resolves (this gates the Docker build):

```bash
.venv/bin/python -m terrawatch.recipes
```

## Where things are

| Path | What |
|---|---|
| `terrawatch/db.py` | Schema, connection, the SQLite job queue |
| `terrawatch/pipeline.py` | The twelve-stage run pipeline and the job handlers |
| `terrawatch/detectors.py` | The detector contract and its implementations |
| `terrawatch/alerting.py` | The six alerting rules, incidents, severity |
| `terrawatch/explain.py` | Deterministic explanations |
| `terrawatch/export.py` | Evidence bundles and `method.md` |
| `recipes/*.yaml` | The recipe catalogue |
| `config.yaml` | All configuration; every key is env-overridable as `TW_SECTION__KEY` |
