"""The forward-run pipeline, stages 1-12 of spec §6, plus the job handlers."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone

import numpy as np

from . import (adapters, alerting, artifacts, baseline as bl, chips, config, db, gee,
               explain, geo, masks, notify, vlm)
from .constellation import spans_event
from .detectors import REGISTRY as DETECTORS
from .log import log
from .recipes import REGISTRY as RECIPES
from .worker import handler

EPOCH = datetime(2015, 1, 1, tzinfo=timezone.utc)


def _dt(iso: str) -> datetime:
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _days(iso: str) -> float:
    return (_dt(iso) - EPOCH).days


def _ctx(con, project_id: int, methodology_id: int | None = None):
    """A project may carry several methodologies; every job addresses exactly one.
    Recipe, parameters and baseline all come from that methodology, never from the
    project row, so two methodologies cannot read each other's baseline."""
    p = con.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    m = db.methodology(con, project_id, methodology_id)
    recipe = RECIPES[m["recipe_id"]]
    return p, m, recipe, DETECTORS[recipe["detector"]], json.loads(m["params_json"]), \
        json.loads(p["aoi_geojson"])


def _set_baseline(con, methodology_id: int, artifact_id, start, end) -> None:
    con.execute("UPDATE project_methodology SET baseline_artifact_id=?,"
                " baseline_start=?, baseline_end=?, updated_at=? WHERE id=?",
                (artifact_id, start, end, db.now(), methodology_id))


def _adapter(con, project, purpose: str = "forward", sensor: str | None = None):
    a = gee.adapter_for(con, project, purpose)   # optional, off unless opted in
    if a is not None:
        return a
    prefs = json.loads(project["adapter_preference"])
    a = adapters.first_healthy(prefs, sensor=sensor)
    if a.name != prefs[0]:
        db.diagnostic(con, "ADAPTER_FALLBACK", "warning",
                      f"{prefs[0]} was unreachable; {a.name} served this request.",
                      project_id=project["id"], detail={"used": a.name})
    return a


def _adapter_named(con, project, name: str):
    return (gee.GeeAdapter(con, project["id"]) if name == "gee"
            else adapters.get_adapter(name))


# --- stages 1-2: discover and admit ----------------------------------------

@handler("poll")
def poll(con, job, payload):
    """One poll covers the whole project. A methodology_id in the payload narrows it
    to that one; otherwise every methodology gets its own discovery pass, because two
    methodologies may want different sensors and admit different scenes."""
    if payload.get("methodology_id") is None:
        total = 0
        for row in db.ensure_methodologies(con, payload["project_id"]):
            total += _poll_one(con, job, payload, row["id"])["enqueued"]
        return {"enqueued": total}
    return _poll_one(con, job, payload, payload["methodology_id"])


def _poll_one(con, job, payload, methodology_id: int):
    p, m, recipe, det, params, aoi = _ctx(con, payload["project_id"], methodology_id)
    adapter = _adapter(con, p, sensor=recipe["sensor"])
    sensor_clause = (" AND collection LIKE 'sentinel-1%'" if recipe["sensor"] == "S1"
                     else " AND collection NOT LIKE 'sentinel-1%'")
    since = con.execute("SELECT MAX(sensed_at) t FROM observation WHERE project_id=?" +
                        sensor_clause, (p["id"],)).fetchone()["t"]
    start = (_dt(since) + timedelta(seconds=1)) if since else \
        datetime.now(timezone.utc) - timedelta(days=30)
    end = datetime.now(timezone.utc)
    db.progress(con, job, 0, None, "Checking for new imagery…")
    scenes = adapter.search(aoi, start, end)
    if not scenes:
        db.diagnostic(con, "NO_ACQUISITION", "info",
                      f"No new scene covering the AOI between {start.date()} and "
                      f"{end.date()}.", project_id=p["id"])
    _check_overdue(con, p, recipe)
    n = 0
    for s in _admit(con, p, recipe, scenes, m["id"]):
        db.enqueue(con, "run", {"project_id": p["id"], "methodology_id": m["id"],
                                "scene_id": s.scene_id, "adapter": adapter.name,
                                "item": s.item, "kind": "forward"}, p["id"])
        n += 1
    return {"enqueued": n}


def _orbit_reason(project, recipe, scene) -> str | None:
    """The same-orbit / same-pass rule of spec §4.3. Every path that admits a radar
    scene into a project — forward polling, baseline fitting, backtests — goes through
    this one predicate, because a scene admitted anywhere is a scene compared."""
    if recipe["sensor"] != "S1":
        return None
    if scene.relative_orbit != project["s1_relative_orbit"]:
        return (f"relative orbit {scene.relative_orbit} differs from the project's "
                f"{project['s1_relative_orbit']}; radar backscatter is "
                "geometry-dependent and may not be mixed")
    if project["s1_pass_direction"] and scene.pass_direction != project["s1_pass_direction"]:
        return (f"pass direction {scene.pass_direction} differs from the project's "
                f"{project['s1_pass_direction']}")
    return None


def _admit(con, project, recipe, scenes, methodology_id: int):
    """Stage 2. Rejected scenes are still written to `observation`.

    "Already seen" is judged per methodology: a scene another methodology has scored
    must still be admitted here, or the second methodology would permanently miss
    everything the first one happened to run first (their workers race). Only
    scene-intrinsic rejections — coverage and orbit/pass, not mask-chain outcomes —
    are shared project-wide, because those re-reject identically for every methodology
    on the same sensor."""
    from shapely.geometry import shape
    aoi = shape(json.loads(project["aoi_geojson"]))
    seen = {r["scene_id"] for r in con.execute(
        "SELECT scene_id FROM observation WHERE project_id=? AND status='rejected'"
        " AND (rejection_reason LIKE 'AOI coverage%'"
        "      OR rejection_reason LIKE 'relative orbit%'"
        "      OR rejection_reason LIKE 'pass direction%')"
        " UNION SELECT o.scene_id FROM run r"
        " JOIN observation o ON o.id=r.target_observation_id"
        " WHERE r.project_id=? AND r.methodology_id=? AND r.status='ok'",
        (project["id"], project["id"], methodology_id))}
    out = []
    for s in scenes:
        if s.scene_id in seen:
            continue
        reason = None
        try:
            cover = shape(s.item["geometry"]).intersection(aoi).area / aoi.area
        except Exception:
            cover = 1.0
        if cover < 0.99:
            reason = f"AOI coverage {cover:.0%} below the 99% requirement"
        reason = _orbit_reason(project, recipe, s) or reason
        if reason:
            _write_observation(con, project, s, valid_fraction=0.0, mask_summary={},
                               status="rejected", rejection_reason=reason)
            if "relative orbit" in reason:
                db.diagnostic(con, "ORBIT_MISMATCH", "error", reason,
                              project_id=project["id"], detail={"scene": s.scene_id})
        else:
            out.append(s)
    return out


def _write_observation(con, project, scene, valid_fraction, mask_summary, status,
                       rejection_reason=None, adapter_name=None):
    cur = con.execute(
        "INSERT OR IGNORE INTO observation(project_id,scene_id,platform,collection,"
        "adapter,sensed_at,discovered_at,relative_orbit,pass_direction,valid_fraction,"
        "cloud_fraction,mask_summary_json,status,rejection_reason,source_uri,"
        "stac_item_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (project["id"], scene.scene_id, scene.platform, scene.collection,
         adapter_name or scene.adapter, scene.datetime, db.now(),
         scene.relative_orbit, scene.pass_direction, valid_fraction,
         scene.cloud_cover, json.dumps(mask_summary), status, rejection_reason,
         scene.source_uri, json.dumps(scene.item)))
    if cur.rowcount:
        return cur.lastrowid
    # The insert was ignored (scene already observed). sqlite's lastrowid keeps the
    # *previous* insert's id in that case — it is never None — so the row count is the
    # only reliable "did this insert happen" signal.
    return con.execute(
        "SELECT id FROM observation WHERE project_id=? AND scene_id=?",
        (project["id"], scene.scene_id)).fetchone()["id"]


def _check_overdue(con, project, recipe):
    row = con.execute("SELECT MAX(sensed_at) t FROM observation WHERE project_id=?"
                      " AND status='usable'", (project["id"],)).fetchone()
    # A site that has never returned a usable scene is overdue too, counted from
    # activation — silence from day one is exactly the case worth shouting about.
    last = row["t"] or project["created_at"]
    revisit = 6 if recipe["sensor"] == "S1" else 5
    days = (datetime.now(timezone.utc) - _dt(last)).days
    if days > revisit * 3:
        db.diagnostic(con, "ACQUISITION_OVERDUE", "warning",
                      f"No usable observation for {days} days "
                      f"({days // revisit} expected revisit cycles"
                      f"{'; none has ever been usable' if not row['t'] else ''}).",
                      project_id=project["id"])


# --- stages 3-5: load, mask, gate ------------------------------------------

def _load_masked(con, project, m, recipe, adapter, scene):
    aoi = json.loads(project["aoi_geojson"])
    det = DETECTORS[recipe["detector"]]
    bands = list(recipe["bands"])
    assets = scene.item.get("assets", {})
    for optional in ("cs", "cloud_probability"):
        if optional in assets and optional not in bands:
            bands.append(optional)
    data = adapters.load_scene(
        adapter, scene, aoi, bands,
        resolution=recipe.get("resolution_m"), target_crs=project["analysis_crs"],
        buffer_px=getattr(det.spec, "kernel_radius_px", 0))
    # Mask parameters come from the addressed methodology first: two methodologies on
    # one AOI can need different exclusions (max_slope_deg, cloud thresholds), and the
    # project row only mirrors whichever methodology was created first.
    mask_params = {**json.loads(project["params_json"]),
                   **json.loads(m["params_json"]), "_aoi": aoi}
    invalid, summary, valid_fraction = masks.apply_chain(data, recipe["mask_chain"],
                                                         mask_params)
    absent = [k for k, v in summary.items() if v is None]
    if absent:
        db.diagnostic(con, "MASK_UNAVAILABLE", "warning",
                      f"The recipe declares {', '.join(absent)} but "
                      f"{'it was' if len(absent) == 1 else 'they were'} not applied to "
                      f"{scene.datetime[:10]}. Anything those masks would have removed "
                      "is still in the score.", project_id=project["id"],
                      detail={"scene": scene.scene_id, "masks": absent})
    for k, v in data.items():
        if not k.startswith("_"):
            data[k] = np.where(invalid, np.nan, v)
    return data, invalid, summary, valid_fraction


def _gate(con, project, recipe, scene, valid_fraction, summary, adapter_name):
    floor = recipe.get("min_valid_fraction", 0.70)
    if valid_fraction >= floor:
        return None
    obs = _write_observation(con, project, scene, valid_fraction, summary,
                            status="partial" if valid_fraction > 0 else "rejected",
                            rejection_reason=f"valid fraction {valid_fraction:.0%} "
                                             f"below the {floor:.0%} gate",
                            adapter_name=adapter_name)
    db.diagnostic(con, "LOW_VALID_FRACTION", "warning",
                  f"Only {valid_fraction:.0%} of the area was usable on "
                  f"{scene.datetime[:10]}; the {floor:.0%} gate was not met, so no "
                  "score was produced.", project_id=project["id"],
                  detail={"scene": scene.scene_id, "masks": summary})
    _persistent_occlusion(con, project)
    return obs


def _persistent_occlusion(con, project):
    recent = [r["status"] for r in con.execute(
        "SELECT status FROM observation WHERE project_id=? ORDER BY sensed_at DESC"
        " LIMIT 4", (project["id"],))]
    # Fire once, on the transition INTO an occlusion episode: the 3 newest are all
    # unusable and the scene just before them was usable (or there is none). As the
    # streak lengthens the 4th entry becomes unusable too, so it won't re-fire until a
    # usable scene resets it. Without this, a backtest over a cloudy site emitted the
    # same alert once per cloudy scene (120 for one 3-year project).
    if (len(recent) >= 3 and all(s != "usable" for s in recent[:3])
            and (len(recent) == 3 or recent[3] == "usable")):
        db.diagnostic(con, "PERSISTENT_OCCLUSION", "warning",
                      "Three or more consecutive acquisitions were unusable. This site "
                      "is currently unobservable with this recipe; consider pairing it "
                      "with a radar recipe.", project_id=project["id"])


# --- stage 6: align --------------------------------------------------------

def _misregistration(reference: np.ndarray, target: np.ndarray) \
        -> tuple[np.ndarray, float, bool]:
    """Phase correlation against the baseline. Returns (shift_yx, magnitude, estimable).
    A featureless scene carries no alignment information; saying so is better than
    reporting a shift the data cannot support."""
    from skimage.registration import phase_cross_correlation
    a = np.nan_to_num(reference, nan=0.0).astype("float64")
    b = np.nan_to_num(target, nan=0.0).astype("float64")
    if a.shape != b.shape or min(a.shape) < 16:
        return np.zeros(2), 0.0, False
    a, b = a - a.mean(), b - b.mean()
    if a.std() < 1e-6 or b.std() < 1e-6:
        return np.zeros(2), 0.0, False
    # Unnormalised correlation yields a meaningful RMS error here. The library's
    # phase-only normalisation reports values near 1 even for translated noisy scenes.
    shift, error, _ = phase_cross_correlation(a, b, upsample_factor=4,
                                              normalization=None)
    if not np.isfinite(error) or error > 0.9:
        return np.zeros(2), 0.0, False          # no usable correlation peak
    return shift.astype("float64"), float(np.hypot(*shift)), True


def _coregister(data: dict, invalid: np.ndarray, shift_yx: np.ndarray):
    """Apply a sub-pixel target-to-reference correction to every loaded band.

    Continuous measurements use bilinear interpolation; categorical masks use nearest
    neighbour. Newly exposed edge pixels are invalid, so a correction cannot invent
    valid observations outside the source footprint.
    """
    from scipy.ndimage import shift as nd_shift
    categorical = {"SCL"}
    for name, values in list(data.items()):
        if name.startswith("_") or not isinstance(values, np.ndarray) or values.ndim != 2:
            continue
        order = 0 if name in categorical else 1
        data[name] = nd_shift(values.astype("float32"), shift_yx, order=order,
                              mode="constant", cval=np.nan, prefilter=False)
    corrected_invalid = nd_shift(invalid.astype("uint8"), shift_yx, order=0,
                                 mode="constant", cval=1, prefilter=False).astype(bool)
    for name, values in list(data.items()):
        if not name.startswith("_") and isinstance(values, np.ndarray) and values.ndim == 2:
            data[name] = np.where(corrected_invalid, np.nan, values)
    return corrected_invalid


def _saturation(data: dict, threshold: float = 0.05) -> tuple[float, list[str]]:
    """Return the largest clipped-pixel fraction among optical reflectance bands."""
    fractions = {}
    for name, values in data.items():
        if not name.startswith("B") or not isinstance(values, np.ndarray):
            continue
        finite = values[np.isfinite(values)]
        if finite.size:
            fractions[name] = float(((finite <= -0.0999) | (finite >= 0.9999)).mean())
    peak = max(fractions.values(), default=0.0)
    return peak, sorted(name for name, fraction in fractions.items()
                        if fraction >= threshold)


def _previous_score(con, project_id: int, detector_id: str, sensed_at: str,
                    kind: str) -> np.ndarray | None:
    """Load the immutable prior score for stateful sequential detectors."""
    row = con.execute(
        "SELECT a.sha256 FROM run r JOIN observation o ON o.id=r.target_observation_id"
        " JOIN artifact a ON a.id=r.score_raster_id WHERE r.project_id=?"
        " AND r.detector_id=? AND r.kind=? AND r.status='ok' AND o.sensed_at<?"
        " ORDER BY o.sensed_at DESC LIMIT 1",
        (project_id, detector_id, kind, sensed_at)).fetchone()
    if not row:
        return None
    import rasterio
    with rasterio.open(artifacts.path_for(row["sha256"])) as src:
        return src.read(1).astype("float32")


def _radar_candidates(project, aoi: dict, start: datetime, end: datetime):
    adapter = adapters.get_adapter("planetary")
    scenes = adapter.search(aoi, start, end, collection=adapters.RTC_COLLECTION,
                            max_cloud=100)
    scenes = [s for s in scenes
              if s.relative_orbit == project["s1_relative_orbit"] and
              (not project["s1_pass_direction"] or
               s.pass_direction == project["s1_pass_direction"])]
    return adapter, scenes


def _radar_on_grid(adapter, scene, aoi: dict, crs: str, transform, shape):
    """Load the paired RTC scene and reproject it onto the optical analysis grid."""
    from rasterio.warp import Resampling, reproject
    source = adapters.load_scene(adapter, scene, aoi, ["VV", "VH"])
    out = {}
    for band in ("VV", "VH"):
        dst = np.full(shape, np.nan, "float32")
        reproject(source[band], dst, src_transform=source["_transform"],
                  src_crs=source["_crs"], dst_transform=transform, dst_crs=crs,
                  src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
        out[band] = dst
    return out


def _paired_radar(con, project, aoi: dict, sensed_at: str, crs: str, transform,
                  shape):
    target = _dt(sensed_at)
    adapter, scenes = _radar_candidates(project, aoi, target - timedelta(days=6),
                                        target + timedelta(days=6))
    if not scenes:
        return None, None, None
    scene = min(scenes, key=lambda s: abs((_dt(s.datetime) - target).total_seconds()))
    data = _radar_on_grid(adapter, scene, aoi, crs, transform, shape)
    valid_fraction = float(np.all([np.isfinite(data[b]) for b in ("VV", "VH")], 0).mean())
    obs_id = _write_observation(con, project, scene, valid_fraction,
                                {"nodata": int(round((1 - valid_fraction) * np.prod(shape)))},
                                "usable" if valid_fraction > 0 else "rejected",
                                adapter_name=adapter.name)
    return data, scene, obs_id


# --- baseline --------------------------------------------------------------

@handler("baseline")
def fit_baseline(con, job, payload):
    p, m, recipe, det, params, aoi = _ctx(con, payload["project_id"],
                                          payload.get("methodology_id"))
    adapter = _adapter(con, p, "baseline", recipe["sensor"])
    years = recipe.get("baseline", {}).get("min_years", 2)
    end = _dt(payload["before"]) if payload.get("before") else datetime.now(timezone.utc)
    start = end - timedelta(days=365 * years + 30)
    db.progress(con, job, 0, None, "Searching for baseline scenes…")
    scenes = adapter.search(aoi, start, end, max_cloud=60)
    skipped_orbit = 0
    for s in list(scenes):
        if _orbit_reason(p, recipe, s):
            scenes.remove(s)
            skipped_orbit += 1
    if skipped_orbit:
        log.info("baseline scenes skipped for orbit", extra={"extra": {
            "project": p["id"], "skipped": skipped_orbit,
            "orbit": p["s1_relative_orbit"]}})
    if not scenes:
        db.diagnostic(con, "NO_ACQUISITION", "error",
                      f"No scene matching this project's constraints between "
                      f"{start.date()} and {end.date()}; the baseline cannot be fitted.",
                      project_id=p["id"])
        return {"observations": 0, "fitted": False}
    stack, obs_ids, crs, transform = [], [], None, None
    for i, s in enumerate(scenes):
        db.progress(con, job, i, len(scenes),
                    f"Fitting baseline: reading {s.datetime[:10]} "
                    f"({i + 1} of {len(scenes)})")
        try:
            data, invalid, summary, vf = _load_masked(con, p, m, recipe, adapter, s)
        except Exception as e:  # noqa: BLE001
            log.warning("baseline scene skipped", extra={"extra": {
                "scene": s.scene_id, "error": str(e)}})
            continue
        if vf < recipe.get("min_valid_fraction", 0.70):
            continue
        crs, transform = data["_crs"], data["_transform"]
        stack.append((_days(s.datetime), data))
        obs_ids.append(_write_observation(con, p, s, vf, summary, "usable",
                                          adapter_name=adapter.name))
    need = recipe.get("baseline", {}).get("min_observations", 30)
    if len(stack) < max(4, det.spec.min_baseline_observations // 4):
        db.diagnostic(con, "BASELINE_TOO_SHORT", "error",
                      f"Only {len(stack)} usable observations in {years} years; this "
                      f"method needs about {need}. The project cannot run until more "
                      "imagery accumulates.", project_id=p["id"])
        con.execute("UPDATE project SET status='failed', updated_at=? WHERE id=?",
                    (db.now(), p["id"]))
        return {"observations": len(stack), "fitted": False}
    if len(stack) < need:
        db.diagnostic(con, "BASELINE_TOO_SHORT", "warning",
                      f"Baseline fitted on {len(stack)} observations, fewer than the "
                      f"{need} this method recommends; scores are less reliable.",
                      project_id=p["id"])
    stack.sort(key=lambda x: x[0])
    fitted = det.fit_baseline(stack, params)
    if det.spec.id == "builtup_dual":
        radar_stack, radar_ids = [], []
        shape = next(v.shape for k, v in stack[-1][1].items() if not k.startswith("_"))
        try:
            radar_adapter, radar_scenes = _radar_candidates(p, aoi, start, end)
            for rs in radar_scenes:
                rd = _radar_on_grid(radar_adapter, rs, aoi, crs, transform, shape)
                vf = float(np.all([np.isfinite(rd[b]) for b in ("VV", "VH")], 0).mean())
                if vf < recipe.get("min_valid_fraction", 0.70):
                    continue
                radar_stack.append((_days(rs.datetime), rd))
                radar_ids.append(_write_observation(
                    con, p, rs, vf, {"nodata": int(round((1 - vf) * np.prod(shape)))},
                    "usable", adapter_name=radar_adapter.name))
            if len(radar_stack) >= 8:
                radar_fit = det.fit_radar_baseline(radar_stack)
                fitted["coef"].update(radar_fit["coef"])
                fitted["sigma"].update(radar_fit["sigma"])
                fitted["coef"]["_RADAR_COUNT"] = np.full(
                    (1, *shape), len(radar_stack), "float32")
                fitted["sigma"]["_RADAR_COUNT"] = np.zeros(shape, "float32")
                obs_ids.extend(radar_ids)
            else:
                db.diagnostic(con, "NO_ACQUISITION", "warning",
                              "No same-orbit Sentinel-1 observations were available "
                              "for the construction baseline; runs will require three "
                              "optical confirmations.", project_id=p["id"])
        except Exception as e:  # noqa: BLE001
            db.diagnostic(con, "SOURCE_UNREACHABLE", "warning",
                          f"The construction radar baseline could not be loaded: {e}. "
                          "The optical stream remains usable.", project_id=p["id"])
    dates = (min(s.datetime for s in scenes), max(s.datetime for s in scenes))
    aid = bl.save(con, fitted, crs, transform, obs_ids, dates,
                  det.spec.id, det.spec.version, adapter=adapter.name)
    if adapter.name == "gee":
        params["_gee_sidecar_artifact_id"] = gee.sidecar(
            con, p["id"], "baseline",
            {"script": f"{det.spec.id} v{det.spec.version} harmonic baseline fit over "
                       f"{len(stack)} GEE-served scenes",
             "asset_ids": [s.item["id"] for s in scenes],
             "dates": list(dates), "baseline_artifact_id": aid,
             "observation_ids": obs_ids})
    stretch = chips.stretch_from(stack[-1][1], chips.display_bands(recipe))
    params["_stretch"] = stretch
    con.execute("UPDATE project_methodology SET baseline_artifact_id=?,"
                " baseline_start=?, baseline_end=?, params_json=?, updated_at=?"
                " WHERE id=?",
                (aid, dates[0], dates[1], json.dumps(params), db.now(), m["id"]))
    if spans_event(dates[0], dates[1], recipe["sensor"]):
        db.diagnostic(con, "BASELINE_SPANS_CONSTELLATION_EVENT", "warning",
                      "The baseline period spans a known constellation change.",
                      project_id=p["id"],
                      detail={"events": spans_event(dates[0], dates[1],
                                                    recipe["sensor"])})
    return {"observations": len(stack), "artifact_id": aid, "fitted": True}


# --- stages 7-12: score through notify -------------------------------------

def _scene_ref_from_item(item: dict, adapter_name: str,
                         scene_id: str | None = None) -> adapters.SceneRef:
    """Rebuild a SceneRef from a stored STAC item — run payloads and chip before-frames
    both persist whole items, so a scene can be re-fetched without a catalogue hit."""
    p = item.get("properties", {})
    return adapters.SceneRef(
        scene_id=scene_id or item["id"],
        platform=p.get("platform", "unknown"),
        datetime=p["datetime"],
        collection=item.get("collection", ""),
        adapter=adapter_name,
        relative_orbit=p.get("sat:relative_orbit"),
        pass_direction=(p.get("sat:orbit_state") or "").upper() or None,
        source_uri=item.get("links", [{}])[0].get("href", ""),
        item=item)

@handler("run")
def run_scene(con, job, payload):
    p, m, recipe, det, params, aoi = _ctx(con, payload["project_id"],
                                          payload.get("methodology_id"))
    scene = _scene_ref_from_item(payload["item"], payload["adapter"],
                                 scene_id=payload["scene_id"])
    adapter = _adapter_named(con, p, payload["adapter"])
    kind = payload.get("kind", "forward")
    # Reproducibility-relevant: a GEE-served run is marked as one everywhere.
    backend = "gee" if adapter.name == "gee" else "local"

    if m["baseline_artifact_id"] is None and det.spec.needs_baseline:
        db.enqueue(con, "baseline", {"project_id": p["id"],
                                     "methodology_id": m["id"],
                                     "before": scene.datetime}, p["id"])
        db.enqueue(con, "run", payload, p["id"],
                   available_at=(datetime.now(timezone.utc)
                                 + timedelta(minutes=1)).isoformat(timespec="seconds"))
        return {"deferred": "baseline"}

    data, invalid, summary, vf = _load_masked(con, p, m, recipe, adapter, scene)
    if _gate(con, p, recipe, scene, vf, summary, adapter.name):
        return {"gated": True}
    obs_id = _write_observation(con, p, scene, vf, summary, "usable",
                                adapter_name=adapter.name)

    radar_scene = None
    radar_obs_id = None
    if det.spec.id == "builtup_dual":
        shape = next(v.shape for k, v in data.items() if not k.startswith("_"))
        try:
            radar_data, radar_scene, radar_obs_id = _paired_radar(
                con, p, aoi, scene.datetime, data["_crs"], data["_transform"], shape)
            if radar_data:
                for band, values in radar_data.items():
                    data[band] = np.where(invalid, np.nan, values)
        except Exception as e:  # noqa: BLE001
            db.diagnostic(con, "SOURCE_UNREACHABLE", "warning",
                          f"The paired radar observation could not be loaded: {e}. "
                          "This run uses the optical stream only.", project_id=p["id"])

    started, caveats, notes = db.now(), [], []
    run_uuid = db.new_uuid()
    base = bl.load(con, m["baseline_artifact_id"]) if det.spec.needs_baseline else None
    if base and base["meta"].get("adapter") not in (None, adapter.name):
        caveats.append("PROVENANCE_DISCONTINUITY")
    if vf < 1.0:
        caveats.append("LOW_VALID_FRACTION") if vf < 0.95 else None

    try:
        pair = getattr(det, "alignment_pair", None)
        shift_yx, shift, estimable = (_misregistration(*pair(data, base))
                                      if (base and pair)
                                      else (np.zeros(2), 0.0, False))
        coregistration = None
        if estimable and shift > params.get("max_misregistration_px", 0.5):
            db.diagnostic(con, "MISREGISTRATION", "warning",
                          f"Estimated shift of {shift:.2f} px against the baseline.",
                          project_id=p["id"],
                          detail={"shift_px": shift,
                                  "shift_yx_px": shift_yx.tolist(),
                                  "auto_coregister": bool(params.get("auto_coregister", False))})
            if params.get("auto_coregister", False):
                prior_invalid = invalid.copy()
                invalid = _coregister(data, invalid, shift_yx)
                edge_pixels = int((invalid & ~prior_invalid).sum())
                summary = {**summary, "coregistration_edges": edge_pixels}
                vf = float(1.0 - invalid.mean())
                con.execute("UPDATE observation SET valid_fraction=?, mask_summary_json=?"
                            " WHERE id=?", (vf, json.dumps(summary), obs_id))
                coregistration = {"applied": True, "shift_px": shift,
                                  "shift_yx_px": shift_yx.tolist(),
                                  "edge_pixels_invalidated": edge_pixels}
            else:
                # No caveat here on purpose: the run aborts, and an aborted run never
                # produces the alert that would carry it. The diagnostic is the record.
                raise RuntimeError(f"misregistration {shift:.2f} px exceeds tolerance")

        score_params = params
        if det.spec.id == "radd_probabilistic":
            prior = _previous_score(con, p["id"], det.spec.id, scene.datetime, kind)
            if prior is not None:
                score_params = {**params, "_previous_probability": prior}
        result = det.score(_days(scene.datetime), data, base, score_params)
        if coregistration:
            result.aux["coregistration"] = coregistration
        saturation_fraction, saturated_bands = _saturation(
            data, float(params.get("saturation_fraction_threshold", 0.05)))
        if saturated_bands:
            db.diagnostic(
                con, "SATURATION", "warning",
                f"{saturation_fraction:.0%} of valid pixels are clipped in "
                f"{', '.join(saturated_bands)}; index differences may be compressed.",
                project_id=p["id"],
                detail={"scene": scene.scene_id, "fraction": saturation_fraction,
                        "bands": saturated_bands})
            caveats.append("SATURATION")
        if radar_scene is not None:
            result.aux["radar_scene"] = {
                "scene_id": radar_scene.scene_id, "sensed_at": radar_scene.datetime,
                "source_uri": radar_scene.source_uri,
                "relative_orbit": radar_scene.relative_orbit,
                "pass_direction": radar_scene.pass_direction,
                "observation_id": radar_obs_id,
            }
    except Exception as e:  # noqa: BLE001
        import traceback
        con.execute(
            "INSERT INTO run(uuid,project_id,methodology_id,kind,detector_id,"
            "detector_version,params_json,target_observation_id,started_at,"
            "finished_at,status,error_json,compute_backend)"
            " VALUES (?,?,?,?,?,?,?,?,?,?, 'failed',?,?)",
            (run_uuid, p["id"], m["id"], kind, det.spec.id, det.spec.version,
             json.dumps(params), obs_id, started, db.now(),
             json.dumps({"error": str(e), "traceback": traceback.format_exc()}),
             backend))
        raise

    # A detector may derive its own threshold from the scene (R4 `otsu` mode); when it
    # does, the derived value is what the run is judged and recorded against.
    threshold = float(result.aux.get("derived_threshold")
                      or params.get("threshold", det.spec.default_threshold))
    if det.spec.score_polarity == "lower_is_more_change":
        crossing = np.nan_to_num(result.score, nan=np.inf) <= threshold
        peak_of = np.nanmin
    else:
        crossing = np.nan_to_num(result.score, nan=-np.inf) >= threshold
        peak_of = np.nanmax
    px_area = geo.pixel_area_m2(data["_transform"])
    kept_mask, comp_areas, changed_area = alerting.components(
        crossing, px_area, params.get("min_mapping_unit_m2", 0))

    # Direction is reported across the changed area, not the whole scene (spec §12.1).
    residuals = result.aux.pop("_residuals", None)
    if residuals and kept_mask.any():
        result.direction = {k: float(np.nanmedian(v[kept_mask]))
                            for k, v in residuals.items()
                            if np.isfinite(v[kept_mask]).any()}
    loewner = result.aux.pop("_loewner", None)
    if loewner is not None:
        n = max(int(kept_mask.sum()), 1)
        up = float((loewner["increase"] & kept_mask).sum()) / n
        down = float((loewner["decrease"] & kept_mask).sum()) / n
        result.direction = {
            "loewner": ("increase" if up > 0.6 else
                        "decrease" if down > 0.6 else "indefinite"),
            "fraction_increase": up, "fraction_decrease": down,
            "fraction_indefinite": max(0.0, 1.0 - up - down)}
    cross_stream = result.aux.pop("_cross_stream_agreement", None)
    if cross_stream is not None:
        n = max(int(kept_mask.sum()), 1)
        result.aux["cross_stream_agreement_fraction"] = float(
            (cross_stream & kept_mask).sum()) / n

    if result.notes:
        notes += result.notes
        caveats.append("DETECTOR_ASSUMPTION")
        db.diagnostic(con, "DETECTOR_ASSUMPTION", "warning", result.notes[0],
                      project_id=p["id"], detail={"notes": result.notes})
    if base and spans_event(base["meta"]["fitted_from"], base["meta"]["fitted_to"],
                            recipe["sensor"]):
        caveats.append("BASELINE_SPANS_CONSTELLATION_EVENT")

    finite = result.score[np.isfinite(result.score)]
    qs = ([50, 90, 95, 99] if det.spec.score_polarity == "higher_is_more_change"
          else [50, 10, 5, 1])
    pct = np.percentile(finite, qs).tolist() if finite.size else [None] * 4
    aoi_area_m2 = p["aoi_area_km2"] * 1e6
    was_in_episode = alerting.in_episode(con, p["id"], m["id"])
    exit_ratio = params.get("exit_ratio", config.get("alerts.default_exit_ratio", 0.7))
    peak = float(peak_of(finite)) if finite.size else float("nan")
    if det.spec.score_polarity == "lower_is_more_change":
        # hysteresis runs the other way for a p-value: staying in an episode is easier,
        # so the exit test relaxes the threshold upward rather than downward.
        still_in = peak <= (threshold / max(exit_ratio, 1e-6) if was_in_episode
                            else threshold)
    else:
        still_in = peak >= (threshold * exit_ratio if was_in_episode else threshold)

    summary_json = {
        # p90/p95/p99 are the *tail toward change*: the upper tail for a z-score,
        # the lower tail for a p-value. The percentile keys keep their names so the
        # UI stays one component; `polarity` below says which way they run.
        "score_p50": pct[0], "score_p90": pct[1], "score_p95": pct[2],
        "score_p99": pct[3], "score_max": float(peak) if finite.size else None,
        "valid_pixels": int(result.valid.sum()), "valid_fraction": vf,
        "changed_pixels": int(kept_mask.sum()), "changed_area_m2": changed_area,
        "n_components": len(comp_areas),
        "largest_component_m2": comp_areas[0] if comp_areas else 0.0,
        "direction": result.direction, "aux": result.aux, "notes": notes,
        "threshold": threshold, "mask_summary": summary,
        "in_episode": bool(still_in and changed_area > 0),
        "units": det.spec.score_units,
        # The headline is the polarity-correct extreme: the max for a z-score, the
        # minimum for a p-value. Everything that ranks or plots a run reads this,
        # so a lower-is-worse detector is not silently read upside down.
        "score_headline": float(peak) if finite.size else None,
        "polarity": det.spec.score_polarity,
    }

    score_id = artifacts.put_cog(con, result.score, data["_crs"], data["_transform"],
                                 meta={"kind": "score", "units": det.spec.score_units,
                                       "run": run_uuid})
    mask_id = artifacts.put_cog(con, (~invalid).astype("float32"), data["_crs"],
                                data["_transform"],
                                meta={"kind": "valid_mask", "run": run_uuid,
                                      "masks": summary})
    mask_layers = {}
    for name, layer in data.get("_mask_components", {}).items():
        if layer.any():
            mask_layers[name] = artifacts.put_cog(
                con, (~layer).astype("float32"), data["_crs"], data["_transform"],
                meta={"kind": "mask_layer", "mask": name, "run": run_uuid})
    summary_json["aux"]["mask_layers"] = mask_layers

    # stage 10: decide
    needed = int(params.get("consecutive_confirmations", 1))
    if changed_area <= 0:
        skip = "below_mmu" if crossing.any() else "below_threshold"
    elif was_in_episode and not still_in:
        skip = "hysteresis"
    else:
        skip = None
    summary_json["no_alert_reason"] = skip
    summary_json["no_alert_reason_text"] = (explain.no_alert_reason(skip) if skip
                                            else None)
    if kind == "backtest" and changed_area > 0:
        calibration_chips = _render_chips(con, p, recipe, adapter, base, data,
                                           kept_mask, scene, params)
        summary_json["aux"]["calibration_chips"] = calibration_chips

    run_id = con.execute(
        "INSERT INTO run(uuid,project_id,methodology_id,kind,detector_id,"
        "detector_version,params_json,"
        "target_observation_id,reference_observation_ids,started_at,finished_at,status,"
        "skip_reason,score_raster_id,mask_raster_id,summary_json,supersedes_run_id,"
        "compute_backend) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_uuid, p["id"], m["id"], kind, det.spec.id, det.spec.version,
         json.dumps(params),
         obs_id, json.dumps(base["meta"]["observation_ids"] if base else []),
         started, db.now(), "ok", skip, score_id, mask_id,
         json.dumps(summary_json), payload.get("supersedes"), backend)).lastrowid

    alerting.close_stale_incidents(con, p["id"], m["id"], scene.datetime)
    alerting.retract_unconfirmed(con, p["id"], m["id"], scene.datetime, needed)
    if skip or kind == "backtest":
        return {"run": run_uuid, "alert": None, "reason": skip}

    # stage 11: render and explain
    geometry = geo.vectorise(kept_mask, data["_transform"], data["_crs"])
    confidence, seen = alerting.confirmation(con, p["id"], m["id"], geometry, needed)
    if det.spec.id == "radd_probabilistic":
        confirm_at = float(result.aux.get("confirmation_probability", 0.975))
        accumulated = bool(result.aux.get("previous_probability_used"))
        retained_peak = (float(np.nanmax(result.score[kept_mask]))
                         if kept_mask.any() else float("nan"))
        confidence = ("confirmed" if accumulated and retained_peak >= confirm_at
                      else "provisional")
    elif det.spec.id == "builtup_dual" and \
            result.aux.get("cross_stream_agreement_fraction", 0.0) > 0.6:
        confidence = "confirmed"
    hist = alerting.history(con, p["id"], m["id"])
    sev = alerting.severity(con, p["id"], m["id"], peak)
    text = explain.explain(
        target_date=scene.datetime[:10],
        baseline_desc=(f"the {base['meta']['fitted_from'][:10]} to "
                       f"{base['meta']['fitted_to'][:10]} baseline" if base
                       else "the reference observation"),
        changed_area_m2=changed_area, aoi_area_m2=aoi_area_m2,
        n_components=len(comp_areas),
        largest_component_m2=comp_areas[0] if comp_areas else 0.0,
        direction=result.direction or {}, score=peak, history=hist,
        valid_fraction=vf, caveats=caveats, recipe_name=recipe["display_name"])

    chip_ids = _render_chips(con, p, recipe, adapter, base, data, kept_mask,
                             scene, params)
    llm_text = None
    try:
        llm_text = vlm.describe(con, chip_ids, text)
    except Exception as e:  # noqa: BLE001
        # Optional prose is never on the alert's critical path.
        log.warning("optional VLM explanation unavailable",
                    extra={"extra": {"project": p["id"], "error": str(e)}})
    incident_id = alerting.attach_incident(con, p["id"], m["id"], geometry, peak, changed_area,
                                           scene.datetime, recipe["display_name"])
    alert_uuid = db.new_uuid()
    con.execute(
        "INSERT INTO alert(uuid,project_id,methodology_id,run_id,incident_id,"
        "raised_at,sensed_at,"
        "severity,confidence,score,threshold,changed_area_m2,changed_fraction,"
        "n_components,largest_component_m2,geometry_geojson,direction_json,"
        "explanation_text,explanation_llm,before_chip_id,after_chip_id,overlay_chip_id,"
        "caveats_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (alert_uuid, p["id"], m["id"], run_id, incident_id, db.now(),
         scene.datetime, sev,
         confidence, peak, threshold, changed_area, changed_area / aoi_area_m2,
         len(comp_areas), comp_areas[0] if comp_areas else 0.0,
         json.dumps(geometry), json.dumps(result.direction),
         text, llm_text, chip_ids.get("before"), chip_ids.get("after"),
         chip_ids.get("overlay"), json.dumps(caveats)))

    if alerting.should_notify(con, incident_id, db.now()):
        notify.send(con, alert_uuid)
    log.info("alert raised", extra={"extra": {"alert": alert_uuid, "severity": sev,
                                              "confidence": confidence}})
    return {"run": run_uuid, "alert": alert_uuid, "confidence": confidence}


def _render_chips(con, project, recipe, adapter, base, after_data, mask, scene, params):
    """Before frame is the last usable pre-event observation, same stretch as after."""
    aoi = json.loads(project["aoi_geojson"])
    prev = con.execute(
        "SELECT stac_item_json FROM observation WHERE project_id=? AND status='usable'"
        " AND sensed_at < ? ORDER BY sensed_at DESC LIMIT 1",
        (project["id"], scene.datetime)).fetchone()
    bands = chips.display_bands(recipe)
    stretch = params.get("_stretch") or chips.stretch_from(after_data, bands)
    before = after_data
    if prev:
        try:
            # Loaded through the run's own adapter (local STAC or GEE alike), on the
            # run's analysis grid — a before frame on a different grid would compare
            # nothing. Falls back loud rather than shipping an identical pair quietly.
            ref = _scene_ref_from_item(json.loads(prev["stac_item_json"]),
                                       adapter.name)
            before = adapters.load_scene(
                adapter, ref, aoi, list(bands),
                resolution=recipe.get("resolution_m"),
                target_crs=project["analysis_crs"])
        except Exception as e:  # noqa: BLE001
            log.warning("before chip fell back to the target frame",
                        extra={"extra": {"error": str(e)}})
    pngs = chips.render(before, after_data, mask, stretch,
                        before_date=(json.loads(prev["stac_item_json"])["properties"]
                                     ["datetime"][:10] if prev else "n/a"),
                        after_date=scene.datetime[:10], bands=bands)
    ids = {k: artifacts.put_bytes(con, v, "image/png",
                                  {"kind": f"{k}_chip", "scene": scene.scene_id,
                                   "stretch": stretch})
           for k, v in pngs.items()}
    for kind, artifact_id in ids.items():
        artifacts.link_project(con, artifact_id, project["id"], "chips",
                               f"{scene.scene_id}_{kind}.png")
    return ids


# --- backtest and reanalysis ----------------------------------------------

@handler("backtest")
def backtest(con, job, payload):
    p, m, recipe, det, params, aoi = _ctx(con, payload["project_id"],
                                          payload.get("methodology_id"))
    years = payload.get("years", 3)
    adapter = _adapter(con, p, "backtest", recipe["sensor"])
    end = datetime.now(timezone.utc)
    split = end - timedelta(days=365 * years)
    if m["baseline_artifact_id"] is None:
        fit_baseline(con, job, {"project_id": p["id"], "methodology_id": m["id"],
                                "before": split.isoformat(timespec="seconds")})
        m = db.methodology(con, p["id"], m["id"])
        if m["baseline_artifact_id"] is None:
            return {"fitted": False}
    db.progress(con, job, 0, None, "Searching the catalogue for scenes…")
    scenes = sorted(adapter.search(aoi, split, end), key=lambda x: x.datetime)
    n = 0
    for i, s in enumerate(scenes):
        db.progress(con, job, i, len(scenes),
                    f"Scoring {s.datetime[:10]} ({i + 1} of {len(scenes)})")
        try:
            run_scene(con, job, {"project_id": p["id"], "methodology_id": m["id"],
                                 "scene_id": s.scene_id,
                                 "adapter": adapter.name, "item": s.item,
                                 "kind": "backtest"})
            n += 1
        except Exception as e:  # noqa: BLE001
            log.warning("backtest scene failed",
                        extra={"extra": {"scene": s.scene_id, "error": str(e)}})
    db.progress(con, job, len(scenes), len(scenes), "Done")
    con.execute("UPDATE project SET status='draft', updated_at=? WHERE id=? AND"
                " status='calibrating'", (db.now(), p["id"]))
    return {"scored": n}


@handler("reanalyse")
def reanalyse(con, job, payload):
    """Re-run history with new params. Creates new runs that supersede the old ones;
    nothing is ever overwritten (spec §1.3)."""
    p, m, recipe, det, params, aoi = _ctx(con, payload["project_id"],
                                          payload.get("methodology_id"))
    params = {**params, **(payload.get("params") or {})}
    con.execute("UPDATE project_methodology SET params_json=?, updated_at=?"
                " WHERE id=?", (json.dumps(params), db.now(), m["id"]))
    rows = con.execute(
        "SELECT r.id, r.uuid, o.stac_item_json, o.adapter, o.scene_id FROM run r"
        " JOIN observation o ON o.id=r.target_observation_id WHERE r.project_id=?"
        " AND r.methodology_id=? AND r.status='ok' ORDER BY r.started_at",
        (p["id"], m["id"])).fetchall()
    n = 0
    for r in rows:
        db.progress(con, job, n, len(rows),
                    f"Re-scoring history ({n + 1} of {len(rows)})")
        run_scene(con, job, {"project_id": p["id"], "methodology_id": m["id"],
                             "scene_id": r["scene_id"], "adapter": r["adapter"],
                             "item": json.loads(r["stac_item_json"]),
                             "kind": "reanalysis", "supersedes": r["id"]})
        n += 1
    return {"reanalysed": n}
