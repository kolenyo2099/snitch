# Snitch

Self-hosted satellite change monitoring. Draw an area, pick what kind of change you care
about, and Snitch watches it — raising explained, citable alerts whenever new
Sentinel-1 or Sentinel-2 imagery crosses your threshold.

**[BUILD.md](BUILD.md) is the specification and the progress tracker.** Start there.

## Run it

```bash
docker compose up -d --build
```

API at <http://localhost:8000/api/v1/health>. Compose binds it to `127.0.0.1` only.

## Production

- **Set a password.** With no `ui.password`, the API answers **loopback callers
  only** — frictionless on a desk, refused everywhere else. On a server, deploy
  with `docker compose up`: copy `.env.example` to `.env`, set
  `SNITCH_UI__PASSWORD`, and compose disables loopback trust so every caller must
  present the password. Browsers can sign in at Settings → Access, which mints a
  cookie so imagery, overlays and export links keep working.
- **TLS.** Snitch speaks plain HTTP. Terminate TLS in a reverse proxy (Caddy,
  nginx, Traefik) in front of port 8000 rather than exposing the port directly.
- **Scale workers, not the API.** The API process owns the scheduler and the
  SQLite database: run one api service. Add workers with
  `docker compose up -d --scale worker=3`; long backtests hold one worker each,
  and a worker told to stop re-queues its in-flight job instead of losing it.
- **Back up `./data`.** Everything lives there: the SQLite databases and the
  artifact store. `scripts/backup.sh <dest>` takes a live-safe snapshot
  (`sqlite3 .backup`) and tars it up.
- **Upgrades.** `docker compose up -d --build` replaces the image in place; the
  database schema migrates itself on first start.

## Develop

```bash
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt
```

```bash
.venv/bin/python -m pytest tests -q
```

Run the API and a worker locally:

```bash
.venv/bin/uvicorn snitch.api:app --reload
```

```bash
.venv/bin/python -m snitch.worker
```

Verify every recipe citation resolves (this gates the Docker build):

```bash
.venv/bin/python -m snitch.recipes
```

## Where things are

| Path | What |
|---|---|
| `snitch/db.py` | Schema, connection, the SQLite job queue |
| `snitch/pipeline.py` | The twelve-stage run pipeline and the job handlers |
| `snitch/detectors.py` | The detector contract and its implementations |
| `snitch/alerting.py` | The six alerting rules, incidents, severity |
| `snitch/explain.py` | Deterministic explanations |
| `snitch/export.py` | Evidence bundles and `method.md` |
| `recipes/*.yaml` | The recipe catalogue |
| `config.yaml` | All configuration; every key is env-overridable as `SNITCH_SECTION__KEY` |
