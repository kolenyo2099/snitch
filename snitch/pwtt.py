"""Pixel-Wise T-Test (PWTT) battle-damage assessment, computed in Earth Engine.

Loyalty note: none of the statistics are reimplemented here. The published `pwtt`
package (Ballinger, MIT, https://github.com/oballinger/PWTT) is called unchanged,
so the Lee filter, the per-orbit two-sample t-test on log backscatter, the
Bonferroni correction across orbits, the Dynamic World built-up mask and the
multi-scale T statistic are byte-for-byte the reference implementation. What this
module adds is exactly what the reference app adds around it: the user-chosen
damage threshold and the AOI summary reducers.

This lane is deliberately outside the forward-monitoring pipeline. PWTT compares
two *periods* server-side on GEE's own `S1_GRD_FLOAT` preprocessing; the local
detector path scores one scene against a fitted baseline on RTC data. Reproducing
PWTT on local arrays would produce different numbers, which is the one thing this
must not do.
"""
from __future__ import annotations

import calendar
import json
import os
from datetime import datetime, timedelta
from typing import Any

from .config import get

REFERENCE = {
    "citation": ("Ballinger, O. (2025). The Pixelwise T-Test: a new algorithm for "
                 "battle damage detection using Sentinel-1 imagery. Remote Sensing "
                 "of Environment."),
    "url": "https://www.sciencedirect.com/science/article/pii/S0034425725004298",
    "implementation": "pwtt (PyPI) — called unchanged; no local reimplementation",
}
LIMITATIONS = [
    "Damage is scored only where Dynamic World reports built-up cover above 0.1 in "
    "the year before the conflict start; rural destruction outside that mask is "
    "invisible to the method.",
    "The t-test needs several Sentinel-1 scenes per relative orbit in each window. "
    "Coverage counts are returned with every assessment for that reason.",
    "The T statistic detects a change in backscatter, not its cause: new "
    "construction, rubble clearance and flooding all raise it.",
]
DEFAULTS = {"pre_interval": 12.0, "post_interval": 2.0, "threshold": 3.3}
#: Above this the synchronous GeoTIFF download is refused rather than left to fail
#: mid-run with an opaque Earth Engine error. Roughly 16 MB of float32.
MAX_DOWNLOAD_PX = 4_000_000


def _init():
    """Initialise Earth Engine from whichever credential this machine has.

    A service account when Snitch is configured with one, otherwise the normal
    `earthengine authenticate` user login — the same credential the reference app
    uses, so both apps talk to the same backend as the same principal.
    """
    import ee
    sa = os.environ.get("GEE_SERVICE_ACCOUNT_JSON") or get("adapters.gee.service_account_json")
    project = (get("adapters.gee.project_id") or os.environ.get("EARTH_ENGINE_PROJECT")
               or os.environ.get("GEE_PROJECT_ID"))
    if sa:
        info = json.loads(sa) if sa.lstrip().startswith("{") else json.load(open(sa))
        ee.Initialize(ee.ServiceAccountCredentials(info["client_email"],
                                                   key_data=json.dumps(info)),
                      project=project)
    else:
        if not project:
            raise ValueError("An Earth Engine cloud project is required: set "
                             "adapters.gee.project_id or EARTH_ENGINE_PROJECT.")
        ee.Initialize(project=project)
    ee.data.setDeadline(600_000)
    return ee


def _params(overrides: dict[str, Any]) -> dict[str, Any]:
    p = {**DEFAULTS, **{k: v for k, v in overrides.items() if v is not None}}
    p["pre_interval"] = float(p["pre_interval"])
    p["post_interval"] = float(p["post_interval"])
    p["threshold"] = float(p["threshold"])
    return p


def coverage(aoi, params: dict[str, Any]) -> dict[str, Any]:
    """Count the Sentinel-1 scenes the t-test will actually compare.

    The test runs per relative orbit, so the per-orbit minimums are the numbers that
    describe the statistical power of a run; an orbit present in only one of the two
    windows contributes nothing and is excluded here.
    """
    ee = _init()
    aoi = aoi if not isinstance(aoi, dict) else ee.Geometry(aoi)
    war_start = datetime.fromisoformat(params["war_start"]).date().isoformat()
    inference_start = datetime.fromisoformat(params["inference_start"]).date().isoformat()
    pre_start = (datetime.fromisoformat(params["war_start"])
                 - timedelta(days=round(params["pre_interval"] * 30))).date().isoformat()
    post_end = (datetime.fromisoformat(params["inference_start"])
                + timedelta(days=round(params["post_interval"] * 30))).date().isoformat()

    base = (ee.ImageCollection("COPERNICUS/S1_GRD_FLOAT")
            .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
            .filter(ee.Filter.eq("instrumentMode", "IW"))
            .filterBounds(aoi))
    pre = base.filterDate(
        ee.Date(params["war_start"]).advance(
            ee.Number(params["pre_interval"]).multiply(-1), "month"),
        params["war_start"])
    post = base.filterDate(
        params["inference_start"],
        ee.Date(params["inference_start"]).advance(params["post_interval"], "month"))
    pre_hist = pre.aggregate_histogram("relativeOrbitNumber_start").getInfo() or {}
    post_hist = post.aggregate_histogram("relativeOrbitNumber_start").getInfo() or {}

    if not post_hist:
        raise ValueError(
            f"No Sentinel-1 scenes were found in the post-event window from "
            f"{inference_start} to {post_end}. Choose a later inference start date "
            "or a longer post-event window.")
    if not pre_hist:
        raise ValueError(
            f"No Sentinel-1 scenes were found in the baseline window before "
            f"{war_start}. Choose a later conflict start date or a shorter baseline.")
    shared = set(pre_hist) & set(post_hist)
    if not shared:
        raise ValueError(
            f"No overlapping Sentinel-1 orbit coverage between the baseline window "
            f"ending {war_start} and the post-event window from {inference_start} to "
            f"{post_end}. Choose a later inference start date or a longer window.")
    return {"pre_scenes": int(sum(pre_hist[o] for o in shared)),
            "post_scenes": int(sum(post_hist[o] for o in shared)),
            "orbit_count": len(shared),
            "min_pre_scenes_per_orbit": int(min(pre_hist[o] for o in shared)),
            "min_post_scenes_per_orbit": int(min(post_hist[o] for o in shared)),
            "pre_window": [pre_start, war_start],
            "post_window": [inference_start, post_end]}


def damage_image(aoi, params: dict[str, Any], validate: bool = True):
    """The reference PWTT image, with the damage mask rebuilt at the run's threshold.

    `pwtt.detect_damage` hardcodes its own cut at T > 3 inside the package; rebuilding
    the mask from the returned T statistic is how the threshold stays a run parameter
    without forking the reference implementation.
    """
    import pwtt as reference
    if validate:
        coverage(aoi, params)
    image = reference.detect_damage(
        aoi=aoi, war_start=params["war_start"],
        inference_start=params["inference_start"],
        pre_interval=params["pre_interval"], post_interval=params["post_interval"],
        viz=False)
    mask = image.select("T_statistic").gt(params["threshold"]).rename("damage").toFloat()
    return image.addBands(mask, overwrite=True)


def _summary(image, aoi) -> dict[str, Any]:
    """Reduce the damage image to the handful of numbers a report quotes."""
    import ee

    def reduce(band, reducer):
        return image.select(band).reduceRegion(
            reducer=reducer, geometry=aoi, scale=20, maxPixels=1e10,
            tileScale=4).get(band)

    damaged_area = (image.select("damage").selfMask().multiply(ee.Image.pixelArea())
                    .rename("damaged_area")
                    .reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi, scale=20,
                                  maxPixels=1e10, tileScale=4).get("damaged_area"))
    # The T statistic is masked to built-up pixels inside the reference package, so
    # its own mask is the built-up area. Damage share is reported against that, not
    # against the whole AOI: dividing by the AOI would dilute the share to nothing
    # for any polygon that also contains farmland or water.
    built_area = (image.select("T_statistic").mask().multiply(ee.Image.pixelArea())
                  .rename("built_area")
                  .reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi, scale=20,
                                maxPixels=1e10, tileScale=4).get("built_area"))
    result = ee.Dictionary({
        "damaged_area_ha": ee.Number(damaged_area).divide(10000),
        "built_area_ha": ee.Number(built_area).divide(10000),
        "damage_share_pct": ee.Number(damaged_area).divide(
            ee.Number(built_area).max(1)).multiply(100),
        "aoi_share_pct": ee.Number(damaged_area).divide(aoi.area(1)).multiply(100),
        "mean_t_score": ee.Number(reduce("T_statistic", ee.Reducer.mean())),
        "max_t_score": ee.Number(reduce("T_statistic", ee.Reducer.max())),
        "damaged_pixel_estimate": ee.Number(reduce("damage", ee.Reducer.sum())).round(),
    }).getInfo()
    return {"damaged_area_ha": round(float(result["damaged_area_ha"]), 2),
            "built_area_ha": round(float(result["built_area_ha"]), 2),
            "damage_share_pct": round(float(result["damage_share_pct"]), 2),
            "aoi_share_pct": round(float(result["aoi_share_pct"]), 2),
            "mean_t_score": round(float(result["mean_t_score"]), 2),
            "max_t_score": round(float(result["max_t_score"]), 2),
            "damaged_pixel_estimate": int(result["damaged_pixel_estimate"])}


def assess(aoi_geojson: dict, war_start: str, inference_start: str,
           pre_interval: float | None = None, post_interval: float | None = None,
           threshold: float | None = None) -> dict[str, Any]:
    """Assess one AOI between two periods and return the damage summary."""
    ee = _init()
    params = _params({"war_start": war_start, "inference_start": inference_start,
                      "pre_interval": pre_interval, "post_interval": post_interval,
                      "threshold": threshold})
    aoi = ee.Geometry(aoi_geojson)
    cov = coverage(aoi, params)
    summary = _summary(damage_image(aoi, params, validate=False), aoi)
    return {**summary, "coverage": cov, "parameters": params,
            "compute_backend": "gee", "method": "pwtt",
            "method_version": __import__("pwtt").__version__,
            "reference": REFERENCE, "limitations": LIMITATIONS}


# --- the monitoring lane ---------------------------------------------------
#
# Everything below serves the `battle_damage_radar` recipe, which runs PWTT once per
# new Sentinel-1 acquisition with the post-event window ending on that acquisition.
# The method is unchanged: it is the same two-period test, evaluated repeatedly as
# the post-event window rolls forward, which is how the reference paper builds its
# cumulative damage series.


def minus_months(when: datetime, months: float) -> datetime:
    """Calendar-month subtraction, matching Earth Engine's `Date.advance(-n, 'month')`.

    Whole months are calendar months (clamped to the length of the shorter month);
    any fraction is taken as 30-day thirtieths, which is how the reference app
    already treats fractional intervals.
    """
    whole = int(months)
    year, month = when.year, when.month - whole
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(when.day, calendar.monthrange(year, month)[1])
    return when.replace(year=year, month=month, day=day) - timedelta(
        days=round((months - whole) * 30))


def window_for(sensed_at: str, params: dict[str, Any]) -> str:
    """The inference start that makes the post-event window *end* on this scene.

    A forward run may never look at imagery the run itself could not have seen, so
    the window is anchored at its end rather than its beginning.
    """
    end = datetime.fromisoformat(sensed_at.replace("Z", "+00:00")).replace(tzinfo=None)
    return minus_months(end, params["post_interval"]).date().isoformat()


def t_statistic_grid(aoi_geojson: dict, params: dict[str, Any], crs: str, transform,
                     shape: tuple[int, int], scale: float = 10.0):
    """Fetch the PWTT T statistic and put it on the project's analysis grid.

    The statistic is computed in Earth Engine — that is the whole point, since the
    method is defined on GEE's own `S1_GRD_FLOAT` preprocessing — and only the
    finished raster is downloaded. Pixels outside the built-up mask come back masked
    and stay NaN here, so they are never scored.
    """
    import numpy as np
    from rasterio.io import MemoryFile
    from rasterio.warp import Resampling, reproject
    import httpx

    if shape[0] * shape[1] > MAX_DOWNLOAD_PX:
        raise ValueError(
            f"This AOI is {shape[0] * shape[1]:,} pixels at {scale:g} m, above the "
            f"{MAX_DOWNLOAD_PX:,} that can be fetched from Earth Engine in one "
            "request. Split the area, or use the one-shot /api/v1/pwtt/assess lane, "
            "which summarises server-side without downloading the raster.")
    ee = _init()
    aoi = ee.Geometry(aoi_geojson)
    image = damage_image(aoi, params, validate=False).select("T_statistic")
    url = image.getDownloadURL({"region": aoi, "scale": scale, "crs": crs,
                                "format": "GEO_TIFF"})
    body = httpx.get(url, timeout=600.0, follow_redirects=True)
    body.raise_for_status()
    with MemoryFile(body.content) as memory, memory.open() as src:
        source = src.read(1, masked=True).filled(np.nan).astype("float32")
        out = np.full(shape, np.nan, "float32")
        reproject(source, out, src_transform=src.transform, src_crs=src.crs,
                  dst_transform=transform, dst_crs=crs, src_nodata=np.nan,
                  dst_nodata=np.nan, resampling=Resampling.bilinear)
    return out


def eecu_estimate(shape: tuple[int, int], coverage_scenes: int) -> float:
    """Reuse the GEE lane's cost model so PWTT runs land in the same usage ledger."""
    from . import gee
    # 10 m pixels: one pixel is 100 m2, i.e. 1e-4 km2.
    return gee.estimate_eecu(shape[0] * shape[1] * 1e-4,
                             max(coverage_scenes, 1), 2)
