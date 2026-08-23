"""Copernicus GLO-30 DEM on the analysis grid, and slope derived from it.

Used by the radar recipes: slope drives the >5 degree exclusion of R4 (spec §8, R4)
and stands in for the layover/shadow reasoning of §4.3 on any product that is not
already terrain-corrected. Tiles are public anonymous COGs on Earth Search.
"""
from __future__ import annotations
from functools import lru_cache

import numpy as np

from .log import log

COLLECTION = "cop-dem-glo-30"


def _key(crs: str, transform, shape) -> tuple:
    t = transform
    return (crs, round(t.c, 1), round(t.f, 1), round(t.a, 4), round(t.e, 4), shape)


def elevation(aoi: dict, crs: str, transform, shape) -> np.ndarray | None:
    """GLO-30 elevation in metres, reprojected onto (crs, transform, shape).
    Returns None if no tile is reachable — callers must stay loud about that."""
    return _elevation(_key(crs, transform, shape), _freeze(aoi), crs, transform, shape)


def _freeze(aoi: dict) -> str:
    import json
    return json.dumps(aoi, sort_keys=True)


@lru_cache(maxsize=8)
def _elevation(_k, aoi_json: str, crs: str, transform, shape) -> np.ndarray | None:
    import json
    import rasterio
    from rasterio.warp import Resampling, reproject

    from .adapters import get_adapter
    from datetime import datetime, timezone

    aoi = json.loads(aoi_json)
    try:
        # The DEM is static; the datetime range only satisfies the STAC API.
        scenes = get_adapter("earthsearch").search(
            aoi, datetime(2000, 1, 1, tzinfo=timezone.utc),
            datetime.now(timezone.utc), collection=COLLECTION, limit=20,
            max_cloud=100)
    except Exception as e:  # noqa: BLE001
        log.warning("dem search failed", extra={"extra": {"error": str(e)}})
        return None
    if not scenes:
        return None
    out = np.full(shape, np.nan, dtype="float32")
    got = False
    for s in scenes:
        href = next((a["href"] for k, a in s.item["assets"].items()
                     if k in ("data", "elevation")), None)
        if not href:
            continue
        try:
            with rasterio.open(href) as src:
                tile = np.full(shape, np.nan, dtype="float32")
                reproject(rasterio.band(src, 1), tile, dst_crs=crs,
                          dst_transform=transform, dst_nodata=np.nan,
                          resampling=Resampling.bilinear)
        except Exception as e:  # noqa: BLE001
            log.warning("dem tile failed", extra={"extra": {"tile": s.scene_id,
                                                            "error": str(e)}})
            continue
        out = np.where(np.isfinite(out), out, tile)
        got = True
    return out if got and np.isfinite(out).any() else None


def slope_deg(aoi: dict, crs: str, transform, shape) -> np.ndarray | None:
    """Slope in degrees from the GLO-30 surface, on the analysis grid."""
    z = elevation(aoi, crs, transform, shape)
    if z is None:
        return None
    dy, dx = np.gradient(z, abs(transform.e), abs(transform.a))
    return np.degrees(np.arctan(np.hypot(dx, dy))).astype("float32")


def hand_from_elevation(z: np.ndarray, pixel_area_m2: float,
                        drainage_area_m2: float = 1_000_000) -> np.ndarray:
    """D8 height above nearest downstream drainage, in metres.

    Flow accumulation identifies the drainage network; each cell inherits the elevation
    of its first downstream drainage cell. Closed sinks conservatively drain to themselves.
    """
    z = np.asarray(z, dtype="float64")
    h, w = z.shape
    receiver = np.arange(h * w).reshape(h, w)
    valid = np.isfinite(z)
    for y in range(h):
        for x in range(w):
            if not valid[y, x]:
                continue
            best, best_z = y * w + x, z[y, x]
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if (dy or dx) and 0 <= yy < h and 0 <= xx < w and valid[yy, xx] \
                            and z[yy, xx] < best_z:
                        best, best_z = yy * w + xx, z[yy, xx]
            receiver[y, x] = best
    flat_z, recv = z.ravel(), receiver.ravel()
    accumulation = np.where(valid.ravel(), pixel_area_m2, 0.0)
    order = np.argsort(np.nan_to_num(flat_z, nan=-np.inf))[::-1]
    for i in order:
        if recv[i] != i:
            accumulation[recv[i]] += accumulation[i]
    drainage = accumulation >= drainage_area_m2
    base = np.full(h * w, np.nan)
    for i in order[::-1]:  # downstream (lower) cells are resolved first
        if not np.isfinite(flat_z[i]):
            continue
        base[i] = flat_z[i] if drainage[i] or recv[i] == i else base[recv[i]]
    return np.maximum(0.0, flat_z - base).reshape(h, w).astype("float32")


def hand_m(aoi: dict, crs: str, transform, shape,
           drainage_area_m2: float = 1_000_000) -> np.ndarray | None:
    z = elevation(aoi, crs, transform, shape)
    if z is None:
        return None
    return hand_from_elevation(z, abs(transform.a * transform.e), drainage_area_m2)
