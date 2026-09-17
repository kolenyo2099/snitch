"""Optional Google Earth Engine backend (spec §11.2).

Off by default and never on the reference path: it accelerates baseline fitting,
backtests, and Cloud Score+ retrieval only. It presents the same `search`/`load`
surface as `adapters.StacAdapter` and returns the same xarray objects (via `xee`),
so nothing downstream branches on the backend. `ee` and `xee` are imported lazily,
so deleting the credentials — or never installing the packages — leaves every
project working locally.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone

from . import db
from .adapters import SceneRef
from .config import get
from .log import log

S2 = "COPERNICUS/S2_SR_HARMONIZED"
CS_PLUS = "GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED"
# GEE band ids for the names the local path uses. `cs` comes from the joined
# Cloud Score+ collection, not from the S2 image itself.
BANDS = {"B02": "B2", "B03": "B3", "B04": "B4", "B08": "B8", "B8A": "B8A",
         "B11": "B11", "B12": "B12", "SCL": "SCL", "cs": "cs",
         "cloud_probability": "MSK_CLDPRB"}
SCALED = {"B2", "B3", "B4", "B8", "B8A", "B11", "B12"}   # 1e-4 reflectance
ALGORITHM_VERSION = "gee-1.0"


class QuotaExceeded(RuntimeError):
    """Pre-flight estimate exceeds the remaining EECU budget. Never submitted."""


# --- configuration and toggle ----------------------------------------------

def configured() -> bool:
    """Enabled globally and pointed at a service account and a Cloud project."""
    if not get("adapters.gee.enabled"):
        return False
    sa = os.environ.get("GEE_SERVICE_ACCOUNT_JSON") or get("adapters.gee.service_account_json")
    return bool(sa and get("adapters.gee.project_id"))


def project_enabled(project) -> bool:
    """Per-project toggle, off unless the project explicitly opted in."""
    try:
        params = json.loads(project["params_json"] or "{}")
    except (TypeError, ValueError):
        return False
    return bool(params.get("gee_enabled")) and configured()


def _init():
    import ee
    sa = os.environ.get("GEE_SERVICE_ACCOUNT_JSON") or get("adapters.gee.service_account_json")
    info = json.loads(sa) if sa.lstrip().startswith("{") else json.load(open(sa))
    creds = ee.ServiceAccountCredentials(info["client_email"], key_data=json.dumps(info))
    ee.Initialize(creds, project=get("adapters.gee.project_id"))
    return ee


# --- EECU accounting -------------------------------------------------------

def estimate_eecu(area_km2: float, n_scenes: int, n_bands: int) -> float:
    """Pre-flight cost estimate in EECU-hours.

    ponytail: a linear pixel-count model calibrated against observed task cost, not a
    simulation of the scheduler. Recalibrate `eecu_per_megapixel` from real task
    reports if the estimates drift.
    """
    megapixels = max(area_km2, 0.0) / 0.0001 * max(n_scenes, 0) * max(n_bands, 1) / 1e6
    return round(megapixels * float(get("adapters.gee.eecu_per_megapixel", 0.0008)), 4)


def used(con, since: str) -> float:
    row = con.execute("SELECT COALESCE(SUM(eecu),0) s FROM gee_usage WHERE occurred_at>=?",
                      (since,)).fetchone()
    return float(row["s"])


def _month_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def _day_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def quota(con) -> dict:
    monthly = float(get("adapters.gee.monthly_eecu_budget", 100))
    daily = float(get("adapters.gee.daily_eecu_cap", 10))
    month_used, day_used = used(con, _month_start()), used(con, _day_start())
    return {"monthly_budget": monthly, "monthly_used": round(month_used, 4),
            "monthly_remaining": round(monthly - month_used, 4),
            "daily_cap": daily, "daily_used": round(day_used, 4),
            "daily_remaining": round(daily - day_used, 4)}


def check_budget(con, estimate: float, project_id: int | None = None) -> dict:
    """Refuse before submitting anything that would overrun (spec §11.2)."""
    q = quota(con)
    for scope, remaining, budget in (("monthly", q["monthly_remaining"], q["monthly_budget"]),
                                     ("daily", q["daily_remaining"], q["daily_cap"])):
        if estimate > remaining:
            db.diagnostic(con, "QUOTA_EXHAUSTED", "error",
                          f"The estimated {estimate} EECU-hours for this task exceed the "
                          f"{remaining} remaining in the {scope} budget of {budget}; it was "
                          "not submitted and the local backend will serve this project.",
                          project_id=project_id, detail={"estimate": estimate, **q})
            raise QuotaExceeded(f"{scope} budget: {estimate} > {remaining}")
    if q["monthly_remaining"] - estimate < 0.2 * q["monthly_budget"]:
        db.diagnostic(con, "QUOTA_LOW", "warning",
                      f"This task leaves {round(q['monthly_remaining'] - estimate, 4)} of "
                      f"{q['monthly_budget']} monthly EECU-hours.",
                      project_id=project_id, detail={"estimate": estimate, **q})
    return q


def record(con, project_id: int | None, kind: str, eecu: float, detail: dict | None = None):
    con.execute("INSERT INTO gee_usage(project_id, kind, eecu, detail_json, occurred_at)"
                " VALUES (?,?,?,?,?)",
                (project_id, kind, float(eecu), json.dumps(detail or {}), db.now()))


# --- the adapter -----------------------------------------------------------

class GeeAdapter:
    """Same surface as `adapters.StacAdapter`; results come back as xarray via xee."""

    name = "gee"
    collection = S2
    supported_collections = [S2]

    def __init__(self, con=None, project_id: int | None = None):
        self.con = con
        self.project_id = project_id

    # -- discovery
    def search(self, aoi: dict, start: datetime, end: datetime,
               collection: str | None = None, limit: int = 500,
               max_cloud: float = 90.0) -> list[SceneRef]:
        ee = _init()
        coll = (ee.ImageCollection(collection or S2)
                .filterBounds(ee.Geometry(aoi))
                .filterDate(str(start.date()), str(end.date()))
                .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", max_cloud))
                .limit(limit))
        return [self._ref(f) for f in coll.getInfo().get("features", [])]

    def _ref(self, feature: dict) -> SceneRef:
        p = feature.get("properties", {})
        stamp = datetime.fromtimestamp(p["system:time_start"] / 1000, timezone.utc)
        item = {"id": feature["id"], "collection": S2, "geometry": feature.get("geometry"),
                "properties": {"datetime": stamp.isoformat(timespec="seconds").replace(
                    "+00:00", "Z"),
                    "platform": p.get("SPACECRAFT_NAME", "sentinel-2"),
                    "eo:cloud_cover": p.get("CLOUDY_PIXEL_PERCENTAGE"),
                    "sat:relative_orbit": p.get("SENSING_ORBIT_NUMBER"),
                    "s2:mean_solar_azimuth": p.get("MEAN_SOLAR_AZIMUTH_ANGLE"),
                    "s2:mean_solar_zenith": p.get("MEAN_SOLAR_ZENITH_ANGLE")},
                "assets": {}, "links": [{"href": f"ee://{feature['id']}"}]}
        return SceneRef(scene_id=feature["id"].split("/")[-1], platform=item["properties"]["platform"],
                        datetime=item["properties"]["datetime"], collection=S2, adapter=self.name,
                        relative_orbit=p.get("SENSING_ORBIT_NUMBER"),
                        pass_direction=(p.get("SENSING_ORBIT_DIRECTION") or "").upper() or None,
                        source_uri=f"ee://{feature['id']}", item=item)

    # -- loading
    def load(self, scenes, aoi: dict, bands: list[str], resolution: float | None = None,
             target_crs: str | None = None, buffer_px: int = 0):
        """Windowed read of the requested scenes as one `time, y, x` Dataset.

        Cloud Score+ arrives by joining its published collection, which is the only
        way to get `cs` without recomputing it locally.
        """
        ee = _init()
        scenes = [scenes] if isinstance(scenes, SceneRef) else list(scenes)
        if not scenes:
            raise ValueError("load requires at least one scene")
        res = float(resolution or 10.0)
        crs = target_crs or "EPSG:4326"
        geom = ee.Geometry(aoi)
        if buffer_px:
            geom = geom.buffer(buffer_px * res)
        ids = [s.item["id"] for s in scenes]
        coll = ee.ImageCollection(S2).filter(ee.Filter.inList("system:index",
                                                              [i.split("/")[-1] for i in ids]))
        if "cs" in bands:
            coll = ee.ImageCollection(ee.Join.saveFirst("cs_img").apply(
                coll, ee.ImageCollection(CS_PLUS),
                ee.Filter.equals(leftField="system:index", rightField="system:index")))
            coll = coll.map(lambda img: img.addBands(
                ee.Image(img.get("cs_img")).select(["cs"])))
        coll = coll.select([BANDS[b] for b in bands], list(bands))

        estimate = estimate_eecu(_area_km2(aoi), len(scenes), len(bands))
        if self.con is not None:
            check_budget(self.con, estimate, self.project_id)
        threshold = float(get("adapters.gee.batch_export_eecu", 1.0))
        started = time.time()
        if estimate >= threshold:
            ds = _batch_export(ee, coll, geom, crs, res, estimate)
        else:
            import xee  # noqa: F401  -- registers the "ee" xarray engine
            import xarray as xr
            ds = xr.open_dataset(coll, engine="ee", geometry=geom, crs=crs, scale=res)
            ds = ds.load()
        ds = _to_local_convention(ds, crs, res, scenes)
        ds.attrs["gee"] = {"algorithm_version": ALGORITHM_VERSION, "asset_ids": ids,
                           "collection": S2, "bands": list(bands),
                           "cloud_score_plus": CS_PLUS if "cs" in bands else None,
                           "dates": [s.datetime for s in scenes], "crs": crs,
                           "scale_m": res, "eecu_estimate": estimate,
                           "mode": "batch_export" if estimate >= threshold else "xee"}
        if self.con is not None:
            record(self.con, self.project_id, "load", estimate,
                   {"scenes": len(scenes), "seconds": round(time.time() - started, 1)})
        return ds

    def health(self) -> dict:
        if not configured():
            return {"adapter": self.name, "ok": False,
                    "error": "GEE is disabled or no service account is configured"}
        try:
            _init()
            return {"adapter": self.name, "ok": True}
        except Exception as e:  # noqa: BLE001
            return {"adapter": self.name, "ok": False, "error": str(e)}


def _area_km2(aoi: dict) -> float:
    from shapely.geometry import shape
    from pyproj import Geod
    return abs(Geod(ellps="WGS84").geometry_area_perimeter(shape(aoi))[0]) / 1e6


def _to_local_convention(ds, crs: str, res: float, scenes):
    """xee hands back `time, lon, lat` in raw DN; the local path wants `time, y, x`
    in reflectance. Renaming here is what keeps detectors backend-agnostic."""
    import numpy as np
    renames = {k: v for k, v in (("lon", "x"), ("lat", "y"), ("X", "x"), ("Y", "y"))
               if k in ds.dims or k in ds.coords}
    if renames:
        ds = ds.rename(renames)
    ds = ds.transpose("time", "y", "x")
    for name in list(ds.data_vars):
        if BANDS.get(name, name) in SCALED:
            ds[name] = ds[name].astype("float32") / 10000.0
        else:
            ds[name] = ds[name].astype("float32")
    from rasterio.transform import from_origin
    x, y = np.asarray(ds["x"].values, float), np.asarray(ds["y"].values, float)
    props = scenes[0].item["properties"]
    ds.attrs.update({"crs": crs, "resolution_m": res,
                     "transform": from_origin(x.min() - res / 2, y.max() + res / 2, res, res),
                     "scene_id": scenes[0].scene_id,
                     "solar_azimuth": props.get("s2:mean_solar_azimuth"),
                     "solar_zenith": props.get("s2:mean_solar_zenith")})
    return ds


def _batch_export(ee, coll, geom, crs: str, res: float, estimate: float):
    """Anything substantial goes through a batch `Export` task. `getDownloadURL` has
    request-size and timeout limits that fail unpredictably in an automated loop, so
    it is never used."""
    bucket = get("adapters.gee.export_bucket")
    if not bucket:
        raise QuotaExceeded(
            f"this task is estimated at {estimate} EECU-hours, above the batch-export "
            "threshold, and adapters.gee.export_bucket is not configured; synchronous "
            "downloads are not used for work this size")
    prefix = f"snitch/{int(time.time())}"
    task = ee.batch.Export.image.toCloudStorage(
        image=coll.toBands(), description="snitch-export", bucket=bucket,
        fileNamePrefix=prefix, region=geom, scale=res, crs=crs, fileFormat="GeoTIFF",
        maxPixels=1e10)
    task.start()
    poll = float(get("adapters.gee.export_poll_s", 15))
    deadline = time.time() + float(get("adapters.gee.export_timeout_s", 3600))
    while time.time() < deadline:
        status = task.status()
        if status.get("state") in ("COMPLETED", "FAILED", "CANCELLED"):
            break
        time.sleep(poll)
    else:
        raise TimeoutError(f"GEE export task {prefix} did not finish in time")
    if status.get("state") != "COMPLETED":
        raise RuntimeError(f"GEE export failed: {status.get('error_message', status)}")
    import rasterio
    import xarray as xr
    import rioxarray  # noqa: F401
    # /vsigs/ is GDAL's lane: ee's initialized credentials do not carry over, so the
    # service account has to be handed to GDAL explicitly or the read fails with an
    # anonymous-access error. The result is loaded inside the Env block, while the
    # credentials are active.
    sa = os.environ.get("GEE_SERVICE_ACCOUNT_JSON") or get("adapters.gee.service_account_json")
    info = sa if sa.lstrip().startswith("{") else open(sa).read()
    with rasterio.Env(CPL_GS_CREDENTIALS=info):
        return xr.open_dataset(f"/vsigs/{bucket}/{prefix}.tif",
                               engine="rasterio").load()


# --- persistence -----------------------------------------------------------

def sidecar(con, project_id: int | None, kind: str, provenance: dict) -> int:
    """The JSON sidecar every GEE product carries: the script, the asset ids, the
    dates, and the algorithm version (spec §11.2). Stored as an immutable artifact."""
    from . import artifacts
    body = {"compute_backend": "gee", "kind": kind,
            "algorithm_version": ALGORITHM_VERSION, "recorded_at": db.now(),
            **provenance}
    return artifacts.put_bytes(con, json.dumps(body, indent=2, default=str).encode(),
                               "application/json",
                               meta={"kind": "gee_sidecar", "project_id": project_id,
                                     **{k: v for k, v in body.items() if k != "script"}})


def persist(con, data: dict, project_id: int, kind: str, provenance: dict) -> dict:
    """Everything GEE computes is exported and stored locally as COGs plus the sidecar,
    so a GEE-backed project is as reproducible offline as a local one."""
    import numpy as np
    from . import artifacts
    out = {name: artifacts.put_cog(con, np.asarray(array, "float32"), data["_crs"],
                                   data["_transform"],
                                   meta={"band": name, "compute_backend": "gee",
                                         "kind": kind, **provenance})
           for name, array in data.items() if not name.startswith("_")}
    return {"bands": out, "sidecar_artifact_id": sidecar(con, project_id, kind, provenance)}


def adapter_for(con, project, purpose: str):
    """The GEE lane, or None when the local path should serve.

    GEE is used for baseline fitting, backtests, and the Cloud Score+ masks those
    loads pull with them — never for forward monitoring, whose catalogue latency is
    worse than the local path's and whose runs are cheap locally (spec §11.2).
    """
    if purpose not in ("baseline", "backtest") or not project_enabled(project):
        return None
    a = GeeAdapter(con, project["id"])
    health = a.health()
    if not health["ok"]:
        db.diagnostic(con, "ADAPTER_FALLBACK", "warning",
                      f"The GEE backend is enabled for this project but unavailable "
                      f"({health.get('error')}); the local path served this {purpose}.",
                      project_id=project["id"], detail={"used": "local"})
        return None
    try:
        check_budget(con, estimate_eecu(project["aoi_area_km2"], 1, 1), project["id"])
    except QuotaExceeded as e:
        log.warning("gee refused on quota", extra={"extra": {"project": project["id"],
                                                             "error": str(e)}})
        return None
    return a
