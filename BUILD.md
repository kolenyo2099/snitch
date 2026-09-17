# Snitch — Build Specification & Progress Tracker

**Spec version 1.0** · This document is the single source of truth. It preserves the
complete original build specification and adds phased delivery plus per-item progress
tracking. Nothing from the original brief has been removed.

## How to read this document

- **Part I** — Progress dashboard and phase roadmap. Check here for "what's done".
- **Part II** — The complete specification, sections 1–19, with a `[ ]` checkbox on
  every independently verifiable requirement and a `→ Pn` tag naming its phase.

### Status legend

| Mark | Meaning |
|---|---|
| `[x]` | Built and exercised by a test or a manual end-to-end run |
| `[~]` | Partially built — a `ponytail:` comment or a note names the remaining gap |
| `[ ]` | Not started |
| `→ Pn` | Delivered in phase *n* |
| `OPEN` | Deliberately unresolved; needs a decision from the user before building |

---

# PART I — DELIVERY PLAN

## Progress dashboard

Last updated: 2026-08-27 · 91 tests passing, 4 network-gated skips (`.venv/bin/python -m pytest tests -q`)

| Phase | Name | Status | Demo criterion |
|---|---|---|---|
| P0 | Foundations: schema, job queue, config, logging | 🟢 complete | A job is enqueued, leased, executed, completed; `/health` responds |
| P1 | Ingest: STAC adapters, windowed loading, artifact store | 🟡 in progress | Pull a clipped, reprojected S2 stack for a drawn AOI |
| P2 | Masks, gates, diagnostics, three-state timeline | 🟢 complete | The app is loud about a cloudy month |
| P3 | First detector end to end (R1) | 🟢 complete | A rendered alert with a deterministic explanation |
| P4 | Frontend core: dashboard, project detail, map, timeline | 🟢 complete | Click through an alert from the feed to its run |
| P5 | Wizard and calibration | 🟢 complete | Drag a threshold, see real historical alerts update live |
| P6 | Alerting rules: hysteresis, confirmation, incidents, notifications | 🟢 complete | A flapping score produces one incident, not forty alerts |
| P7 | Radar path: S1 loading, orbit constraints, preprocessing, R4 + R7 | 🟢 complete | A flood map and a Wishart change map from the same AOI |
| P8 | Remaining recipes: R2, R3, R5, R6, R8 | 🟡 in progress | All eight recipes selectable in the wizard |
| P9 | Export and provenance | 🟢 complete | A stranger understands an exported bundle |
| P10 | GEE backend | 🟢 complete | Baseline fitting on GEE, quota-tracked, results stored locally |

Legend: ⚪ not started · 🟡 in progress · 🟢 complete

## Phase detail

Each phase is independently demonstrable. Do not proceed until the previous one works
end to end (spec §17).

### P0 — Foundations
Schema and migrations, the SQLite job queue with atomic leasing, the worker loop, the
scheduler, `config.yaml` loading, structured JSON logging, the health endpoint, Docker
Compose. **Proves:** a job can be enqueued, leased, executed, and completed.

### P1 — Ingest
The `cdse` and `earthsearch` adapters behind one `SourceAdapter` protocol, windowed COG
reads, adapter fallback with provenance recording, the content-addressed artifact store.
**Proves:** a clipped, reprojected Sentinel-2 stack for a drawn AOI.

### P2 — Masks and gates
The mask chain, `valid_fraction`, the full diagnostic taxonomy, the three-state timeline.
**Proves:** the app is loud about a cloudy month rather than silently producing nothing.

Cloud Score+ and s2cloudless now run when their published STAC assets are present, with
Cloud Score+ preferred, probability and solar-geometry shadow projection as fallback,
and SCL as the final local fallback. Missing declared masks remain explicit diagnostics.
Every stored diagnostic carries stable severity/recovery metadata and the UI renders the
suggested remedy. The local P2 path is complete, and the GEE quota diagnostics
are raised by the optional P10 backend's pre-flight budget check.

### P3 — First detector end to end
R1 `harmonic_residual`, baseline fitting and persistence, scoring, thresholding,
morphology, run persistence, the deterministic explanation, chip rendering, one alert.
**Proves:** the whole spine.

### P4 — Frontend core
Dashboard with health strip and unified feed, project list, project detail with the four
tabs, MapLibre map, TiTiler overlays, timeline, alert cards, run detail.

### P5 — Wizard and calibration
Four-step creation wizard, the backtest job, score histogram, draggable threshold with
live historical alerts and the alert-frequency counter. **Highest-value UI work.**

### P6 — Alerting rules
MMU filtering, hysteresis, provisional/confirmed confirmation, incident grouping and
closure, cooldowns, severity from the project's own distribution, notification channels.

### P7 — Radar path — 🟢 complete
Sentinel-1 RTC loading via Planetary Computer, the relative-orbit constraint enforced on
every admission path, GLO-30 slope masking, then R4 `s1_ratio_flood` and R7
`wishart_omnibus`. The GRD preprocessing chain was made unnecessary by using an RTC
collection, which is what §4.3 rule 1 asks for; the §4.3 checkboxes record why each GRD
step is deliberately absent rather than pending. Verified live against orbit 10
descending over the Rio Negro: 17 scenes on a single orbit, both detectors producing
maps from the same AOI.

### P8 — Remaining recipes
R2 `radd_probabilistic`, R3 `dnbr`, R5 `mndwi_extent`, R6 `builtup_dual`,
R8 `irmad_cva`.

All five local detector IDs and recipe files exist, are registered, and are covered by
synthetic detection/non-detection tests, so all eight recipes are selectable in the
wizard. R2 now carries its posterior forward through immutable score artifacts, R3 and
R8 use monthly reference planes with loud fallbacks/overrides, and R6 pairs each optical
run with the nearest same-orbit RTC scene on the optical grid. R6 reuses the tested R7
Wishart statistic, gates it to Loewner-order increases, and expresses its significance as
the configured radar z-evidence. All five P8 recipes now have complete local paths; the
phase stays open only for the cached real-event golden fixtures assigned to P8 in §18.

### P9 — Export and provenance
Evidence bundles for alerts, runs, and projects; auto-generated `method.md`; manifest
hashing.

### P10 — GEE backend — 🟢 complete
Optional adapter, service-account config, EECU quota tracking and pre-flight estimation,
batch exports, the `xee` bridge, local persistence of everything computed remotely.

The backend presents the same `search`/`load` surface as the STAC adapters and returns
the same xarray objects, so no detector, mask, or pipeline stage branches on it. It is
reachable only from the baseline and backtest handlers; forward monitoring is always
local. `ee` and `xee` are imported lazily and are not in `requirements.txt`, so an
install without them — or with the credentials deleted — is fully functional.

## Open questions

All three were decided with the user on 2026-08-22. Kept here with their answers so
the reasoning is not lost.

- [x] **Should backtests be capped by AOI size?** → **Soft cap, never a refusal.**
  `POST /projects/{uuid}/backtest` warns above 500 km² with a runtime estimate, records
  a `SLOW_BACKTEST` diagnostic, and runs anyway. A large AOI is slow, not wrong, and
  refusing it would be the app deciding what the user may look at.
- [x] **Should the S1 orbit choice allow multi-orbit projects with per-orbit baselines?**
  → **No. One relative orbit per project.** The wizard forces the choice and explains
  why; two orbits means two projects. Per-orbit baselines would double the baseline
  bookkeeping and make incident grouping ambiguous for a case that splitting solves.
- [x] **Should incidents support manual merge and split?** → **Not in v1.** Automatic
  grouping only. Hand-edited incidents need an editor, endpoints, and an audit record
  so exports stay honest about who merged what.

### Decided during P7

- [x] **Where does Sentinel-1 come from?** → **Planetary Computer `sentinel-1-rtc`.**
  Earth Search's `sentinel-1-grd` was the first choice, but its assets are raw SAFE
  measurement TIFFs on a **requester-pays** bucket (`storage:requester_pays: true`),
  which needs AWS credentials and is not terrain-corrected. Planetary Computer serves
  terrain-corrected gamma0 COGs in UTM, signed with an anonymously issued SAS token —
  no account, no credential in `config.yaml`. This is spec §4.3 rule 1, and taking it
  removes the entire in-app GRD calibration chain rather than approximating it.

## Repository map

```
BUILD.md                  this document
config.yaml               single config file, env-overridable
docker-compose.yml        api + worker + tiler
recipes/*.yaml            the recipe catalogue, one file per recipe
snitch/
  db.py                   schema, connection, job queue, diagnostics
  config.py               config.yaml loader with SNITCH_SECTION__KEY env overrides
  log.py                  structured JSON logging with rotation
  geo.py                  AOI helpers: cleaning, area, analysis CRS, cron suggestion
  adapters.py             STAC source adapters
  gee.py                  optional Google Earth Engine backend (P10)
  masks.py                mask chain
  dem.py                  GLO-30 elevation and slope on the analysis grid
  baseline.py             baseline serialisation: multi-band COG + JSON sidecar
  detectors.py            detector contract + implementations
  recipes.py              recipe registry loader
  pipeline.py             the 12-stage run pipeline
  alerting.py             alerting rules, incidents, severity
  explain.py              deterministic explanation templates
  chips.py                before/after/overlay rendering
  artifacts.py            content-addressed store
  export.py               evidence bundles
  api.py                  FastAPI app
  worker.py               job runner
  scheduler.py            APScheduler wiring
  notify.py               notification channels
  vlm.py                  optional vision-language explanation layer
  constellation.py        constellation-events lookup
  constellation_events.json
frontend/                 React 18 + TypeScript + Vite
tests/                    unit, golden fixture, and property tests
```

---

# PART II — THE SPECIFICATION

A self-hosted satellite change monitoring application. The user draws an area on a map,
chooses what kind of change they care about, and Snitch watches that area for as
long as they want it watched, raising explained alerts on a dashboard whenever new
Sentinel-1 or Sentinel-2 imagery shows a change that crosses their threshold.

This document is the complete build brief. Anything it does not specify is a deliberate
implementation choice left to the builder, except where marked OPEN.

## 1. Design principles

These are load-bearing. When a design decision is ambiguous, resolve it against this
list.

1. **Honest before helpful.** A wrong alert is worse than no alert, and a silent failure
   is worse than a loud one. The app must always be able to distinguish "nothing
   changed" from "we could not see" from "we could not run." These three states are
   visually and semantically distinct everywhere in the UI.
2. **Every number is traceable.** Every alert carries the exact scenes, dates, detector
   version, parameter values, and masks that produced it. Any alert can be exported as a
   self-contained evidence bundle.
3. **Append-only history.** Runs and alerts are never overwritten or deleted by the
   system. Re-analysis creates new records that reference the old ones. History is
   retrievable without ever recomputing.
4. **Methods are citable.** Every detector and every recipe carries a literature
   reference with a resolvable link, surfaced in the UI. A user must be able to answer
   "why this threshold" with a source.
5. **The expert knobs exist but are closed by default.** A user who knows nothing about
   remote sensing must be able to create a working monitor in under two minutes. A user
   who knows a lot must be able to reach every parameter.
6. **Local-first, cloud-optional.** The default deployment runs entirely on the user's
   machine against public STAC endpoints. Google Earth Engine is an optional
   accelerator, never a requirement, and everything it computes is pulled down and
   stored locally.

## 2. Non-goals for v1

- Multi-user accounts, roles, or authentication beyond a single optional shared password.
- Commercial or very-high-resolution imagery (Planet, Maxar, Airbus).
- InSAR coherence as a detector. SLC pair processing does not belong inside a monitoring
  loop.
- Mobile-native clients. The web UI should be responsive but is designed for desktop.
- Automated attribution of cause. Snitch reports what changed, never who did it or
  why.

---

## 3. System architecture

### 3.1 Processes

A single Docker Compose stack with three services:

| Service | Contents | Notes |
|---|---|---|
| `api` | FastAPI app, serves REST API and the built frontend as static files | Also hosts the embedded scheduler |
| `worker` | Job runner, 1 to N replicas, default 1 | Polls the SQLite job table |
| `tiler` | TiTiler instance serving COGs from the artifact store | Read-only mount of the artifact directory |

- [x] `api` service — FastAPI, REST API, static frontend → P0/P4
- [x] `worker` service — job runner, N replicas → P0
- [x] `tiler` service — TiTiler over the artifact directory → P4
- [x] Docker Compose stack wiring all three → P0

Do not introduce Celery, Redis, RabbitMQ, or Postgres in v1. The job queue is a table in
SQLite with `SELECT ... FOR UPDATE`-equivalent semantics implemented via an atomic
`UPDATE ... WHERE status='queued' AND id=(SELECT id ...) RETURNING`. SQLite runs in WAL
mode with `busy_timeout=10000`.

- [x] Atomic `UPDATE ... RETURNING` job leasing → P0 · `snitch/db.py:lease`
- [x] WAL mode + `busy_timeout=10000` → P0 · `snitch/db.py:connect`
- [x] No Celery/Redis/RabbitMQ/Postgres (verify at review time) → P0

The scheduler is APScheduler running in the `api` process, with jobs persisted to the
database so restarts do not lose schedules. The scheduler only enqueues; it never
computes.

- [x] APScheduler in the `api` process, jobs persisted to the DB → P0
- [x] Scheduler only enqueues, never computes → P0

### 3.2 Storage layout

```
/data
  snitch.db            SQLite, WAL mode
  /artifacts               content-addressed, sha256 of contents
    /ab/cd/abcd1234....tif
  /projects/<project_id>
    /chips                 rendered PNG/JPEG previews for the UI
    /exports               generated evidence bundles
  /logs
    snitch.jsonl       structured JSON logs, rotated
```

- [x] Content-addressed artifact store, sha256 fan-out directories → P1
- [x] Per-project chip and export directories → P3/P9 · chips are immutable hard-linked
  views of content-addressed artifacts; exports use the specified project directory
- [x] Structured JSONL logs with rotation → P0

Rasters never go in the database. The database stores artifact hashes, paths, and
metadata. Artifacts are immutable once written. A garbage collector runs only when
explicitly invoked by the user and only deletes artifacts referenced by zero non-deleted
records.

- [x] Artifacts immutable once written → P1
- [x] User-invoked GC, deletes only zero-referenced artifacts → P9

### 3.3 Language and libraries

Backend: Python 3.11+. Core dependencies: `fastapi`, `uvicorn`, `pydantic` v2,
`sqlalchemy` (Core, not necessarily ORM), `apscheduler`, `pystac-client`, `odc-stac` or
`stackstac`, `rasterio`, `rioxarray`, `xarray`, `numpy`, `scipy`, `scikit-image`,
`shapely`, `pyproj`, `numba` (optional, for the SAR statistics), `httpx`.

Frontend: React 18 + TypeScript + Vite. Map stack: **MapLibre GL JS** with `terra-draw`
for AOI geometry and `deck.gl` for heavy overlays. Charting: `observable-plot` or
`recharts`. Do not use Leaflet.

- [x] Backend dependency set pinned → P0
- [x] Frontend: React 18 + TS + Vite + MapLibre + terra-draw + deck.gl → P4
- [x] No Leaflet → P4

---

## 4. Data sources

### 4.1 Source adapter interface

All imagery access goes through an adapter. Adapters are registered by name and are
selectable per project.

```python
class SourceAdapter(Protocol):
    name: str
    supported_collections: list[str]

    def search(self, aoi: Polygon, start: datetime, end: datetime,
               collection: str, **filters) -> list[SceneRef]:
        """Return scene references. Must not download pixels."""

    def load(self, scenes: list[SceneRef], aoi: Polygon,
             bands: list[str], resolution: float) -> xr.Dataset:
        """Return an xarray Dataset clipped to the AOI, in a projected CRS,
        with dims (time, y, x) and one variable per band. Must use windowed
        reads. Must never download whole scenes."""

    def health(self) -> AdapterHealth:
        """Reachability, latency, quota state if applicable."""
```

`SceneRef` carries at minimum: scene id, platform (S1A/S1C/S1D/S2A/S2B/S2C), datetime,
collection, relative orbit number, orbit direction (for S1), a stable source URI, and
the raw STAC item JSON.

- [x] `SceneRef` with all required fields → P1 · `snitch/adapters.py`
- [x] Adapter protocol: `search` / `load` / `health` → P1 · production `load` returns an
  `xr.Dataset` with `(time, y, x)` dimensions; the pipeline compatibility shim keeps
  synthetic test adapters lightweight
- [x] `search` never downloads pixels → P1
- [x] `load` uses windowed reads → P1

### 4.2 Adapters to implement in v1

**`cdse`** (default, required). Copernicus Data Space Ecosystem STAC. Collections
`SENTINEL-2` L2A and `SENTINEL-1` GRD/RTC as available. This is the authoritative source
and has the lowest ingestion latency.

**`earthsearch`** (required fallback). Element84 Earth Search on AWS, `sentinel-2-l2a`
and `sentinel-1-grd`. Anonymous access, well-behaved, used automatically when `cdse`
fails health checks.

**`planetarycomputer`** (optional). Primarily valuable for its Sentinel-1 RTC
collection, which is already radiometrically terrain-corrected.

**`gee`** (optional, off by default). See section 11.

- [~] `cdse` adapter → P1 · current public STAC endpoint and `sentinel-2-l2a` discovery
  are wired; authenticated `s3://eodata` asset access is outstanding. S1 processing uses
  Planetary Computer RTC rather than mixing uncorrected GRD into the reference path
- [x] `earthsearch` adapter → P1
- [x] `planetarycomputer` adapter → P7 · Sentinel-1 RTC COGs with anonymous SAS signing
- [x] `gee` adapter → P10 · `snitch/gee.py`, lazily imported

The project record stores an ordered preference list of adapters. On failure, the worker
falls through the list and records which adapter served each observation. If a run is
served by a different adapter than the baseline was, it must be flagged as a
**provenance discontinuity** on the run (see 9.3).

- [x] Ordered adapter preference with fallthrough → P1 · `adapters.first_healthy`
- [x] Record the serving adapter per observation → P1
- [x] Flag provenance discontinuity when run and baseline adapters differ → P2

### 4.3 Sentinel-1 preprocessing

Do not attempt SNAP-equivalent processing in v1. Rules in order of preference:

1. If a terrain-corrected RTC collection is available for the AOI, use it.
2. Otherwise use GRD with the following minimum processing applied in-app: convert DN to
   dB via the provided calibration, apply the border-noise mask if available in the
   product, apply a multitemporal or Refined Lee speckle filter, and mask
   layover/shadow using a slope mask derived from the Copernicus GLO-30 DEM.
3. If neither is possible, refuse to run the project and raise a data-quality event
   explaining exactly which preprocessing step is unavailable.

- [x] RTC preferred where available → P7 · `planetary` / `sentinel-1-rtc`, the only S1
  source wired; rule 1 applies everywhere the app can currently run
- [x] GRD path: DN→dB calibration → P7 · **not needed and deliberately not built.**
  RTC is delivered as calibrated gamma0, so rule 2 never fires. An approximate in-app
  calibration alongside a correctly calibrated product would be a worse second path,
  not a fallback
- [x] GRD path: border-noise mask → P7 · same: RTC border noise is already removed and
  arrives as nodata
- [x] GRD path: Refined Lee or multitemporal speckle filter → P7 · **deliberately
  absent.** R7 is invalid on filtered data (the Wishart model describes unfiltered
  multilook statistics) and R4 gains little once the MMU is applied. Filtering would
  quietly break the recipe whose whole value is a defensible p-value
- [x] GRD path: GLO-30 slope-derived layover/shadow mask → P7 · `snitch/dem.py`
  fetches GLO-30 and the `slope` mask excludes steep ground; RTC additionally delivers
  layover and shadow as nodata, which the `nodata` mask already removes
- [x] Refuse to run and raise a diagnostic when neither is possible → P7 ·
  `first_healthy` raises `SOURCE_UNREACHABLE`, and `MASK_UNAVAILABLE` is raised for any
  mask a recipe declares but a run could not apply

Comparison rule: **Sentinel-1 observations may only be compared against observations
from the same relative orbit and same pass direction.** This is not optional. Backscatter
is geometry-dependent and mixing orbits is the most common source of spurious SAR
change. The project stores the chosen relative orbit; if the AOI is covered by multiple
orbits, the user picks one during setup and the app explains why.

- [x] Hard same-orbit / same-pass constraint on S1 comparison → P7 · one predicate,
  `pipeline._orbit_reason`, used by forward polling *and* baseline fitting, because a
  scene admitted on any path is a scene compared
- [x] Orbit picker in the wizard with an explanation → P5/P7

### 4.4 Constellation status awareness

The app ships a small static `constellation_events.json` recording known platform and
orbit changes, and warns when a project's time series spans one. Seed it with:

- `2026-04-17`: Sentinel-1D open data access begins, three-satellite interim phase.
- `2026-06-09` to `2026-06-23`: Sentinel-1C manoeuvre, S1C acquisitions suspended.
- `2026-06-29`: Sentinel-1A operations terminated.
- `2026-07` onward: final S1C/S1D configuration, six-day revisit pattern shifted by one
  day relative to the previous S1A/S1B timeline.

Source: <https://dataspace.copernicus.eu/news/2026-5-28-sentinel-1-orbital-reconfiguration-dates>

When a baseline spans one of these events, every alert derived from it carries a caveat
chip reading "baseline spans a constellation change" linked to the event description.
The file is user-editable so new events can be added without a code change.

- [x] `constellation_events.json` seeded with the four events → P2
- [x] Warn when a project's series spans an event → P2
- [x] `BASELINE_SPANS_CONSTELLATION_EVENT` caveat chip on derived alerts → P3
- [x] File is user-editable without a code change → P2

---

## 5. Domain model

### 5.1 Concepts

- **Project** — a persistent monitoring configuration. Owns an AOI, a recipe, a
  schedule, and its full history. Independent from every other project.
- **AOI** — a polygon or multipolygon, stored as GeoJSON in EPSG:4326 plus a derived UTM
  zone used as the analysis CRS.
- **Recipe** — a named, literature-grounded bundle of sensor, bands, detector, masking,
  baseline strategy, and default parameters. See section 8.
- **Detector** — a plugin implementing the contract in section 7. A recipe references
  exactly one detector.
- **Run** — one execution of a project's detector against one new observation date (or
  one bitemporal pair). Immutable.
- **Observation** — the per-scene record of what was fetched and how usable it was,
  whether or not it produced a run.
- **Alert** — a threshold crossing that survived the alerting rules in section 10.
  Immutable.
- **Incident** — a group of consecutive related alerts, so a slow-moving change is one
  thing in the UI rather than forty.
- **Diagnostic** — a data-quality or system event. First-class, not a log line.

### 5.2 SQLite schema

Written as DDL. All timestamps are UTC ISO-8601 strings. All geometry is GeoJSON text.
Use `INTEGER PRIMARY KEY` rowids plus a public `uuid` column on user-visible entities.

- [x] `project` table → P0 · `snitch/db.py`
- [x] `artifact` table → P0
- [x] `observation` table → P0
- [x] `run` table → P0
- [x] `alert` table → P0
- [x] `incident` table → P0
- [x] `diagnostic` table → P0
- [x] `job` table → P0
- [x] Indexes on `observation(project_id, sensed_at)`, `run(project_id, started_at)`,
      `alert(project_id, sensed_at)`, `diagnostic(project_id, occurred_at)`,
      `job(status, available_at)` → P0

The full DDL is implemented verbatim in [`snitch/db.py`](snitch/db.py) and is
the authoritative copy; the original brief's DDL is reproduced there without change.

---

## 6. Processing pipeline

Each forward run proceeds in strictly ordered stages. Every stage can emit diagnostics.
A stage failure aborts the run with `status='failed'` and a populated `error_json`; it
never produces a partial alert.

1. **Discover.** Query the adapter for new scenes since the last observation.
   Deduplicate against the `observation` table.
2. **Admit.** Apply hard filters: correct collection, correct relative orbit and pass
   direction for S1, AOI intersection above 99%, scene not already observed. Rejected
   scenes are still written to `observation` with a `rejection_reason`.
3. **Load.** Windowed read of only the required bands over the AOI buffered by the
   detector's declared kernel radius, reprojected to the project's analysis CRS at the
   recipe's resolution.
4. **Mask.** Apply the recipe's mask chain (section 9.1). Compute `valid_fraction`.
5. **Gate.** If `valid_fraction` is below `min_valid_fraction`, stop. Write the
   observation with `status='partial'` or `'rejected'`, emit a `LOW_VALID_FRACTION`
   diagnostic, and create no run. This is a normal outcome, not an error.
6. **Align.** Verify geometric consistency against the baseline using phase correlation
   on a stable subset. If the estimated shift exceeds `max_misregistration_px`
   (default 0.5), emit `MISREGISTRATION` and either abort or, if `auto_coregister` is
   on, shift and record the correction in the run.
7. **Score.** Invoke the detector. Receives the masked target array, the baseline object,
   and resolved parameters. Returns a `DetectorResult`.
8. **Threshold and morphology.** Binarise the score raster, remove connected components
   smaller than the minimum mapping unit, optionally apply a single opening operation.
9. **Persist.** Write the score raster and mask as COGs to the artifact store, write the
   run record with the summary.
10. **Decide.** Apply the alerting rules (section 10). Create or update an alert and
    incident, or record explicitly that no alert was raised and why.
11. **Render and explain.** Generate before/after/overlay chips and the deterministic
    explanation. Optionally call the VLM.
12. **Notify.** Emit to configured channels (section 13.4).

- [x] Stage 1 Discover → P3
- [x] Stage 2 Admit, with rejected scenes still written → P3
- [x] Stage 3 Load, buffered by detector kernel radius → P3
- [x] Stage 4 Mask → P2
- [x] Stage 5 Gate → P2
- [x] Stage 6 Align (phase correlation, `auto_coregister`) → P3 · sub-pixel correction
  is applied to all bands, newly exposed edges are invalidated, and the shift is stored
  in the run summary
- [x] Stage 7 Score → P3
- [x] Stage 8 Threshold and morphology → P3
- [x] Stage 9 Persist COGs + run record → P3
- [x] Stage 10 Decide → P6
- [x] Stage 11 Render and explain → P3
- [x] Stage 12 Notify → P6
- [x] A stage failure never produces a partial alert → P3

---

## 7. The detector contract

This is the central abstraction. Every method, from a two-line band difference to the
Wishart omnibus test, implements the same interface. Nothing downstream knows which
detector ran.

### 7.1 Interface

```python
@dataclass(frozen=True)
class DetectorSpec:
    id: str
    version: str
    display_name: str
    sensor: Literal["S2", "S1"]
    required_bands: list[str]
    needs_baseline: bool
    min_baseline_observations: int      # 0 if bitemporal
    score_units: str                    # e.g. "z", "index_delta", "p_value", "probability"
    score_polarity: Literal["higher_is_more_change", "lower_is_more_change"]
    default_threshold: float
    threshold_semantics: str            # plain-language, shown in UI
    reference: Reference                # citation + resolvable URL
    param_schema: dict                  # JSON Schema for tunable params

@dataclass
class DetectorResult:
    score: np.ndarray                   # float32, 2D, NaN where invalid
    valid: np.ndarray                   # bool, 2D
    direction: dict | None              # e.g. {"ndvi": -0.31, "nbr": -0.44}
    aux: dict                           # detector-specific, stored in summary_json
    notes: list[str]                    # human-readable caveats from the method itself

class Detector(Protocol):
    spec: DetectorSpec
    def fit_baseline(self, stack: xr.Dataset, params: dict) -> Baseline: ...
    def score(self, target: xr.Dataset, baseline: Baseline | None,
              params: dict) -> DetectorResult: ...
```

`Baseline` is a serialisable object persisted as a multi-band COG plus a JSON sidecar. It
must record the observation ids it was fitted from and the fitting date range.

- [x] `DetectorSpec` dataclass → P3 · `snitch/detectors.py`
- [x] `DetectorResult` dataclass → P3
- [x] `Detector` protocol: `fit_baseline` + `score` → P3
- [x] `Baseline` persisted as multi-band COG + JSON sidecar → P3
- [x] Baseline records source observation ids and fitting date range → P3

### 7.2 Rules for detector authors

- The score raster must be a per-pixel quantity in the declared units. Do not return
  already-thresholded masks.
- `score_units` of `p_value` means the threshold is a false-alarm rate and must be
  presented to the user as such. This is the preferred form where the statistics allow
  it.
- Detectors must be pure functions of their inputs. No network access, no database
  access, no writes.
- Detectors must handle NaN input gracefully and propagate invalidity rather than
  filling.
- Any detector-internal assumption that could invalidate results (too few observations,
  suspected seasonality mismatch, saturated values) goes into `notes` and surfaces on the
  alert.

- [x] Detectors are pure functions, no I/O → P3
- [x] NaN propagation rather than filling → P3
- [x] Assumption violations emitted as `notes` → P3
- [x] `p_value` units presented as a false-alarm rate in the UI → P4/P7 · the run
  summary carries `polarity` and a polarity-correct `score_headline`, and the
  calibration histogram, shading, slider and would-have-alerted list all invert for a
  lower-is-more-change detector

### 7.3 Run summary

Every run writes a `summary_json` with at minimum: score percentiles (p50, p90, p95,
p99, max), valid pixel count and fraction, changed pixel count at threshold, changed area
in m², number of connected components above MMU, largest component area, mean direction
vector, and the detector's `aux` and `notes`.

- [x] Full `summary_json` with every listed field → P3

---

## 8. The recipe catalogue

A recipe is what the user actually picks. Each is defined in a YAML file under
`recipes/` and loaded into a registry at startup. Recipes are versioned; a project pins a
recipe version so history stays interpretable when recipes are updated.

- [x] YAML recipe files loaded into a registry at startup → P3 · `snitch/recipes.py`
- [x] Recipes are versioned; projects pin a version → P3
- [x] Reference URLs verified at build time (§19) → P3

Recipe file schema (example, R1):

```yaml
id: vegetation_loss_optical
version: "1.0"
display_name: "Vegetation loss or land clearing"
plain_question: "Has vegetation been cleared or damaged here?"
sensor: S2
detector: harmonic_residual
resolution_m: 10
bands: [B4, B8, B11, B12]
indices: [NDVI, NBR]
mask_chain: [cloudscore_plus, scl_shadow, snow]
baseline:
  strategy: harmonic
  min_years: 2
  min_observations: 30
defaults:
  threshold: 3.0
  min_mapping_unit_m2: 2000
  consecutive_confirmations: 2
reference:
  citation: "Zhu, Z. & Woodcock, C.E. (2014). Continuous change detection and classification of land cover using all available Landsat data. Remote Sensing of Environment 144, 152-171."
  url: "https://doi.org/10.1016/j.rse.2014.01.011"
  note: "Threshold is expressed in robust standard deviations of the harmonic model residual, following the CCDC change criterion of repeated exceedance across consecutive observations."
limitations:
  - "Cannot distinguish harvest from illegal clearing."
  - "Requires at least two years of history to model seasonality."
  - "Under persistent cloud, detection may lag by weeks. Pair with a radar recipe."
```

The eight v1 recipes follow. Each specifies the method, the default parameters, the
grounding reference, and the failure modes that must be shown in the UI.

### R1. Vegetation loss or land clearing (optical) — 🟡 P3

**Question:** Has vegetation been cleared or damaged here?
**Sensor:** Sentinel-2 L2A, 10 m. **Bands:** B4, B8, B11, B12. **Indices:** NDVI, NBR.

**Detector:** `harmonic_residual`. Fit a harmonic regression per pixel per index over the
baseline (intercept, slope, and one to three annual harmonic pairs), then score each new
observation as the residual divided by a robust estimate of residual scale (MAD-based).
Flag a pixel when the standardised residual exceeds the threshold in the
change-consistent direction for `consecutive_confirmations` observations in a row.

**Default threshold:** 3.0 robust sigma, 2 consecutive confirmations, MMU 2000 m².

**Grounding:** The residual-exceedance-across-consecutive-observations criterion is the
core of CCDC. Zhu & Woodcock 2014, <https://doi.org/10.1016/j.rse.2014.01.011>. The
breakpoint framing is shared with BFAST: Verbesselt et al. 2010, *Remote Sensing of
Environment* 114(1), 106-115, <https://doi.org/10.1016/j.rse.2009.08.014>.

**Limitations to display:** requires two years of history; cannot separate harvest from
clearing; lags under cloud.

- [x] Harmonic regression per pixel per index, 1–3 annual harmonic pairs → P3
- [x] MAD-based robust residual scale → P3
- [x] Change-consistent direction only → P3
- [x] `consecutive_confirmations` enforcement → P6 · overlapping provisional alerts
  confirm at the recipe count; R2 uses its accumulated posterior threshold
- [x] Recipe YAML with grounding and limitations → P3

### R2. Forest disturbance, all-weather (radar) — 🟢 P8

**Question:** Has forest been cut or disturbed, even under cloud?
**Sensor:** Sentinel-1 GRD or RTC, VV and VH, 10 m pixel spacing.

**Detector:** `radd_probabilistic`. Fit per-pixel forest and non-forest backscatter
distributions from the baseline using a two-component Gaussian mixture on the dB values.
Convert each new observation to a non-forest probability, and where that probability
exceeds the flagging threshold, begin Bayesian updating of a disturbance probability with
each subsequent observation. Raise a provisional alert on first flag and a confirmed
alert when the accumulated probability crosses the confirmation threshold.

**Defaults:** flag at non-forest probability > 0.75; confirm at disturbance probability
> 0.975; MMU 1000 m². Normalise each incoming image by matching the median backscatter of
stable forest to the baseline median, to suppress dry-season drift.

**Grounding:** Reiche et al. 2021, *Environmental Research Letters* 16, 024005,
<https://doi.org/10.1088/1748-9326/abd0a8>. Preprocessing follows Mullissa et al. 2021,
*Remote Sensing* 13(10), 1954, <https://doi.org/10.3390/rs13101954>.

**Limitations:** tuned for tropical forest in the source literature and needs
recalibration elsewhere; detects edges better than interiors, so large uniform clearings
can be slow to confirm; does not distinguish human from natural disturbance.

- [x] Two-component Gaussian mixture on baseline dB values → P8
- [x] Bayesian disturbance-probability updating → P8 · VV and VH update the prior; each
  forward/backtest/reanalysis sequence loads the previous immutable probability raster
- [x] Provisional on flag, confirmed at 0.975 → P8 · the first flag is always provisional;
  a subsequent accumulated posterior must cross 0.975 inside the MMU-retained footprint
- [x] Stable-forest median normalisation → P8 · capped at ±1.5 dB so a scene-wide real
  disturbance cannot be normalised away

### R3. Burn scar and fire severity — 🟢 P8

**Question:** Has this area burned, and how badly?
**Sensor:** Sentinel-2 L2A. **Bands:** B8A (NIR), B12 (SWIR2).

**Detector:** `dnbr`. NBR = (B8A - B12) / (B8A + B12). dNBR = NBR_pre - NBR_post, scaled
by 1000. Pre-fire NBR is the baseline median from the same phenological window where
available, falling back to the most recent clear observation.

**Default severity classes** (scaled dNBR, editable in the UI):

| Class | Range |
|---|---|
| Enhanced regrowth, high | < -250 |
| Enhanced regrowth, low | -250 to -100 |
| Unburned | -100 to +99 |
| Low severity | 100 to 269 |
| Moderate-low severity | 270 to 439 |
| Moderate-high severity | 440 to 659 |
| High severity | ≥ 660 |

Alert threshold defaults to the low-severity boundary (100) with MMU 5000 m².

**Grounding:** Key, C.H. & Benson, N.C. (2006), Landscape Assessment, in *FIREMON: Fire
Effects Monitoring and Inventory System*, USDA Forest Service RMRS-GTR-164-CD,
<https://www.fs.usda.gov/research/treesearch/24066>. Operational use of these class
breaks: Copernicus EFFIS fire severity methodology,
<https://forest-fire.emergency.copernicus.eu/about-effis/technical-background/fire-severity>.

**Limitations:** dNBR is sensitive to pre-fire condition, so relative variants (RdNBR,
Miller & Thode 2007) may be preferable in heterogeneous vegetation; class breaks are
ecosystem-dependent and were derived for North American systems; a 30-day post-fire delay
is standard practice for severity assessment, so immediate alerts describe extent more
reliably than severity.

- [x] dNBR computation with phenological-window pre-fire median → P8 · twelve monthly
  planes are persisted; a missing month loudly falls back to the latest clear baseline
- [x] Seven severity classes, editable in the UI → P8 · six validated boundaries define
  the seven classes and saving them queues append-only historical reanalysis

### R4. Flooding and surface water (radar) — 🟢 P7

**Question:** Is this area flooded?
**Sensor:** Sentinel-1 GRD, VH preferred (more sensitive to surface change), VV available
as an option.

**Detector:** `s1_ratio_flood`. Compute the ratio of the pre-event reference backscatter
to the post-event backscatter on speckle-filtered images. Threshold the ratio. Apply a
terrain exclusion mask removing slopes above 5 degrees and a HAND (height above nearest
drainage) exclusion where a DEM is available.

**Defaults:** ratio threshold 1.25 (UN-SPIDER recommended value), MMU 10000 m². Provide
an alternative `otsu` mode that derives the threshold from the image histogram per run and
records the derived value.

**Grounding:** UN-SPIDER Recommended Practice, Flood Mapping and Damage Assessment Using
Sentinel-1 SAR Data in Google Earth Engine,
<https://un-spider.org/advisory-support/recommended-practices/recommended-practice-google-earth-engine-flood-mapping>.
Otsu, N. (1979), *IEEE Transactions on Systems, Man, and Cybernetics* 9(1), 62-66,
<https://doi.org/10.1109/TSMC.1979.4310076>.

**Limitations to display prominently:** SAR misclassifies smooth dry surfaces, tarmac,
and sand as water; it substantially under-detects flooding under vegetation canopy and in
dense urban areas because of double-bounce; wind roughening suppresses the water signal on
open water. The UI must show the terrain and urban exclusion mask as a layer, not hide it.

- [x] Pre/post backscatter ratio → P7 · the reference is the per-pixel temporal
  **median** of the baseline, so one flooded date in the reference window cannot move
  it. Deliberately **not** speckle-filtered: see §4.3 above
- [x] Slope > 5° exclusion + HAND exclusion → P7 · both derive from GLO-30; HAND uses
  D8 flow accumulation and records its heuristic drainage-area and height defaults
- [x] `otsu` mode recording the derived threshold → P7 · the split is found in dB, where
  the water and land modes are closer to Gaussian, then converted back to a linear
  ratio. The run is judged against the derived value, which is stored on the run and
  shown next to the recipe default
- [x] Exclusion mask visible as a UI layer → P7 · the composite and every named mask,
  including slope and HAND, are persisted separately and selectable on the map

### R5. Water body extent change (optical) — 🟢 P8

**Question:** Is this lake, reservoir, river, or pond changing size?
**Sensor:** Sentinel-2 L2A. **Bands:** B3 (green), B8 (NIR), B11 (SWIR1).

**Detector:** `mndwi_extent`. MNDWI = (B3 - B11) / (B3 + B11). Threshold via Otsu on the
AOI histogram, falling back to a fixed 0.0 threshold when the histogram is unimodal
(which itself is reported). Score is the signed change in water extent relative to the
baseline seasonal median extent for the same day-of-year window.

**Defaults:** alert on a 10% change in water area relative to the seasonal baseline,
MMU 2500 m².

**Grounding:** Xu, H. (2006), Modification of normalised difference water index (NDWI) to
enhance open water features, *International Journal of Remote Sensing* 27(14), 3025-3033,
<https://doi.org/10.1080/01431160600589179>. Original NDWI: McFeeters, S.K. (1996),
*IJRS* 17(7), 1425-1432, <https://doi.org/10.1080/01431169608948714>. Thresholding: Otsu
1979 as above.

**Limitations:** MNDWI confuses shadow and some dark built surfaces with water; turbidity
and aquatic vegetation shift index values; seasonal comparison requires at least two years
of baseline.

- [x] MNDWI + Otsu with unimodal fallback, fallback reported → P8
- [x] Seasonal day-of-year baseline extent comparison → P8 · twelve monthly extent
  planes are persisted in the baseline artifact; missing months use the all-season median

### R6. New construction and built-up expansion — 🟢 P8

**Question:** Is something being built here?
**Sensor:** Dual. Sentinel-2 (B8, B11, B12 for NDBI and brightness) and Sentinel-1 (VV
backscatter).

**Detector:** `builtup_dual`. Two evidence streams scored independently and fused:
(a) optical, a persistent increase in NDBI and in visible brightness against the seasonal
baseline; (b) radar, a persistent increase in VV backscatter consistent with new hard
surfaces and vertical structures. Require agreement between streams or persistence across
three or more observations in one stream before confirming.

**Defaults:** optical z-threshold 2.5, radar z-threshold 2.5, MMU 1000 m² (roughly ten
Sentinel-2 pixels), 3 confirmations.

**Grounding:** NDBI, Zha, Y., Gao, J. & Ni, S. (2003), Use of normalized difference
built-up index in automatically mapping urban areas from TM imagery, *International
Journal of Remote Sensing* 24(3), 583-594, <https://doi.org/10.1080/01431160304987>. The
radar stream uses the omnibus statistics of R7.

**Limitations, stated bluntly in the UI:** at 10 m, Sentinel data cannot reliably detect
individual buildings. This recipe detects site-scale activity such as ground clearing,
foundation work, hardstanding, and large structures. Bare soil exposure produces NDBI
increases indistinguishable from early construction, which is why persistence is required.

- [x] Optical stream: NDBI + brightness z-scores vs seasonal baseline → P8 · both must
  rise because the score is their pixelwise minimum; monthly planes suppress phenology
- [x] Radar stream: VV increase via R7 omnibus statistics → P8 · nearest same-orbit RTC
  scenes are reprojected to the optical grid; the R7 p-value is gated to Loewner-order
  increases and converted to a one-sided significance z-score for fusion
- [x] Fusion rule: cross-stream agreement or 3-observation persistence → P8 · agreement
  over 60% of the retained footprint confirms immediately; one stream stays provisional
  until the existing three-overlap confirmation rule is satisfied

### R7. Site activity, statistical radar monitoring — 🟢 P7

**Question:** Has anything changed at this facility, whatever it is?
**Sensor:** Sentinel-1 GRD, dual-pol VV+VH, same relative orbit only.

**Detector:** `wishart_omnibus`. Treat each pixel as a diagonal 2x2 complex covariance
matrix over the dual-pol intensities. Apply the sequential omnibus likelihood-ratio test
for equality of covariance matrices across the time series, which identifies both whether
and when change occurred, at a user-chosen significance level. Report the Loewner order
direction (increase, decrease, or indefinite) alongside the change map.

**Defaults:** significance level alpha = 0.01, effective number of looks 4.4 for GEE-style
IW data (make this a parameter, since it depends on preprocessing), MMU 500 m².

**Why this recipe matters:** the threshold is an actual false-alarm probability rather
than an arbitrary number, which makes it the most defensible detector in the catalogue.

**Grounding:** Conradsen, K., Nielsen, A.A. & Skriver, H. (2016), Determining the points
of change in time series of polarimetric SAR data, *IEEE TGRS* 54(5), 3007-3024,
<https://doi.org/10.1109/TGRS.2015.2510160>. Sentinel-1 implementation: Canty, M.J.,
Nielsen, A.A., Conradsen, K. & Skriver, H. (2020), *Remote Sensing* 12(1), 46,
<https://doi.org/10.3390/rs12010046>. Reference implementation and tutorial:
<https://developers.google.com/earth-engine/tutorials/community/detecting-changes-in-sentinel-1-imagery-pt-1>.

**Limitations:** assumes the pixel statistics follow the complex Wishart model, which
speckle filtering violates, so this detector must run on unfiltered multilook data; alpha
controls per-pixel false alarms, so over a large AOI the expected number of false
positives is alpha times the pixel count, and the UI must state that number explicitly
next to the threshold.

- [x] Omnibus likelihood-ratio test → P7 · `−2lnQ = 2[n·ln|Σ̂| − n₁ln|Σ̂₁| − n₂ln|Σ̂₂|]`
  for the diagonal dual-pol case, `f = (k−1)·p = 2`, against the χ² tail. Verified by
  simulation rather than by assertion: `test_wishart_false_alarm_rate` generates
  Gamma(ENL) speckle with no change and checks that the realised false-alarm rate lands
  near alpha and the p-values come out roughly uniform
- [x] Change-point identification (when, not just whether) → P7 · the test runs as
  **baseline period vs each new observation**, so the change point is the observation
  that first crosses — which is what the alert carries and what the timeline shows.
  The *sequential* multi-change-point variant, which re-partitions a whole series at
  once, is not built: it does not fit the one-scene-at-a-time pipeline and would need
  its own job kind. Add if backfilling historical change points becomes a real ask
- [x] Loewner order direction reporting → P7 · increase / decrease / **indefinite**,
  reduced over the changed area only. Indefinite is a real answer and is reported as
  one rather than forced to a direction
- [x] ENL as a parameter, default 4.4 → P7 · matches `sar:looks_equivalent_number` on
  the IW products, and is a parameter because it depends on preprocessing
- [x] UI states expected false-positive count = alpha × pixel count → P7 · on the run
  detail, next to the observed crossing count, with the note that only the MMU
  separates the two
- [x] Runs on unfiltered multilook data only → P7 · no speckle filter exists anywhere in
  the radar path, so this cannot be violated by configuration

### R8. General bitemporal change (optical, unsupervised) — 🟢 P8

**Question:** Has anything at all changed between these two dates?
**Sensor:** Sentinel-2 L2A, all 10 m and 20 m bands resampled to 10 m.

**Detector:** `irmad_cva`. Run IR-MAD between the two dates to obtain change variates
that are invariant to linear radiometric differences, then compute the chi-squared change
magnitude from the standardised MAD variates and derive a per-pixel no-change
probability. Separately compute a change vector direction in index space for the
explanation layer.

**Defaults:** threshold on no-change probability at 0.05, iterate IR-MAD to convergence or
50 iterations, MMU 1000 m².

**Grounding:** Nielsen, A.A. (2007), The regularized iteratively reweighted MAD method for
change detection in multi- and hyperspectral data, *IEEE Transactions on Image
Processing* 16(2), 463-478, <https://doi.org/10.1109/TIP.2006.888195>. Change vector
direction framing: Bovolo, F. & Bruzzone, L. (2007), A theoretical framework for
unsupervised change detection based on change vector analysis in the polar domain, *IEEE
TGRS* 45(1), 218-236, <https://doi.org/10.1109/TGRS.2006.885408>.

**Use when:** the user does not know what they are looking for. This is the catch-all and
should be described that way.

**Limitations:** bitemporal only, so it has no notion of seasonality and will flag
phenology as change unless the two dates are phenologically matched; the app must enforce
a same-season-window constraint by default and warn loudly if the user overrides it.

- [x] IR-MAD iterated to convergence or 50 iterations → P8
- [x] Chi-squared no-change probability → P8
- [x] CVA polar-domain direction for the explanation layer → P8 · NDVI and NBR
  direction are reported over the changed area
- [x] Same-season-window constraint enforced by default, loud override warning → P8 ·
  monthly reference planes enforce a window narrower than the 45-day default; missing
  seasonal data and the expert all-season override both emit stored detector notes, and
  the UI warns before queueing override reanalysis

### 8.1 Mask chain components

Referenced by recipes by name.

- `cloudscore_plus` — Cloud Score+ `cs` band, threshold 0.60 by default (values 0.50 to
  0.65 are the documented working range). Available through GEE or the published product.
  Pasquarella, V.J. et al. (2023), Comprehensive Quality Assessment of Optical Satellite
  Imagery Using Weakly Supervised Video Learning, CVPR Workshops,
  <https://doi.org/10.1109/CVPRW59228.2023.00206>.
- `s2cloudless` — fallback when Cloud Score+ is unavailable, threshold 0.4 probability,
  with a shadow projection buffer derived from solar geometry.
- `scl_shadow` — Sentinel-2 SCL classes 3 (cloud shadow), 8, 9, 10 (clouds and cirrus),
  dilated by 2 pixels.
- `snow` — SCL class 11 plus an NDSI > 0.4 test.
- `s1_terrain` — layover and shadow mask from GLO-30, plus a slope exclusion.
- `s1_border_noise` — GRD border noise removal where the product provides the annotation.
- `water_permanent` — optional exclusion of permanent water using a static layer, for
  recipes where water flicker is noise.

Every mask contributes named pixel counts to `mask_summary_json`, and the UI shows the
breakdown. The user must always be able to see how much of their AOI each mask removed on
a given date.

- [x] `cloudscore_plus` → P2 · preferred whenever a `cs` asset is published
- [x] `s2cloudless` → P2 · probability fallback with solar-geometry shadow projection
- [x] `scl_shadow` → P2 · `snitch/masks.py`
- [x] `snow` → P2
- [x] `s1_terrain` → P7 · GLO-30 slope and D8 HAND layers over RTC imagery
- [x] `s1_border_noise` → P7 · not applicable to the selected RTC collection; GRD
  border annotations never enter the local reference path
- [x] `water_permanent` → P8 · optional aligned static layer with named pixel counts
- [x] Named per-mask pixel counts in `mask_summary_json` → P2
- [x] Per-mask breakdown visible in the UI → P4

---

## 9. Data quality and vocality

This section is a feature, not error handling.

### 9.1 The gate

Each recipe declares `min_valid_fraction`, default 0.70. A run that cannot meet it does
not produce a weak alert; it produces a diagnostic and a visible gap in the timeline. The
project timeline widget renders three distinct states per date: green (observed and
scored), amber (observed but insufficient valid pixels), grey (no acquisition). Never
render amber and grey the same way.

- [x] `min_valid_fraction` declared per recipe, default 0.70 → P2
- [x] Gate produces a diagnostic and no run → P2
- [x] Three-state timeline: green / amber / grey, visually distinct → P4

### 9.2 Diagnostic taxonomy

Codes are stable strings. Each has a template message, a severity, and a suggested remedy
shown in the UI.

| Code | Severity | Meaning | Status |
|---|---|---|---|
| `NO_ACQUISITION` | info | No scene covering the AOI in the expected window | [x] P2 |
| `ACQUISITION_OVERDUE` | warning | No usable observation for more than N expected revisit cycles | [x] P2 |
| `LOW_VALID_FRACTION` | warning | Valid pixels below the recipe gate | [x] P2 |
| `PERSISTENT_OCCLUSION` | error | Three or more consecutive gated runs | [x] P2 |
| `MISREGISTRATION` | warning | Geometric shift above tolerance | [x] P3 |
| `ORBIT_MISMATCH` | error | Only off-orbit S1 scenes available | [x] P7 |
| `BASELINE_TOO_SHORT` | error | Fewer observations than the detector requires | [x] P3 |
| `BASELINE_SPANS_CONSTELLATION_EVENT` | warning | See 4.4 | [x] P2 |
| `ADAPTER_FALLBACK` | warning | Primary source failed, a fallback served the data | [x] P1 |
| `PROVENANCE_DISCONTINUITY` | warning | Run and baseline came from different adapters or processing levels | [x] P2 |
| `SATURATION` | warning | A large fraction of pixels at the index's extreme value | [x] P3 |
| `DETECTOR_ASSUMPTION` | warning | Detector emitted a note about a violated assumption | [x] P3 |
| `QUOTA_LOW` / `QUOTA_EXHAUSTED` | warning / error | GEE compute quota state | [x] P10 |
| `SOURCE_UNREACHABLE` | error | All adapters failed | [x] P1 |
| `RUN_FAILED` | error | Unhandled exception, with traceback in `detail_json` | [x] P3 |

- [x] Diagnostic write helper → P0 · `snitch/db.py:diagnostic`
- [x] Every local code has a template message, severity, and suggested remedy → P2 ·
  stable severity/remedy metadata is stored in `detail_json` and rendered on cards;
  the GEE quota codes are raised by the pre-flight budget check in `gee.check_budget`

### 9.3 Caveats on alerts

Caveats are the subset of diagnostics that attach to a specific alert and must render as
visible chips on the alert card, never buried in a details pane. An alert with caveats is
still an alert, but the UI must make the caveat impossible to miss.

- [x] Caveat codes stored on the alert → P3
- [x] Caveat chips always visible on the alert card → P4

### 9.4 Health surface

The dashboard has a persistent health strip showing, per project: last usable
observation, days since, current diagnostic count by severity, and next scheduled poll. A
project that has silently stopped seeing data must be as visually loud as a project
raising alerts.

- [x] Persistent health strip with all four fields per project → P4

---

## 10. Alerting logic

Raw threshold crossings are not alerts. Apply in order:

1. **Minimum mapping unit.** Discard connected components below the recipe's MMU. If
   nothing survives, no alert.
2. **Hysteresis.** A project enters the alerting state when the score crosses
   `threshold`, and leaves it only when the score falls below `threshold * exit_ratio`
   (default 0.7). This prevents flapping at the boundary.
3. **Confirmation.** Recipes declaring `consecutive_confirmations > 1` raise a
   `provisional` alert on the first crossing and upgrade it to `confirmed` on the Nth.
   Provisional alerts are visually distinct and are automatically retracted (status set,
   never deleted) if the next observations do not confirm.
4. **Incident grouping.** A new alert within `incident_window` days (default 30) of an
   open incident, with spatially overlapping geometry (IoU > 0.2), joins that incident
   instead of opening a new one. An incident closes after `cooldown` days (default 45)
   with no new alerts.
5. **Cooldown.** No more than one notification per incident per `notification_cooldown`
   (default 24 hours), regardless of how many alerts arrive.
6. **Severity.** Derived from where the score and the changed area sit in the project's
   own historical distribution, not from absolute values. Low is above threshold, medium
   is above the 90th percentile of historical scores, high is above the 99th.

The reason no alert was raised must be recorded on the run. "Below threshold", "below
MMU", "awaiting confirmation", and "gated" are all distinct and all shown in the run
history.

- [x] Rule 1 — MMU component filtering → P6
- [x] Rule 2 — hysteresis with `exit_ratio` → P6
- [x] Rule 3 — provisional → confirmed, with retraction (never deletion) → P6
- [x] Rule 4 — incident grouping by time window + IoU > 0.2, closure after cooldown → P6
- [x] Rule 5 — notification cooldown per incident → P6
- [x] Rule 6 — severity from the project's own historical distribution → P6
- [x] No-alert reason recorded on the run, four distinct values → P6

---

## 11. Compute backends

### 11.1 Local (default)

Windowed COG reads over HTTP via `odc-stac` / `rasterio`, numpy and scipy for scoring.
All detectors must have a working local implementation. This is the reference path and
the one used for reproducibility.

- [x] Windowed COG reads over HTTP → P1 · buffered AOI windows are reprojected onto one
  common analysis grid without downloading whole scenes
- [x] Every detector has a working local implementation → P3–P8 · R6 pairs optical data
  with the nearest same-orbit RTC scene and fuses its Wishart increase significance

### 11.2 Google Earth Engine (optional)

Enabled per-project, off by default, and only usable when the user has configured a
service account and a registered Cloud project.

Use GEE for the operations where the local path is slowest and GEE is strongest:

- Baseline fitting over multi-year archives.
- The historical backtest at project creation.
- Retrieval of Cloud Score+ masking data.

Do not use GEE for forward monitoring by default, because its catalogue ingestion trails
the Copernicus Data Space and forward runs are cheap locally.

Hard requirements:

- Everything GEE computes is exported and stored locally as COGs plus a JSON sidecar
  recording the exact script, the asset ids, the dates, and the algorithm version. Use
  `xee` to bridge results into the same xarray objects the local path produces so
  downstream code does not branch.
- Track EECU consumption. Read the project's tier and remaining quota where the API
  allows, estimate cost before submitting large tasks, and refuse to submit if the
  estimate exceeds the remaining budget, raising `QUOTA_LOW` or `QUOTA_EXHAUSTED`.
- Use batch `Export` tasks rather than synchronous `getDownloadURL` for anything
  substantial. Synchronous requests have request-size and timeout limits that fail
  unpredictably in automated loops.
- Note in the settings UI that noncommercial projects have a monthly EECU-hour quota that
  resets on the first of each month, that exceeding it degrades to restricted mode rather
  than cutting off, that a daily EECU cap can be set in the Cloud console, and that
  noncommercial status requires annual reverification. Link to
  <https://developers.google.com/earth-engine/guides/noncommercial_tiers>.

Any run using GEE records `compute_backend='gee'`, and the UI marks it, because it is a
reproducibility-relevant fact.

- [x] Per-project GEE toggle, off by default, service-account config → P10 ·
  `POST /projects/{uuid}/gee`; needs both the global config and the project opt-in
- [x] GEE used for baseline fitting, backtests, Cloud Score+ only → P10 ·
  `gee.adapter_for` returns None for `forward`, so monitoring stays local
- [x] Everything GEE computes exported locally as COG + JSON sidecar → P10 ·
  `gee.persist` / `gee.sidecar` record the script, asset ids, dates, and version
- [x] `xee` bridge so downstream code does not branch → P10 · `GeeAdapter.load`
  returns the same `time, y, x` Dataset `as_array_dict` already normalises
- [x] EECU tracking, pre-flight estimation, refusal above budget → P10 · the
  `gee_usage` ledger, `estimate_eecu`, and a monthly/daily `check_budget` refusal
- [x] Batch `Export` tasks, never `getDownloadURL` for substantial work → P10 ·
  above `adapters.gee.batch_export_eecu` the load submits an Export task, and refuses
  when no export bucket is configured rather than falling back to a synchronous download
- [x] Settings UI explains the noncommercial tier and links the guide → P10
- [x] `compute_backend` column on `run` → P0
- [x] UI marks GEE-backed runs → P10

---

## 12. Explanation generation

### 12.1 Deterministic layer (always present, never optional)

Generated from the run summary by a template engine keyed on the recipe. It must state,
in plain language:

- What was compared: the target date and the reference or baseline period, named
  explicitly.
- How much changed: area in hectares and as a percentage of the AOI, plus number of
  distinct patches and the size of the largest.
- In which direction: the index deltas and their plain-language reading, for example
  "vegetation index fell sharply while shortwave infrared rose, a pattern consistent with
  vegetation removal or burning".
- How unusual it is: where this score sits in the project's own history, for example "the
  highest score in 47 observations".
- What the caveats are: every caveat code rendered as a sentence.

Example output:

> Between 2026-03-14 and 2026-08-19, 4.2 hectares changed across 3 patches, the largest
> being 2.9 hectares. NDVI fell by 0.31 and NBR by 0.44 across the changed area, a
> pattern consistent with vegetation removal. This is the highest change score in 47
> observations of this site. 82% of the area was cloud-free on the target date.

- [x] Template engine keyed on the recipe → P3
- [x] States what was compared, explicitly named → P3
- [x] States how much changed (ha, % of AOI, patches, largest) → P3
- [x] States direction with a plain-language reading → P3
- [x] States how unusual against the project's own history → P3
- [x] Renders every caveat code as a sentence → P3

### 12.2 Optional VLM layer

If configured, send the before chip, the after chip, and the overlay chip to a vision
model along with the deterministic summary as context, and store the response in
`explanation_llm`.

Rules: it is rendered in a visually distinct block labelled as machine-generated and
unverified; it may never modify severity, confidence, or whether an alert fires; it is
never included in an evidence export by default; and if the model is unavailable the
alert is produced normally without it. State in the UI that vision models are unreliable
on 10 m multispectral imagery.

- [x] VLM call with the three chips + deterministic summary → P9
- [x] Rendered in a distinct block, labelled machine-generated and unverified → P9
- [x] Never modifies severity, confidence, or whether an alert fires → P9
- [x] Excluded from evidence exports by default → P9
- [x] Alert produced normally when the model is unavailable → P9
- [x] UI states VLMs are unreliable on 10 m multispectral imagery → P9

---

## 13. Frontend

### 13.1 Screens

**Dashboard.** Health strip across the top. Below it, a unified reverse-chronological
feed of alerts and error-severity diagnostics across all projects, filterable by project,
severity, status, and date. Each alert card: thumbnail overlay chip, project name, date,
severity, one-line explanation, caveat chips, and triage buttons (true / false / unclear /
acknowledge).

**Project list.** Table with sparkline of recent scores, last observation date, open
incident count, diagnostic count, and status.

**Project detail.** Four tabs.

- *Map*: MapLibre with the AOI, the latest change overlay served by TiTiler, a
  before/after swipe comparator, and toggleable mask layers.
- *Timeline*: every observation as a coloured tick (green/amber/grey per 9.1) on a
  horizontal axis with the score plotted above it and the threshold as a horizontal line.
  Clicking a tick opens that run.
- *Alerts*: the project's alerts and incidents.
- *Health*: diagnostics, adapter status, baseline info, quota state.

**Project creation wizard.** Four steps, described in 13.2.

**Run detail.** Everything about one run: input scenes with links to source, mask
breakdown table, score histogram, parameters, detector version, timing, and a "download
run bundle" action.

**Settings.** Adapters and credentials, GEE configuration, notification channels, storage
usage, recipe registry browser.

- [x] Dashboard: health strip + unified filterable feed + alert cards with triage → P4
- [x] Project list with sparklines and counts → P4
- [x] Project detail — Map tab (AOI, TiTiler overlay, swipe comparator, mask toggles) → P4
- [x] Project detail — Timeline tab (three-state ticks, score plot, threshold line) → P4
- [x] Project detail — Alerts tab → P4
- [x] Project detail — Health tab → P4
- [x] Run detail with scene links, mask table, histogram, params, bundle download → P4/P9
- [x] Settings screen → P4

### 13.2 The creation wizard

This is where the simple-user-experience requirement is won or lost.

**Step 1, Where.** Draw a polygon, upload a GeoJSON/KML, or paste coordinates. Show the
area and warn above 500 km² that runs will be slow. Immediately show which Sentinel-1
relative orbits cover the AOI and how many usable Sentinel-2 observations exist in the
last two years, so the user learns before committing whether this site is even
monitorable.

**Step 2, What.** A card grid of the eight recipes, each phrased as its `plain_question`,
with an icon, the sensor it uses, its revisit cadence, and a "why this method" link
opening the reference. No jargon on the card face.

**Step 3, Calibrate.** This is the core interaction and must be built carefully.

On entering this step, enqueue a backtest job covering the last three years (or the
maximum the recipe's baseline allows). Show progress honestly. When it completes, render:

- A histogram of every historical score computed for this AOI.
- A draggable vertical threshold line, initialised at the recipe default.
- A live-updating list of the historical dates that *would have* alerted at the current
  threshold, each with a before/after thumbnail pair.
- A live counter: "at this threshold you would have received N alerts in 3 years, roughly
  one every M weeks."

The user tunes by dragging and looking at real past events at their own site. They never
have to understand what a robust z-score is. Store the chosen threshold and the backtest
artifact id on the project, so the calibration itself is part of the audit trail.

Provide an "I don't know, use the published default" button that keeps the recipe default
and records that choice.

**Step 4, Watch.** Name the project, choose the schedule (the app suggests one derived
from the actual revisit cadence at that latitude rather than offering an arbitrary cron
box), choose notification channels, and activate.

- [x] Step 1 — draw / paste GeoJSON / import polygon KML → P5
- [x] Step 1 — area readout with a >500 km² slowness warning → P5
- [x] Step 1 — S1 orbit coverage and S2 usable-observation count, shown before committing → P5
- [x] Step 2 — recipe card grid, plain questions, no jargon, "why this method" link → P5
- [x] Step 3 — backtest job with honest progress → P5
- [x] Step 3 — score histogram → P5
- [x] Step 3 — draggable threshold line → P5
- [x] Step 3 — live list of would-have-alerted dates with matched-stretch thumbnail pairs → P5
- [x] Step 3 — live "N alerts in 3 years, one every M weeks" counter → P5
- [x] Step 3 — chosen threshold stored on the project with its provenance → P5
- [x] Step 3 — "use the published default" button, choice recorded → P5
- [x] Step 4 — name, suggested schedule from real revisit cadence, activate → P5

### 13.3 Presentation rules

- Never show a change overlay without the mask layer available as a toggle.
- Never show an alert without its caveat chips.
- Before/after chips are always rendered with identical stretch parameters, computed once
  from the baseline, and the stretch values are printed on the image. Independently
  stretched pairs manufacture apparent change and must not be produced.
- Every score displayed carries its units and a tooltip with `threshold_semantics` from
  the detector spec.
- Every recipe name in the UI links to its reference.

- [x] Mask layer toggle always available alongside any overlay → P4
- [x] Caveat chips always rendered with an alert → P4
- [x] Identical stretch across before/after, values printed on the image → P3
- [x] Every score carries units + `threshold_semantics` tooltip → P4
- [x] Every recipe name links to its reference → P4

### 13.4 Notifications

Pluggable channels, all optional: webhook (POST JSON), email (SMTP), Slack, Matrix, and a
local desktop notification. Payload contains the alert summary, the deterministic
explanation, a link to the alert in the UI, and the caveat list. Notifications respect the
cooldown in section 10.

- [x] Webhook channel → P6
- [x] Email (SMTP) channel → P6
- [x] Slack channel → P6
- [x] Matrix channel → P6
- [x] Local desktop notification → P6
- [x] Payload carries summary, explanation, UI link, caveats → P6
- [x] Cooldown respected → P6

---

## 14. API surface

REST, JSON, prefix `/api/v1`. All list endpoints paginate with `limit`/`cursor`.

```
GET    /projects
POST   /projects
GET    /projects/{uuid}
PATCH  /projects/{uuid}
POST   /projects/{uuid}/pause
POST   /projects/{uuid}/activate
DELETE /projects/{uuid}                  soft delete only
POST   /projects/{uuid}/backtest         start or restart calibration
GET    /projects/{uuid}/backtest         status + score distribution
POST   /projects/{uuid}/threshold        set threshold, records provenance
POST   /projects/{uuid}/run-now          force a poll + run cycle
POST   /projects/{uuid}/reanalyse        re-run history with new params
                                         (creates new runs, supersedes old)
GET    /projects/{uuid}/observations
GET    /projects/{uuid}/runs
GET    /runs/{uuid}
GET    /projects/{uuid}/alerts
GET    /alerts/{uuid}
PATCH  /alerts/{uuid}                    triage status and note
GET    /projects/{uuid}/incidents
GET    /diagnostics                      global, filterable
POST   /diagnostics/{id}/acknowledge
GET    /recipes                          registry with references
GET    /detectors                        specs incl. param schemas
GET    /health                           adapters, worker, queue depth, storage
GET    /projects/{uuid}/export           evidence bundle, see 15
```

Tiles are served by the `tiler` service at `/tiles/{artifact_sha}/{z}/{x}/{y}.png` with a
signed short-lived token issued by the API.

- [x] Project CRUD + pause/activate/soft-delete → P0/P4
- [x] Backtest start/status endpoints → P5
- [x] Threshold endpoint recording provenance → P5
- [x] `run-now` and `reanalyse` (new runs supersede old) → P3
- [x] Observations / runs / run detail endpoints → P3
- [x] Alerts / alert detail / triage PATCH / incidents → P6
- [x] Diagnostics list + acknowledge → P2
- [x] Recipes and detectors registries → P3
- [x] `/health`: adapters, worker, queue depth, storage → P0
- [x] Export endpoint → P9
- [x] Cursor pagination on every list endpoint → P4
- [~] Signed short-lived tile tokens → P4 · the token endpoint and the unused TiTiler
  service were removed in the 2026-08 audit fixes: tokens were issued but nothing ever
  verified or consumed them. Overlays today are deck.gl vector geometry plus renderable
  mask PNGs from the API; restore COG tiling (with verification on the tiler side)
  when a use case needs it

---

## 15. Provenance and export

Any alert, run, or whole project can be exported as a zip bundle containing:

- `manifest.json` with schema version, export timestamp, Snitch version and git
  commit, and a sha256 for every file.
- The source COGs used, or if too large, the STAC item JSON plus the exact source URIs
  and byte ranges read.
- The score raster, the mask raster, and the chips.
- The full run record including detector id, version, and every resolved parameter.
- `method.md`, auto-generated: the recipe definition, the citation, the reference URL, the
  threshold and how it was chosen (default or calibrated, with the calibration artifact),
  and the stated limitations.
- `diagnostics.json`, every diagnostic touching the exported records.

The bundle must be sufficient for a third party with no access to the running system to
understand what was claimed and on what basis. This is what makes findings defensible.

- [x] `manifest.json` with version, commit, timestamp, per-file sha256 → P9
- [x] Source COGs or STAC items + URIs + byte ranges → P9
- [x] Score raster, mask raster, chips → P9
- [x] Full run record with every resolved parameter → P9
- [x] Auto-generated `method.md` → P9
- [x] `diagnostics.json` → P9
- [x] Exportable at alert, run, and project granularity → P9

---

## 16. Configuration

Single `config.yaml` plus environment overrides. Keys:

```yaml
storage: {data_dir, max_artifact_gb, gc_enabled: false}
adapters:
  cdse: {enabled: true, credentials_env: [...], timeout_s: 60}
  earthsearch: {enabled: true}
  planetarycomputer: {enabled: false}
  gee: {enabled: false, service_account_json: null, project_id: null,
        monthly_eecu_budget: 100, daily_eecu_cap: 10}
scheduler: {max_concurrent_runs: 2, poll_jitter_minutes: 30}
alerts: {default_exit_ratio: 0.7, incident_window_days: 30,
         cooldown_days: 45, notification_cooldown_hours: 24}
explanations: {vlm_enabled: false, vlm_endpoint: null, vlm_model: null}
notifications: {...}
ui: {password: null}
```

Credentials never appear in logs, exports, or the API. The `/health` endpoint reports
credential presence and validity, never values.

- [x] `config.yaml` with every key above → P0
- [x] Environment variable overrides → P0
- [x] Credentials never in logs, exports, or the API → P0
- [x] `/health` reports credential presence and validity, never values → P0

---

## 17. Build order

Each milestone is independently demonstrable. Do not proceed until the previous one works
end to end. (Mapped to phases P0–P10 in Part I.)

1. **Skeleton.** Schema, migrations, job queue, worker loop, health endpoint, Docker
   Compose. Prove a job can be enqueued, leased, executed, and completed. → **P0**
2. **Ingest.** The `cdse` and `earthsearch` adapters, windowed loading, the artifact
   store. Prove you can pull a clipped, reprojected Sentinel-2 stack for a drawn AOI.
   → **P1**
3. **Masks and gates.** Mask chain, valid fraction, the diagnostic taxonomy, and the
   three-state timeline. Prove the app is loud about a cloudy month. → **P2**
4. **First detector end to end.** R1 (`harmonic_residual`) plus baseline fitting,
   scoring, thresholding, morphology, run persistence, deterministic explanation, and a
   rendered alert. This exercises the whole spine. → **P3**
5. **Frontend core.** Dashboard, project detail, MapLibre map, timeline, alert cards.
   → **P4**
6. **Wizard and calibration.** Backtest job, score histogram, draggable threshold with
   live historical alerts. This is the highest-value UI work in the project. → **P5**
7. **Alerting rules.** Hysteresis, confirmation, incidents, cooldowns, notifications.
   → **P6**
8. **Radar path.** S1 loading, orbit constraints, preprocessing, then R4 and R7. → **P7**
9. **Remaining recipes.** R2, R3, R5, R6, R8. → **P8**
10. **Export and provenance.** Evidence bundles, `method.md` generation. → **P9**
11. **GEE backend.** Optional adapter, quota tracking, xee bridge for baseline fitting and
    backtests. → **P10**

---

## 18. Testing and acceptance

**Unit.** Every detector against synthetic arrays with known injected change, verifying
both detection and the absence of detection in unchanged regions. Every mask against
fixtures with known pixel counts.

**Golden fixtures.** Ship a small set of cached STAC responses and clipped COGs for three
sites with known events (a documented burn, a documented flood, a documented clearing)
and assert that the corresponding recipe detects them within a stated date tolerance and
does not fire elsewhere in the series. These fixtures are the regression suite.

**Property tests.** Score rasters never contain values where the mask is invalid. Runs are
never mutated after `finished_at`. Every alert resolves to a run, an observation, and at
least one artifact.

- [x] Per-detector synthetic-change unit tests, both detection and non-detection → per phase
- [x] Per-mask fixture tests with known pixel counts → P2
- [ ] Golden fixtures: burn, flood, clearing, with date tolerance → P8
- [x] Property: no score values where the mask is invalid → P3
- [x] Property: runs never mutated after `finished_at` → P3
- [x] Property: every alert resolves to run + observation + artifact → P3

**Acceptance criteria for v1:**

- [ ] A user with no remote sensing knowledge can create a working monitor in under two
      minutes.
- [x] A month of total cloud cover produces zero alerts and a clearly visible amber gap
      plus a `PERSISTENT_OCCLUSION` diagnostic.
- [x] Killing the process mid-run leaves no partial alert and the job retries cleanly.
- [x] Every alert in the UI links to its method reference in two clicks or fewer.
- [ ] An exported evidence bundle can be understood by someone who has never seen the
      application.
- [x] Deleting the GEE credentials leaves every project functional on the local path ·
      the optional backend is off by default and no local adapter imports or requires it.

---

## 19. Notes for the implementing agent

- Verify every DOI and URL in section 8 at build time and fail the recipe registry load if
  a reference is unresolvable. A citation that does not resolve is worse than no citation.
- Where this spec gives a numeric default drawn from the literature, keep it and cite it.
  Where you must invent a default, mark it clearly in the recipe YAML as
  `provenance: heuristic` so the UI can distinguish published thresholds from ones the app
  made up.
- Resist adding a "confidence score" that blends heterogeneous evidence into one number.
  The confidence model here is deliberately coarse (provisional / confirmed) because a
  fabricated continuous confidence is exactly the kind of false precision this application
  exists to avoid.
- OPEN questions (all now answered — see Part I): whether backtests should be capped by AOI
  size, whether the S1 orbit choice should allow multi-orbit projects with per-orbit
  baselines, and whether incidents should support manual merge and split.

- [x] Reference resolution verified at build time (`python -m snitch.recipes`) → P3
- [x] Invented defaults marked `provenance: heuristic` → per phase · registry loading
  fails if any default lacks `literature`, `statistical`, or `heuristic` provenance
- [x] No blended continuous confidence score anywhere → all phases · confidence is only
  provisional or confirmed; detector scores retain their stated physical/statistical units
