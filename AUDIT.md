# TerraWatch — Full Application Audit

**Date:** 2026-08-27 · **Scope:** entire repository compared against `BUILD.md` (spec v1.0),
`README.md`, `recipes/*.yaml`, and the code in `terrawatch/`, `frontend/src/`, `tests/`,
and the Docker/deployment files.

**Status:** C1–C3, H1, H2 (scaffolding removed), H4, the §5 doc drift, and §7 priority
item 6 were fixed in the commits immediately after this audit (including the trivial
hardening items: severity headline, overdue-from-activation, feed cursor/date filters,
Timeline polarity, GEE before-frames, constant-time password compare). Still open:
H3 golden fixtures, §3.12 cdse `ADAPTER_FALLBACK` coalescing, §3.13 performance hot
spots, and §4's notes on failed-run records for stages 3–5 and the Wishart NaN convention.

**Method:** every source file was read in full (≈8,800 lines). Suspected defects were
verified by executing code in the project's own `.venv` (results quoted below). The test
suite was run: **91 passed, 4 skipped, in 5 s** (`BUILD.md`'s dashboard still says
"80 tests passing", a stale count).

---

## Executive summary

The app is substantially what `BUILD.md` says it is, and large parts of it are genuinely
good: the detector catalogue (all 8 recipes), the diagnostic taxonomy, the job queue,
the explanation/excerpt provenance chain, the calibration wizard, the three-state
timeline, the GEE opt-in backend, and the evidence bundles all exist and behave close to
spec. The honest-before-helpful principle is visibly implemented (no-alert reasons,
caveat chips, loud mask gaps, retraction instead of deletion).

However, the audit found **two critical bugs that break core flows on the real data
path**, **one spec requirement that is dead code despite being checked off**, and a
cluster of doc-vs-code drifts where `BUILD.md` or a recipe YAML claims something the
code does not do. The critical items were invisible to the test suite because the
end-to-end test monkeypatches out exactly the code that breaks.

---

## 1. Critical — breaks core functionality

### C1. The flagship recipe (`vegetation_loss_optical`) can never raise an alert on real data — chip rendering crashes

`chips.display_bands()` (`terrawatch/chips.py:13-23`) returns the intersection of the
recipe's bands with the RGB triple `(B04, B03, B02)`. For `vegetation_loss_optical`
(bands `B03, B04, B08, B11, B12, SCL` — no `B02`) it returns **two** bands:

```
vegetation_loss_optical -> ('B04', 'B03')        # every other recipe -> 3 bands
vegetation_loss chips render FAILED: ValueError operands could not be broadcast
together with shapes (100,2) (3,)
```

(the overlay tint `ov[m] = 0.45*ov[m] + 0.55*[255,60,60]` cannot broadcast over 2
channels; `Image.fromarray` would also reject an (h, w, 2) frame).

`chips.render` is called from `_render_chips` (`terrawatch/pipeline.py:793-824`), which
is on the mandatory path of:

- **stage 11** for every forward alert — the exception propagates *before* the alert
  INSERT (`pipeline.py:759` precedes `pipeline.py:771`), so **no alert is ever created**;
- the **backtest calibration** for any historical date with `changed_area > 0`
  (`pipeline.py:711-714`) — the wizard's step-3 calibration crashes for this recipe as
  soon as history contains a crossing.

The recipe that `README.md` and the whole spec are built around is effectively dead on
the real path. **Why the tests missed it:** `tests/test_pipeline.py:91-93` monkeypatches
`pipeline._render_chips` to a stub, so no test ever renders a chip.

**Fix:** in `display_bands`, only take the RGB path when all three RGB bands are present
(e.g. fall back to `bands[:3]` padded like the radar branch already does), and add a test
that renders chips for every recipe.

### C2. Duplicate observation writes get a **stale** row id — corrupts run↔observation links

`_write_observation` (`terrawatch/pipeline.py:149-163`) relies on
`INSERT OR IGNORE … .lastrowid or con.execute(SELECT …)`. But sqlite3's `lastrowid`
after an ignored insert is **not** `None` — it is the previous successful insert's id on
that connection, which is truthy, so the `SELECT` fallback never runs (verified on this
machine's Python 3.12: first insert → `1`, duplicate insert → `1`, not `None`).

Consequences whenever a scene's observation already exists:

- **`reanalyse`**: every re-run of history is for an already-recorded scene, so each new
  run's `target_observation_id` points at whatever observation that (long-lived) worker
  connection last really inserted — an unrelated scene, possibly another project's.
- **Multi-methodology projects**: the second methodology scoring the same scene links to
  the wrong observation.
- Downstream corruption: RADD's `_previous_score` joins `run→observation→sensed_at`
  (`pipeline.py:300-313`), chip before-frame selection, the timeline, and exports all
  read through this link.

The forward path (fresh scenes) is unaffected, and the property test only checks that
the referenced observation *exists*, not that it is the right one.

**Fix:** check `cur.rowcount` / fetch the id explicitly when `lastrowid` may be stale,
or `SELECT id` first, and add a regression test for `reanalyse` asserting the observation
link matches the scene id.

### C3. Provenance discontinuity can never fire (spec §4.2 checked off, but dead)

`run_scene` appends the `PROVENANCE_DISCONTINUITY` caveat when
`base["meta"].get("adapter")` differs from the serving adapter (`pipeline.py:530-532`).
But `baseline.save` (`terrawatch/baseline.py:9-25`) **never writes an `adapter` key into
the baseline metadata** — so the check is always `None` and the caveat can never be
raised. Spec §4.2: "If a run is served by a different adapter than the baseline was, it
must be flagged" — and `BUILD.md` marks this `[x] → P2`. The caveat sentence and UI chip
exist (`explain.py`, `Chips.tsx`) but are unreachable.

**Fix:** record `adapter` (and processing level) in `baseline.save`'s meta; optionally
write a diagnostic when it changes rather than only chipping alerts.

---

## 2. High — doc claims vs reality

### H1. The Docker "reference gate" does not gate

`README.md`: "Verify every recipe citation resolves (**this gates the Docker build**)";
spec §19: "fail the recipe registry load if a reference is unresolvable". The Dockerfile
(`Dockerfile:22-23`) runs:

```dockerfile
RUN if [ "$VERIFY_REFERENCES" = "1" ]; then python -m terrawatch.recipes || \
    echo "WARNING: reference check skipped (no network at build time)"; fi
```

The `|| echo` swallows the failure unconditionally — a dead DOI can never fail the
build. The check is real (`recipes.verify_references` with DOI-redirect handling), the
gate is not.

### H2. TiTiler / tile tokens are scaffolded but not integrated

Spec §14: "Tiles are served by the `tiler` service at
`/tiles/{artifact_sha}/{z}/{x}/{y}.png` with a signed short-lived token". Reality:

- The API **issues** HMAC tile tokens (`api.py:693-704`) — but nothing verifies or
  consumes them. `api.ts` exports `tileToken()` (dead code); the only other trace is a
  dev-only `/tiles → :8001` proxy in `vite.config.ts`.
- The `tiler` service runs in compose with a read-only artifacts mount, but no
  configuration maps the artifact store into TiTiler, and no route serves
  `/tiles/{sha}/...`.
- The frontend map renders the change footprint as a **vector GeoJSON layer**
  (`MapView.tsx:66-71`) and masks via a server-rendered amber PNG
  (`/artifacts/{id}/overlay.png`) — it never requests COG tiles.

`BUILD.md`'s `[x] Signed short-lived tile tokens → P4` is only true for issuance.
Either wire TiTiler end-to-end (including token verification) or shorten the claim.

### H3. Golden fixtures still missing (honestly marked, but P8 stays open)

`BUILD.md` §18: `- [ ] Golden fixtures: burn, flood, clearing` — the only explicitly
unchecked build item, and the reason P8 is still 🟡. All eight detectors have synthetic
detection/non-detection tests (`test_p8_detectors.py`, `test_radar.py`), which is good,
but the regression suite against real cached events does not exist yet. The dashboard
text is accurate here; this item simply remains open.

### H4. Calibration artifact id is never recorded with the threshold

Spec §13.2: "Store the chosen threshold **and the backtest artifact id** on the project,
so the calibration itself is part of the audit trail." The API accepts
`backtest_artifact_id` (`ThresholdIn`), and `method.md` prints it — but the wizard's
`activate()` (`Wizard.tsx:127-139`) sends only `{threshold, source}`, so exports always
say "Calibration artifact: none". The audit trail link the spec asks for is broken at the
one place a user actually calibrates.

---

## 3. Medium — functional deviations and correctness gaps

1. **Mask parameters come from the project row, not the methodology**
   (`pipeline.py:193`, `_load_masked` uses `project["params_json"]` while score params
   come from the methodology). Two methodologies on one project cannot tune mask params
   (e.g. `max_slope_deg`) independently, and a second methodology's defaults are silently
   ignored for masking.

2. **Multi-methodology poll race**: `_admit`'s already-seen set is project-wide
   (`pipeline.py:124-125`). If methodology A's run job executes between A's and B's
   passes of the *same* poll job, B's `seen` already contains the scene and B never
   scores it. Low probability, permanent loss.

3. **Severity rule deviates from spec §10 rule 6**: `alerting.severity` compares the
   alert's peak score to percentiles of the historical **`score_p99`** series (a
   percentile of tail statistics), and the changed **area is not used at all** despite
   the spec saying severity comes from "where the score *and the changed area* sit in the
   project's own historical distribution".

4. **Timeline and Sparkline ignore score polarity** (`Timeline.tsx:85`, `Sparkline.tsx`):
   dots/lines colour by `score >= threshold`, which is inverted for the two
   lower-is-more-change detectors (`wishart_omnibus`, `irmad_cva`). `Calibrate` and
   `RunDetail` handle polarity correctly; the timeline for radar site-activity projects
   highlights exactly the wrong ticks.

5. **`ACQUISITION_OVERDUE` never fires for a site that never saw data**:
   `_check_overdue` (`pipeline.py:166-169`) returns when there is no usable observation
   yet — precisely the "silently stopped seeing data" case §9.4 cares about is only half
   covered (the health strip does show "No usable observation yet").

6. **Settings GC button always fails with the default config**:
   `POST /maintenance/gc?dry_run=false` returns 400 unless `storage.gc_enabled: true`
   (`api.py:707-712`); the Settings page offers "Delete unreferenced artifacts" with no
   hint (`Settings.tsx:102-105`).

7. **Backtest status mixes methodologies**: `GET /projects/{uuid}/backtest` returns runs
   of *all* methodologies with no filter (`api.py:425-439`), so the wizard's calibration
   histogram blends different detectors' score scales on multi-methodology projects.

8. **`MISREGISTRATION` caveat is unreachable**: the code appends it and immediately
   raises (`pipeline.py:559-561`), so the run fails instead of alerting; with
   `auto_coregister` on, the caveat is never appended. The alert-card chip can never
   render. (The diagnostic itself is fine.)

9. **Data-directory resolution is split-brained**: `db.py:7` resolves `./data` against
   the **process CWD at import time**, while `config.data_dir()` (used by logs and the
   artifact store, `log.py:25`, `artifacts.py:10-11`) resolves against the repo root.
   With `TW_DATA_DIR` unset and a non-root CWD, the database lands in one tree and the
   artifacts/logs in another.

10. **Export gaps vs spec §15**: `sources.json` records STAC items and URIs but **no
    byte ranges** (the spec's fallback explicitly includes them, and nothing records
    ranges anywhere); `MAX_SOURCE_COG_BYTES` is defined but never used
    (`export.py:10`); `recipe.yaml` in the bundle contains JSON; `git_commit` is always
    null inside Docker (git binary installed but `.git` is not copied, `export.py:37-45`).

11. **GEE-backed chips silently fall back to before == after**:
    `_render_chips` builds `StacAdapter(adapter.name)` (`pipeline.py:804`), which raises
    `KeyError` for the `gee` adapter; the `except` logs and falls back to rendering the
    *target frame as the before image* — an identical before/after pair, labelled with
    different dates. For an app whose presentation rules say independent stretches
    "manufacture apparent change", an identical-frame pair is the same sin in inverse.

12. **`ADAPTER_FALLBACK` warning on every poll with default config**: the `cdse`
    adapter's `health()` is hardcoded to `ok: False` (`adapters.py:214-219`, honestly
    documented in `BUILD.md` as `[~]`), while `config.yaml` still enables it and project
    creation defaults to `["cdse", "earthsearch"]`. Every poll/baseline/backtest writes a
    `ADAPTER_FALLBACK` diagnostic for a fallback that is really the permanent primary.
    Diagnostics also have no dedup/coalescing, so the project-health warning count grows
    without bound.

13. **Performance hot spots in pure Python**: `dem.hand_from_elevation` does a 9-neighbour
    loop over every pixel plus a full Python argsort walk (`dem.py:94-120`), and
    `HarmonicResidual.fit_baseline` runs one `np.linalg.lstsq` per pixel
    (`detectors.py:91-96`). Both are called per run / per baseline scene; they are fine
    for test-size (40×40) grids and small AOIs, but a 500 km² AOI (5M pixels) is hours —
    the wizard's "slow, not wrong" soft cap is honest, but these two loops are where the
    hours live.

---

## 4. Minor issues, rough edges

- `GET /feed` accepts a `cursor` parameter and ignores it (`api.py:536-559`), and the
  dashboard has no date filter although §13.1 lists date among the feed filters.
- `DiagnosticCard` always styles the severity chip as `sev-high`
  (`AlertCard.tsx:57`).
- `HealthStrip` hardcodes "stale" at >20 days (`HealthStrip.tsx:12`) instead of deriving
  it from the recipe revisit.
- `retract_unconfirmed` marks retracted alerts as `user_status='unclear'`
  (`alerting.py:119-123`), conflating a machine retraction with a human triage decision
  (mitigated by the appended note).
- The password check is a plain `!=` comparison (`api.py:46`) — not constant-time; low
  stakes for an optional shared password, but trivial to fix with `hmac.compare_digest`.
- `docker-compose.yml`'s `api` service has no healthcheck and the `worker` does not wait
  for it meaningfully (`depends_on: [api]` is start-order only).
- A `backtest` job that fails after retries leaves the project stuck in
  `status='calibrating'` (the `UPDATE … status='draft'` only runs on success,
  `pipeline.py:859-861`); the RUN_FAILED diagnostic is the only trace.
- `WishartOmnibus.score` fills invalid pixels with p=1.0 instead of NaN
  (`detectors.py:837`), unlike every other detector — conservative, but the DetectorResult
  contract in §7.1 says "NaN where invalid" (the separate `valid` array is correct).
- `run_scene` skips its failed-run record when the exception happens in load/mask/gate
  (stages 3–5 run before the `try` at `pipeline.py:534`); those failures surface only as
  job retries and eventually a `RUN_FAILED` diagnostic from `worker.py:61-64`, not as a
  failed run row. Defensible, but §6's "stage failure aborts the run with
  status='failed'" is only literally true for stages 6–11.
- Repo hygiene is clean: `data/`, `.venv/`, `dist/` are properly ignored; nothing stray
  is tracked.

---

## 5. Documentation drift (docs need to move, code is reasonable)

| Where | Says | Reality |
|---|---|---|
| `BUILD.md` dashboard | "80 tests passing" | 91 passed + 4 skipped |
| `BUILD.md` repository map | lists 16 modules | omits `gee.py`, `baseline.py`, `config.py`, `geo.py`, `log.py`, `vlm.py`, `constellation.py` |
| `BUILD.md` R8 checkbox | "Same-season-window constraint enforced by default … `[x]`" | `recipes/general_change_optical.yaml` limitation still says "not yet enforced by scene admission" (the monthly-plane matching *is* implemented, so the YAML is the stale side) |
| `BUILD.md` R2 checkbox | Bayesian posterior carried through immutable score artifacts `[x]` | implemented (`_previous_score`), but `recipes/forest_disturbance_radar.yaml` still warns "persistent Bayesian state is not yet stored per pixel" — stale YAML |
| `BUILD.md` §4.2/§16 | adapter named `planetarycomputer` | config key and code call it `planetary` |
| `README.md` | "tiles at <http://localhost:8001>" | tiler runs but is not usable for artifact COGs (see H2) |
| `BUILD.md` §5.2 | "the full DDL … reproduced without change" | schema adds `project_methodology` and `gee_usage` tables beyond the brief's 8 (a reasonable, documented-in-comment extension, but not "verbatim") |
| §13.2 step 4 | wizard step 4 includes choosing notification channels | channels are Settings-level; the wizard says so in a footnote |

---

## 6. What the audit verified as genuinely solid

Worth stating so the problems above are read in proportion:

- **All 8 recipes end-to-end**: registry validation (required keys, provenance-tagged
  defaults, resolvable-reference check), detectors registered, synthetic
  detection *and* non-detection tests for each; R7's false-alarm rate is verified by
  simulation (`test_wishart_false_alarm_rate`).
- **The 12-stage pipeline** matches §6 stage-for-stage, including rejected-scene
  recording, gate-as-normal-outcome, append-only reanalysis with `supersedes_run_id`,
  and retraction instead of deletion.
- **The same-orbit S1 rule** is genuinely one predicate used on every admission path
  (`_orbit_reason`), exactly as claimed.
- **Diagnostic taxonomy** is complete with stable severity/remedy metadata, rendered in
  the UI; the three-state timeline is real and visually distinct.
- **The job queue** leases atomically (`BEGIN IMMEDIATE` + `UPDATE … RETURNING`), WAL,
  `busy_timeout=10000`, expired-lease reclaim, retry-then-fail with `RUN_FAILED`
  diagnostics, per-job progress reporting that the UI renders as honest
  determinate/indeterminate bars.
- **GEE backend** is properly optional: lazy imports, off by default, per-project toggle,
  baseline/backtest-only (`adapter_for` refuses `forward`), EECU ledger with pre-flight
  refusal, batch-export threshold with refusal when no bucket — all covered by tests.
- **Evidence bundles** contain manifest hashes (verified by test), method.md with
  citation/threshold-provenance/limitations, and deliberately exclude VLM text.
- **Security posture** matches spec §16: credentials presence-only in `/health`, secret
  filtering in the JSON log formatter, no secrets in exports.
- **The frontend** implements the dashboard/health strip/unified feed, four-plus tabbed
  project detail, the four-step wizard with coverage preview and orbit picker, the
  polarity-aware calibration histogram with draggable threshold and would-have-alerted
  cards, alert triage, the VLM disclaimer block, and identical-stretch swipe compare.
- Frontend stack matches §3.3 exactly (MapLibre + terra-draw + deck.gl + recharts, no
  Leaflet); test suite is fully offline and runs in ~5 s.

---

## 7. Priorities

1. **Fix C1** (two-band chip crash) — one-line guard plus a chips-for-every-recipe test;
   unblocks alerts for the default recipe.
2. **Fix C2** (stale `lastrowid`) — small change in `_write_observation` plus a
   reanalyse regression test; prevents silent provenance corruption.
3. **Fix C3 / H1 / H4** — write `adapter` into baseline meta; make the Docker
   reference gate actually fail the build (or remove the claim); send
   `backtest_artifact_id` from the wizard.
4. **Decide on tiles (H2)** — either wire TiTiler + token verification end-to-end or
   delete the tiler service, the token endpoint, and the doc claims.
5. **Reconcile the doc drift in §5** — the two recipe-YAML limitations (R2, R8) directly
   contradict checked-off `BUILD.md` items and will mislead users of the wizard, which
   displays those strings verbatim.
6. Then work down §3 (methodology-scoped mask params and the poll race first — both
   touch the multi-methodology model the codebase is clearly invested in).

---

## Appendix A — Verified at runtime during this audit

- `chips.display_bands(vegetation_loss_optical)` → `('B04', 'B03')`; `chips.render`
  raises `ValueError: operands could not be broadcast together with shapes (100,2) (3,)`.
- sqlite3 `lastrowid` after `INSERT OR IGNORE` on a duplicate returns the previous
  insert's id (truthy), so the `or SELECT` fallback in `_write_observation` cannot fire.
- `baseline.save` metadata contains no `adapter` key; `PROVENANCE_DISCONTINUITY` is
  unreachable.
- `pytest tests -q` → 91 passed, 4 skipped (real-data tests skip without network).
- `grep` over `frontend/src` finds zero references to TiTiler, port 8001, `/tiles`, or
  `tileToken` consumption.
