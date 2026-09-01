"""FastAPI app: REST API under /api/v1 plus the built frontend as static files."""
from __future__ import annotations
import json, os
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import adapters, artifacts, config, db, gee, geo, scheduler
from .detectors import REGISTRY as DETECTORS
from .log import log
from .recipes import REGISTRY as RECIPES

V1 = "/api/v1"


@asynccontextmanager
async def lifespan(_app):
    db.connect().close()
    scheduler.start()
    log.info("api started", extra={"extra": {"recipes": sorted(RECIPES)}})
    yield


app = FastAPI(title="TerraWatch", version="1.0", lifespan=lifespan)


def con():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


# --- auth (single optional shared password, spec §2) -----------------------

@app.middleware("http")
async def _password_gate(request: Request, call_next):
    pw = config.get("ui.password")
    if pw and request.url.path.startswith(V1) and request.url.path != f"{V1}/health":
        import hmac
        if not hmac.compare_digest(request.headers.get("x-terrawatch-password") or "",
                                   pw):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return await call_next(request)


# --- models ----------------------------------------------------------------

class ProjectIn(BaseModel):
    name: str
    description: str | None = None
    aoi_geojson: dict
    recipe_id: str | None = None
    #: One project may watch the same AOI with several methodologies at once. Either
    #: field works; recipe_ids wins when both are given, and recipe_id stays for
    #: existing callers.
    recipe_ids: list[str] | None = None
    params: dict = Field(default_factory=dict)
    adapter_preference: list[str] | None = None
    s1_relative_orbit: int | None = None
    s1_pass_direction: Literal["ASCENDING", "DESCENDING"] | None = None
    schedule_cron: str | None = None


class ThresholdIn(BaseModel):
    threshold: float
    source: Literal["calibrated", "default"] = "calibrated"
    backtest_artifact_id: int | None = None


class TriageIn(BaseModel):
    user_status: Literal["new", "acknowledged", "true", "false", "unclear"] | None = None
    user_note: str | None = None


_JSON_TEXT_COLUMNS = ("adapter_preference",)

# Soft cap only (spec §13.2 / open question): warn above this, never refuse.
BACKTEST_WARN_KM2 = 500.0


def _row(r) -> dict:
    """JSON-typed text columns are decoded so clients never parse strings themselves."""
    d = dict(r)
    for k, v in list(d.items()):
        if not isinstance(v, str):
            continue
        if k.endswith("_geojson"):
            try:
                d[k] = json.loads(v)
                d[k[:-8]] = d[k]          # geometry_geojson -> geometry
            except json.JSONDecodeError:
                pass
        elif k.endswith("_json"):
            try:
                d[k[:-5]] = json.loads(v)
            except json.JSONDecodeError:
                pass
        elif k in _JSON_TEXT_COLUMNS:
            try:
                d[k] = json.loads(v)
            except json.JSONDecodeError:
                pass
    return d


def _page(c, sql: str, params: tuple, limit: int, cursor: int | None):
    """Keyset pagination on the primary key, descending."""
    base = sql
    sql += (" AND id < ?" if cursor else "") + " ORDER BY id DESC LIMIT ?"
    args = params + ((cursor,) if cursor else ()) + (limit + 1,)
    rows = [_row(r) for r in c.execute(sql, args)]
    nxt = rows[limit]["id"] if len(rows) > limit else None
    # The caller renders "showing N of TOTAL"; without it a capped page is
    # indistinguishable from the whole set, which is how 609 diagnostics silently
    # became 100.
    total = c.execute(f"SELECT COUNT(*) n FROM ({base})", params).fetchone()["n"]
    return {"items": rows[:limit], "next_cursor": nxt, "total": total}


def _project(c, uuid: str):
    r = c.execute("SELECT * FROM project WHERE uuid=? AND status != 'deleted'",
                  (uuid,)).fetchone()
    if not r:
        raise HTTPException(404, "project not found")
    return r


class AoiIn(BaseModel):
    aoi_geojson: dict


@app.post(f"{V1}/aoi/preview")
def aoi_preview(body: AoiIn):
    """Wizard step 1: tell the user before they commit whether this site is even
    monitorable — which S1 orbits cover it and how much usable S2 exists (§13.2)."""
    from collections import Counter
    from datetime import datetime, timedelta, timezone
    try:
        aoi = geo.clean(body.aoi_geojson)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"That area could not be read as a shape: {e}")
    area = geo.area_km2(aoi)
    out = {"area_km2": area, "analysis_crs": geo.utm_crs(aoi),
           "slow_warning": area > 500,
           "suggested_cron": {s: geo.suggest_cron(aoi, s) for s in ("S2", "S1")},
           "s2": None, "s1_orbits": [], "errors": {}}
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=730)
    try:
        a = adapters.first_healthy(["cdse", "earthsearch"])
        scenes = a.search(aoi, start, end, max_cloud=100)
        usable = [s for s in scenes if (s.cloud_cover or 0) < 60]
        out["s2"] = {"adapter": a.name, "total": len(scenes),
                     "usable_estimate": len(usable),
                     "per_year": round(len(usable) / 2, 1)}
    except Exception as e:  # noqa: BLE001
        out["errors"]["s2"] = str(e)
    try:
        a = adapters.get_adapter("planetary")
        s1 = a.search(aoi, end - timedelta(days=90), end,
                      collection=adapters.RTC_COLLECTION, max_cloud=100)
        counts = Counter((s.relative_orbit, s.pass_direction) for s in s1)
        out["s1_orbits"] = [{"relative_orbit": o, "pass_direction": d, "scenes_90d": n}
                            for (o, d), n in sorted(counts.items(),
                                                    key=lambda kv: -kv[1])
                            if o is not None]
    except Exception as e:  # noqa: BLE001
        out["errors"]["s1"] = str(e)
    return out


# --- projects --------------------------------------------------------------

@app.get(f"{V1}/projects")
def list_projects(c=Depends(con), limit: int = 50, cursor: int | None = None):
    return _page(c, "SELECT * FROM project WHERE status != 'deleted'", (), limit, cursor)


@app.post(f"{V1}/projects", status_code=201)
def create_project(p: ProjectIn, c=Depends(con)):
    wanted = p.recipe_ids or ([p.recipe_id] if p.recipe_id else [])
    wanted = list(dict.fromkeys(wanted))          # order-preserving de-dupe
    if not wanted:
        raise HTTPException(400, "a project needs at least one recipe")
    unknown = [r for r in wanted if r not in RECIPES]
    if unknown:
        raise HTTPException(400, f"unknown recipe {unknown[0]}")
    chosen = [RECIPES[r] for r in wanted]
    recipe = chosen[0]
    needs_radar_orbit = any(r["sensor"] == "S1" or r.get("secondary_sensor") == "S1"
                            for r in chosen)
    if needs_radar_orbit and p.s1_relative_orbit is None:
        raise HTTPException(400, "s1_relative_orbit is required for radar recipes: "
                                 "backscatter is geometry-dependent and observations "
                                 "may only be compared within one relative orbit")
    # Radar comes from the RTC collection; optical from the two S2 catalogues.
    sensors = {r["sensor"] for r in chosen}
    preference = p.adapter_preference or (["planetary"] if recipe["sensor"] == "S1"
                                          else ["cdse", "earthsearch"])
    if p.adapter_preference:
        unknown_a = [n for n in preference if n not in adapters.ENDPOINTS]
        if unknown_a:
            raise HTTPException(400, f"unknown adapter {unknown_a[0]}")
        served = {adapters.sensor_of(n) for n in preference}
        missing = sensors - served
        if missing:
            raise HTTPException(
                400, f"adapter_preference {preference} serves {sorted(served)} but "
                     f"this project also needs {sorted(missing)} data")
    elif sensors == {"S1", "S2"}:
        preference = ["cdse", "earthsearch", "planetary"]
    params = {**recipe["defaults"], **p.params}
    try:
        aoi = geo.clean(p.aoi_geojson)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"That area could not be read as a shape: {e}")
    now = db.now()
    uuid = db.new_uuid()
    cron = p.schedule_cron or geo.suggest_cron(aoi, recipe["sensor"])
    cur = c.execute(
        "INSERT INTO project(uuid,name,description,aoi_geojson,aoi_area_km2,"
        "analysis_crs,recipe_id,recipe_version,params_json,adapter_preference,"
        "s1_relative_orbit,s1_pass_direction,schedule_cron,status,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'draft',?,?)",
        (uuid, p.name, p.description, json.dumps(aoi),
         geo.area_km2(aoi), geo.utm_crs(aoi), recipe["id"],
         recipe["version"], json.dumps(params), json.dumps(preference),
         p.s1_relative_orbit, p.s1_pass_direction, cron, now, now))
    for r in chosen:
        c.execute(
            "INSERT INTO project_methodology(project_id,recipe_id,recipe_version,"
            "params_json,status,created_at,updated_at) VALUES (?,?,?,?,'draft',?,?)",
            (cur.lastrowid, r["id"], r["version"],
             json.dumps({**r["defaults"], **p.params}), now, now))
    return get_project_row(c, cur.lastrowid)


def get_project_row(c, project_id: int) -> dict:
    d = _row(c.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone())
    ms = [_row(m) for m in db.ensure_methodologies(c, project_id)]
    d["methodologies"] = ms
    # `recipe_id`, `params` and the baseline fields describe the primary methodology,
    # so single-methodology clients keep working unchanged. Anything that needs the
    # others reads `methodologies`.
    d["recipe_id"] = ms[0]["recipe_id"]
    d["recipe_version"] = ms[0]["recipe_version"]
    d["params"] = ms[0]["params"]
    for k in ("baseline_start", "baseline_end", "baseline_artifact_id"):
        d[k] = ms[0][k]
    return d


def _methodology(c, project, methodology_id: int | None):
    try:
        return db.methodology(c, project["id"], methodology_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get(f"{V1}/projects/{{uuid}}/methodologies")
def list_methodologies(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    return {"items": [_row(m) for m in db.ensure_methodologies(c, p["id"])]}


@app.post(f"{V1}/projects/{{uuid}}/methodologies", status_code=201)
def add_methodology(uuid: str, body: dict, c=Depends(con)):
    """Add a second question over the same AOI. It starts without a baseline, so it
    calibrates on its own before it can score anything."""
    p = _project(c, uuid)
    recipe = RECIPES.get(body.get("recipe_id"))
    if not recipe:
        raise HTTPException(400, f"unknown recipe {body.get('recipe_id')}")
    if any(m["recipe_id"] == recipe["id"]
           for m in db.ensure_methodologies(c, p["id"])):
        raise HTTPException(409, f"{recipe['id']} is already on this project")
    if (recipe["sensor"] == "S1" or recipe.get("secondary_sensor") == "S1") \
            and p["s1_relative_orbit"] is None:
        raise HTTPException(400, "this recipe needs radar, and the project has no "
                                 "s1_relative_orbit; radar may only be compared "
                                 "within one relative orbit")
    served = {adapters.sensor_of(n)
              for n in json.loads(p["adapter_preference"])
              if n in adapters.ENDPOINTS}
    if recipe["sensor"] not in served:
        raise HTTPException(400, f"this project's adapter_preference serves "
                                 f"{sorted(served)}, not {recipe['sensor']}")
    t = db.now()
    mid = c.execute(
        "INSERT INTO project_methodology(project_id,recipe_id,recipe_version,"
        "params_json,status,created_at,updated_at) VALUES (?,?,?,?,'draft',?,?)",
        (p["id"], recipe["id"], recipe["version"],
         json.dumps({**recipe["defaults"], **(body.get("params") or {})}), t, t)
    ).lastrowid
    return _row(c.execute("SELECT * FROM project_methodology WHERE id=?",
                          (mid,)).fetchone())


@app.delete(f"{V1}/projects/{{uuid}}/methodologies/{{methodology_id}}",
            status_code=204)
def remove_methodology(uuid: str, methodology_id: int, c=Depends(con)):
    p = _project(c, uuid)
    rows = db.ensure_methodologies(c, p["id"])
    if len(rows) <= 1:
        raise HTTPException(400, "a project must keep at least one methodology")
    if not any(m["id"] == methodology_id for m in rows):
        raise HTTPException(404, "methodology not found on this project")
    scored = c.execute("SELECT COUNT(*) n FROM run WHERE methodology_id=?",
                       (methodology_id,)).fetchone()["n"]
    if scored:
        raise HTTPException(
            409, f"{scored} runs were scored with this methodology; removing it would "
                 "orphan them. Pause the project instead.")
    c.execute("DELETE FROM project_methodology WHERE id=?", (methodology_id,))
    return Response(status_code=204)


@app.get(f"{V1}/projects/{{uuid}}")
def get_project(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    d = get_project_row(c, p["id"])
    d["next_poll"] = scheduler.next_poll(uuid)
    return d


@app.patch(f"{V1}/projects/{{uuid}}")
def patch_project(uuid: str, body: dict, c=Depends(con)):
    p = _project(c, uuid)
    allowed = {"name", "description", "schedule_cron", "adapter_preference"}
    sets, args = [], []
    for k, v in body.items():
        if k not in allowed:
            raise HTTPException(400, f"{k} is not patchable; use /reanalyse to change "
                                     "parameters so history stays interpretable")
        sets.append(f"{k}=?")
        args.append(json.dumps(v) if isinstance(v, (dict, list)) else v)
    if sets:
        c.execute(f"UPDATE project SET {','.join(sets)}, updated_at=? WHERE id=?",
                  (*args, db.now(), p["id"]))
    if "schedule_cron" in body and p["status"] == "active":
        scheduler.schedule(p["id"], uuid, body["schedule_cron"])
    return get_project(uuid, c)


@app.post(f"{V1}/projects/{{uuid}}/gee")
def set_gee(uuid: str, body: dict, c=Depends(con)):
    """Per-project GEE toggle, off by default. Not a `/reanalyse` parameter: it picks
    which machine fits the baseline, and every run records which one served it."""
    p = _project(c, uuid)
    if body.get("enabled") and not gee.configured():
        raise HTTPException(400, "GEE is disabled in config.yaml or has no service "
                                 "account and Cloud project configured")
    params = {**json.loads(p["params_json"]), "gee_enabled": bool(body.get("enabled"))}
    c.execute("UPDATE project SET params_json=?, updated_at=? WHERE id=?",
              (json.dumps(params), db.now(), p["id"]))
    return {"gee_enabled": params["gee_enabled"], "quota": gee.quota(c)}


@app.post(f"{V1}/projects/{{uuid}}/activate")
def activate(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    c.execute("UPDATE project SET status='active', updated_at=? WHERE id=?",
              (db.now(), p["id"]))
    scheduler.schedule(p["id"], uuid, p["schedule_cron"])
    return get_project(uuid, c)


@app.post(f"{V1}/projects/{{uuid}}/pause")
def pause(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    c.execute("UPDATE project SET status='paused', updated_at=? WHERE id=?",
              (db.now(), p["id"]))
    scheduler.unschedule(uuid)
    return get_project(uuid, c)


@app.delete(f"{V1}/projects/{{uuid}}", status_code=204)
def soft_delete(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    c.execute("UPDATE project SET status='deleted', updated_at=? WHERE id=?",
              (db.now(), p["id"]))
    scheduler.unschedule(uuid)


@app.post(f"{V1}/projects/{{uuid}}/run-now", status_code=202)
def run_now(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    return {"job_id": db.enqueue(c, "poll", {"project_id": p["id"]}, p["id"])}


@app.get(f"{V1}/projects/{{uuid}}/jobs")
def project_jobs(uuid: str, c=Depends(con), limit: int = 5):
    """Recent background work for this project, newest first, each with how far it
    has got. The UI polls this to draw a real progress bar instead of a spinner."""
    p = _project(c, uuid)
    return {"items": [_row(r) for r in c.execute(
        "SELECT * FROM job WHERE project_id=? ORDER BY id DESC LIMIT ?",
        (p["id"], limit))]}


@app.post(f"{V1}/projects/{{uuid}}/backtest", status_code=202)
def start_backtest(uuid: str, years: int = 3, methodology: int | None = None,
                   c=Depends(con)):
    """No hard cap on AOI size: a large backtest is slow, not wrong, and refusing it
    would be the app deciding what the user is allowed to look at. It warns with an
    estimate instead, and the warning is recorded as a diagnostic so the wait is
    explained later rather than looking like a hang."""
    p = _project(c, uuid)
    warning = None
    if p["aoi_area_km2"] > BACKTEST_WARN_KM2:
        hours = p["aoi_area_km2"] * years / 900.0
        warning = (f"This AOI is {p['aoi_area_km2']:.0f} km². A {years}-year backtest "
                   f"over it will take roughly {hours:.1f} hours and will hold a "
                   "worker for that whole time. It will still run.")
        db.diagnostic(c, "SLOW_BACKTEST", "info", warning, project_id=p["id"],
                      detail={"area_km2": p["aoi_area_km2"], "years": years})
    c.execute("UPDATE project SET status='calibrating', updated_at=? WHERE id=?",
              (db.now(), p["id"]))
    m = _methodology(c, p, methodology)
    return {"job_id": db.enqueue(c, "backtest", {"project_id": p["id"],
                                                 "methodology_id": m["id"],
                                                 "years": years}, p["id"]),
            "warning": warning}


@app.get(f"{V1}/projects/{{uuid}}/backtest")
def backtest_status(uuid: str, methodology: int | None = None, c=Depends(con)):
    """Calibration status. Scores from different methodologies are different
    quantities on different scales, so `methodology` scopes the distribution; without
    it a single-methodology project behaves exactly as before."""
    p = _project(c, uuid)
    job = c.execute("SELECT * FROM job WHERE kind='backtest' AND project_id=?"
                    " ORDER BY id DESC LIMIT 1", (p["id"],)).fetchone()
    sql = "SELECT * FROM run WHERE project_id=? AND kind='backtest'"
    args: list[Any] = [p["id"]]
    if methodology is not None:
        sql += " AND methodology_id=?"
        args.append(methodology)
    runs = [_row(r) for r in c.execute(sql + " ORDER BY started_at", args)]
    scores = [r["summary"].get("score_headline", r["summary"].get("score_p99"))
              for r in runs
              if r.get("summary", {}).get("score_headline",
                                          r.get("summary", {}).get("score_p99"))
              is not None]
    return {"job": _row(job) if job else None, "n_runs": len(runs),
            "scores": scores, "runs": runs}


@app.post(f"{V1}/projects/{{uuid}}/threshold")
def set_threshold(uuid: str, body: ThresholdIn, methodology: int | None = None,
                  c=Depends(con)):
    p = _project(c, uuid)
    m = _methodology(c, p, methodology)
    params = json.loads(m["params_json"])
    params["threshold"] = body.threshold
    params["threshold_provenance"] = {"source": body.source, "at": db.now(),
                                      "backtest_artifact_id": body.backtest_artifact_id,
                                      "default_kind": (
                                          RECIPES[m["recipe_id"]]
                                          .get("default_provenance", {})
                                          .get("threshold")
                                          if body.source == "default" else None)}
    c.execute("UPDATE project_methodology SET params_json=?, updated_at=? WHERE id=?",
              (json.dumps(params), db.now(), m["id"]))
    return get_project(uuid, c)


@app.post(f"{V1}/projects/{{uuid}}/reanalyse", status_code=202)
def reanalyse(uuid: str, body: dict | None = None,
              methodology: int | None = None, c=Depends(con)):
    p = _project(c, uuid)
    m = _methodology(c, p, methodology)
    return {"job_id": db.enqueue(c, "reanalyse",
                                 {"project_id": p["id"], "methodology_id": m["id"],
                                  "params": (body or {})}, p["id"])}


@app.get(f"{V1}/projects/{{uuid}}/observations")
def observations(uuid: str, c=Depends(con), limit: int = 200, cursor: int | None = None):
    p = _project(c, uuid)
    return _page(c, "SELECT * FROM observation WHERE project_id=?", (p["id"],),
                 limit, cursor)


@app.get(f"{V1}/projects/{{uuid}}/runs")
def runs(uuid: str, c=Depends(con), limit: int = 200, cursor: int | None = None):
    p = _project(c, uuid)
    return _page(c, "SELECT * FROM run WHERE project_id=?", (p["id"],), limit, cursor)


@app.get(f"{V1}/runs/{{uuid}}")
def run_detail(uuid: str, c=Depends(con)):
    r = c.execute("SELECT * FROM run WHERE uuid=?", (uuid,)).fetchone()
    if not r:
        raise HTTPException(404, "run not found")
    d = _row(r)
    # A run detail page with no way back to its monitor is a dead end.
    owner = c.execute("SELECT uuid, name FROM project WHERE id=?",
                      (r["project_id"],)).fetchone()
    if owner:
        d["project_uuid"], d["project_name"] = owner["uuid"], owner["name"]
    # The graph's central edge is two-way: a run knows the alert it raised.
    raised = c.execute(
        "SELECT uuid, severity, confidence, sensed_at, raised_at, user_status,"
        " score, threshold FROM alert WHERE run_id=?"
        " ORDER BY raised_at DESC LIMIT 1", (r["id"],)).fetchone()
    d["alert"] = _row(raised) if raised else None
    if r["supersedes_run_id"] is not None:
        prev = c.execute("SELECT uuid FROM run WHERE id=?",
                         (r["supersedes_run_id"],)).fetchone()
        d["supersedes_uuid"] = prev["uuid"] if prev else None
    d["observations"] = [_row(o) for o in c.execute(
        "SELECT * FROM observation WHERE id IN (SELECT value FROM json_each(?))"
        " OR id=?", (r["reference_observation_ids"] or "[]",
                     r["target_observation_id"]))]
    return d


@app.get(f"{V1}/projects/{{uuid}}/alerts")
def alerts(uuid: str, c=Depends(con), limit: int = 100, cursor: int | None = None):
    p = _project(c, uuid)
    return _page(c, "SELECT * FROM alert WHERE project_id=?", (p["id"],), limit, cursor)


@app.get(f"{V1}/alerts/{{uuid}}")
def alert_detail(uuid: str, c=Depends(con)):
    r = c.execute("SELECT * FROM alert WHERE uuid=?", (uuid,)).fetchone()
    if not r:
        raise HTTPException(404, "alert not found")
    d = _row(r)
    d["run"] = _row(c.execute("SELECT * FROM run WHERE id=?", (r["run_id"],)).fetchone())
    # the recipe that produced this alert, not whatever the project lists first
    meth = db.methodology(c, r["project_id"], r["methodology_id"])
    d["methodology"] = _row(meth)
    d["recipe"] = RECIPES.get(meth["recipe_id"])
    return d


@app.patch(f"{V1}/alerts/{{uuid}}")
def triage(uuid: str, body: TriageIn, c=Depends(con)):
    sets = {k: v for k, v in body.model_dump().items() if v is not None}
    if sets:
        c.execute(f"UPDATE alert SET {','.join(f'{k}=?' for k in sets)} WHERE uuid=?",
                  (*sets.values(), uuid))
    return alert_detail(uuid, c)


@app.get(f"{V1}/projects/{{uuid}}/incidents")
def incidents(uuid: str, c=Depends(con), limit: int = 100, cursor: int | None = None):
    p = _project(c, uuid)
    return _page(c, "SELECT * FROM incident WHERE project_id=?", (p["id"],),
                 limit, cursor)


# --- feed, diagnostics, registries, health ---------------------------------

@app.get(f"{V1}/feed")
def feed(c=Depends(con), limit: int = 50, cursor: str | None = None,
         severity: str | None = None, user_status: str | None = None,
         project: str | None = None, date_from: str | None = None,
         date_to: str | None = None):
    """Unified reverse-chronological feed of alerts + error diagnostics (§13.1).

    `cursor` is the sort timestamp of the oldest item on the previous page; `date_from`
    and `date_to` (YYYY-MM-DD, inclusive) narrow the window. """
    a = ("SELECT a.*, p.name AS project_name, p.uuid AS project_uuid FROM alert a"
         " JOIN project p ON p.id=a.project_id WHERE 1=1")
    # LEFT JOIN, not JOIN: a diagnostic raised by the scheduler or a source adapter
    # has no project, and must still reach the feed. Only rows belonging to a project
    # the user deleted are dropped.
    d = ("SELECT dg.*, p.name AS project_name, p.uuid AS project_uuid"
         " FROM diagnostic dg LEFT JOIN project p ON p.id=dg.project_id"
         " WHERE dg.severity='error' AND dg.acknowledged=0"
         " AND dg.resolved_at IS NULL"
         " AND (p.id IS NULL OR p.status != 'deleted')")
    args: list[Any] = []
    dargs: list[Any] = []
    if severity:
        a += " AND a.severity=?"; args.append(severity)
    if user_status:
        a += " AND a.user_status=?"; args.append(user_status)
    if project:
        a += " AND p.uuid=?"; args.append(project)
        d += " AND p.uuid=?"; dargs.append(project)
    if cursor:
        a += " AND a.sensed_at < ?"; args.append(cursor)
        d += " AND dg.occurred_at < ?"; dargs.append(cursor)
    if date_from:
        a += " AND a.sensed_at >= ?"; args.append(date_from)
        d += " AND dg.occurred_at >= ?"; dargs.append(date_from)
    if date_to:
        a += " AND a.sensed_at <= ?"; args.append(f"{date_to}T23:59:59")
        d += " AND dg.occurred_at <= ?"; dargs.append(f"{date_to}T23:59:59")
    items = [dict(_row(r), _type="alert") for r in
             c.execute(a + " ORDER BY a.sensed_at DESC LIMIT ?", (*args, limit))]
    diags = [dict(_row(r), _type="diagnostic") for r in c.execute(
        d + " ORDER BY dg.occurred_at DESC LIMIT ?", (*dargs, limit))]
    merged = sorted(items + diags,
                    key=lambda x: x.get("sensed_at") or x.get("occurred_at"),
                    reverse=True)
    # Keyset by the merge sort key: if either source still had rows at the cut, the
    # oldest returned item is the next page's cursor.
    next_cursor = None
    if len(merged) > limit:
        last = merged[limit - 1]
        next_cursor = last.get("sensed_at") or last.get("occurred_at")
    return {"items": merged[:limit], "next_cursor": next_cursor}


@app.get(f"{V1}/diagnostics")
def diagnostics(c=Depends(con), limit: int = 100, cursor: int | None = None,
                severity: str | None = None, code: str | None = None,
                project: str | None = None, acknowledged: bool | None = None):
    sql, args = "SELECT * FROM diagnostic WHERE 1=1", []
    if severity:
        sql += " AND severity=?"; args.append(severity)
    if code:
        sql += " AND code=?"; args.append(code)
    if acknowledged is not None:
        sql += " AND acknowledged=?"; args.append(int(acknowledged))
    if project:
        sql += " AND project_id=(SELECT id FROM project WHERE uuid=?)"
        args.append(project)
    return _page(c, sql, tuple(args), limit, cursor)


@app.get(f"{V1}/diagnostics/summary")
def diagnostics_summary(c=Depends(con), project: str | None = None):
    """One row per (code, severity): a repeated fault is one problem, not 609."""
    sql = ("SELECT code, severity, COUNT(*) n, MAX(occurred_at) last_at,"
           " MIN(occurred_at) first_at, MAX(id) latest_id"
           " FROM diagnostic WHERE acknowledged=0 AND resolved_at IS NULL")
    args: list[Any] = []
    if project:
        sql += " AND project_id=(SELECT id FROM project WHERE uuid=?)"
        args.append(project)
    sql += " GROUP BY code, severity ORDER BY last_at DESC"
    return {"items": [_row(r) for r in c.execute(sql, tuple(args))]}


@app.post(f"{V1}/diagnostics/acknowledge")
def acknowledge_many(body: dict, c=Depends(con)):
    """Acknowledge every open diagnostic sharing a code. Clearing 609 identical
    warnings one button at a time is not a workflow anyone completes."""
    sql = ("UPDATE diagnostic SET acknowledged=1 WHERE acknowledged=0"
           " AND resolved_at IS NULL AND code=?")
    args: list[Any] = [body["code"]]
    if body.get("project"):
        sql += " AND project_id=(SELECT id FROM project WHERE uuid=?)"
        args.append(body["project"])
    n = c.execute(sql, tuple(args)).rowcount
    return {"ok": True, "acknowledged": n}


@app.post(f"{V1}/diagnostics/{{diag_id}}/acknowledge")
def acknowledge(diag_id: int, c=Depends(con)):
    c.execute("UPDATE diagnostic SET acknowledged=1 WHERE id=?", (diag_id,))
    return {"ok": True}


@app.get(f"{V1}/recipes")
def list_recipes():
    return list(RECIPES.values())


@app.get(f"{V1}/detectors")
def list_detectors():
    from dataclasses import asdict
    return [asdict(d.spec) for d in DETECTORS.values()]


@app.get(f"{V1}/health")
def health(c=Depends(con), check_adapters: bool = False):
    queue = {r["status"]: r["n"] for r in c.execute(
        "SELECT status, COUNT(*) n FROM job GROUP BY status")}
    ad = ([adapters.get_adapter(n).health()
           for n in ("cdse", "earthsearch")
           if config.get(f"adapters.{n}.enabled")] if check_adapters else None)
    stalled = c.execute(
        "SELECT COUNT(*) n FROM job WHERE status='queued'"
        # available_at, not created_at: a job deliberately deferred into the future
        # is waiting correctly, not stalled. Timestamps are stored as UTC ISO-8601
        # ("...T12:29:35+00:00"), so the comparison string has to match that shape —
        # SQLite's own datetime() uses a space separator and would never compare equal.
        " AND available_at < strftime('%Y-%m-%dT%H:%M:%S', 'now', '-5 minutes')"
    ).fetchone()["n"]
    return {"ok": True, "queue": queue,
            # Jobs pile up silently when no worker is running; say so rather than
            # letting the UI show "Queued…" forever.
            "worker": {"stalled_jobs": stalled,
                       "ok": stalled == 0,
                       "hint": "No worker has picked up queued jobs for over 5 minutes."
                               " Start one with: python -m terrawatch.worker"
                               if stalled else None},
            "adapters": ad,
            "credentials": config.credential_status(),
            "gee": {"configured": gee.configured(), **gee.quota(c)},
            "storage": {**artifacts.usage(),
                        "gc_enabled": bool(config.get("storage.gc_enabled", False))},
            "projects": c.execute(
                "SELECT COUNT(*) n FROM project WHERE status='active'").fetchone()["n"]}


@app.get(f"{V1}/projects/{{uuid}}/health")
def project_health(uuid: str, c=Depends(con)):
    p = _project(c, uuid)
    last = c.execute("SELECT MAX(sensed_at) t FROM observation WHERE project_id=?"
                     " AND status='usable'", (p["id"],)).fetchone()["t"]
    counts = {r["severity"]: r["n"] for r in c.execute(
        "SELECT severity, COUNT(*) n FROM diagnostic WHERE project_id=?"
        " AND resolved_at IS NULL AND acknowledged=0 GROUP BY severity", (p["id"],))}
    days = None
    if last:
        from datetime import datetime, timezone
        days = (datetime.now(timezone.utc)
                - datetime.fromisoformat(last)).days
    return {"last_usable_observation": last, "days_since": days,
            "diagnostics": counts, "next_poll": scheduler.next_poll(uuid),
            "status": p["status"], "adapter_preference":
                json.loads(p["adapter_preference"])}


# --- export, tiles, maintenance -------------------------------------------

@app.get(f"{V1}/projects/{{uuid}}/export")
def export_project(uuid: str, c=Depends(con)):
    from .export import build
    _project(c, uuid)
    return FileResponse(build(c, project_uuid=uuid), filename=f"terrawatch_{uuid}.zip")


@app.get(f"{V1}/runs/{{uuid}}/export")
def export_run(uuid: str, c=Depends(con)):
    from .export import build
    return FileResponse(build(c, run_uuid=uuid), filename=f"terrawatch_run_{uuid}.zip")


@app.get(f"{V1}/alerts/{{uuid}}/export")
def export_alert(uuid: str, c=Depends(con)):
    from .export import build
    return FileResponse(build(c, alert_uuid=uuid),
                        filename=f"terrawatch_alert_{uuid}.zip")


@app.get(f"{V1}/artifacts/{{artifact_id}}/raw")
def artifact_raw(artifact_id: int, c=Depends(con)):
    r = c.execute("SELECT media_type FROM artifact WHERE id=?",
                  (artifact_id,)).fetchone()
    p = artifacts.local_path(c, artifact_id)
    if not r or not p or not os.path.exists(p):
        raise HTTPException(404, "artifact not found")
    return FileResponse(p, media_type=r["media_type"])


@app.get(f"{V1}/artifacts/{{artifact_id}}/overlay")
def artifact_overlay(artifact_id: int, c=Depends(con)):
    import rasterio
    from rasterio.warp import transform_bounds
    p = artifacts.local_path(c, artifact_id)
    if not p or not os.path.exists(p):
        raise HTTPException(404, "artifact not found")
    with rasterio.open(p) as src:
        w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
    return {"url": f"{V1}/artifacts/{artifact_id}/overlay.png",
            "coordinates": [[w, n], [e, n], [e, s], [w, s]]}


@app.get(f"{V1}/artifacts/{{artifact_id}}/overlay.png")
def artifact_overlay_png(artifact_id: int, c=Depends(con)):
    import io
    import numpy as np
    import rasterio
    from PIL import Image
    p = artifacts.local_path(c, artifact_id)
    if not p or not os.path.exists(p):
        raise HTTPException(404, "artifact not found")
    with rasterio.open(p) as src:
        valid = src.read(1, out_shape=(min(src.height, 1024), min(src.width, 1024)),
                         resampling=rasterio.enums.Resampling.nearest) >= 0.5
    rgba = np.zeros((*valid.shape, 4), "uint8")
    rgba[~valid] = (217, 164, 65, 150)
    out = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(out, format="PNG", optimize=True)
    return Response(out.getvalue(), media_type="image/png")


@app.post(f"{V1}/maintenance/gc")
def garbage_collect(c=Depends(con), dry_run: bool = True):
    if not config.get("storage.gc_enabled", False) and not dry_run:
        raise HTTPException(400, "storage.gc_enabled is false; GC runs only when the "
                                 "user explicitly enables and invokes it")
    return artifacts.gc(c, dry_run=dry_run)


# --- static frontend -------------------------------------------------------

_DIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "frontend", "dist")
if os.path.isdir(_DIST):
    from starlette.exceptions import HTTPException as _StarletteHTTPException

    @app.exception_handler(_StarletteHTTPException)
    async def _spa_fallback(request: Request, exc: _StarletteHTTPException):
        # A client-side route (/runs/<uuid>, /alerts/<uuid>, or any page refresh)
        # is a real file only for /assets; everything else must return index.html so
        # the SPA router can take over. API 404s stay JSON.
        if (exc.status_code == 404 and request.method in ("GET", "HEAD")
                and not request.url.path.startswith(V1)):
            return FileResponse(os.path.join(_DIST, "index.html"))
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
