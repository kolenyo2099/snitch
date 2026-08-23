"""STAC source adapters. Search returns SceneRefs; load does windowed COG reads."""
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache

import httpx
import numpy as np

ENDPOINTS = {
    "earthsearch": ("https://earth-search.aws.element84.com/v1", "sentinel-2-l2a"),
    "cdse": ("https://stac.dataspace.copernicus.eu/v1", "sentinel-2-l2a"),
    # Sentinel-1 RTC: terrain-corrected gamma0 COGs in UTM. Spec §4.3 rule 1 prefers
    # an RTC collection where one exists, which removes the whole in-app GRD
    # calibration / border-noise / layover chain. Earth Search's sentinel-1-grd is
    # raw SAFE measurement TIFFs on a requester-pays bucket and is not usable here.
    "planetary": ("https://planetarycomputer.microsoft.com/api/stac/v1",
                  "sentinel-1-rtc"),
}

SAR_COLLECTIONS = {"sentinel-1-rtc", "sentinel-1-grd"}


def sensor_of(name: str) -> str:
    """S1 or S2, from the adapter's default collection."""
    return "S1" if ENDPOINTS[name][1] in SAR_COLLECTIONS else "S2"

RTC_COLLECTION = "sentinel-1-rtc"
_SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token"


@dataclass
class SceneRef:
    scene_id: str
    platform: str
    datetime: str
    collection: str
    adapter: str
    relative_orbit: int | None
    pass_direction: str | None
    source_uri: str
    item: dict = field(repr=False)

    @property
    def cloud_cover(self) -> float | None:
        return self.item.get("properties", {}).get("eo:cloud_cover")


class StacAdapter:
    """One class for every STAC-API source; they differ only by base URL."""

    def __init__(self, name: str):
        self.name = name
        self.base, self.collection = ENDPOINTS[name]
        self.supported_collections = [self.collection]

    def search(self, aoi: dict, start: datetime, end: datetime,
               collection: str | None = None, limit: int = 500,
               max_cloud: float = 90.0) -> list[SceneRef]:
        coll = collection or self.collection
        body = {
            "collections": [coll],
            "intersects": aoi,
            "datetime": f"{start.date()}T00:00:00Z/{end.date()}T23:59:59Z",
            "limit": 100,
        }
        if coll not in SAR_COLLECTIONS:      # radar has no cloud property to filter on
            body["query"] = {"eo:cloud_cover": {"lt": max_cloud}}
        items = []
        # STAC pagination: the `next` link carries its own method and body. Earth
        # Search returns method=POST with the paging token merged into the original
        # query; re-issuing it as a bare GET (or a POST with no body) silently drops
        # every filter and returns the unfiltered catalogue — S1 GRD scenes nowhere
        # near the AOI, which then fail on band lookup, footprint, and bucket auth.
        method, url, payload = "POST", f"{self.base}/search", body
        with httpx.Client(timeout=60, follow_redirects=True) as c:
            while url and len(items) < limit:
                r = (c.post(url, json=payload) if method == "POST"
                     else c.get(url, params=payload))
                r.raise_for_status()
                page = r.json()
                got = page.get("features", [])
                items += got
                link = next((l for l in page.get("links", [])
                             if l.get("rel") == "next"), None)
                if not got or not link:
                    break
                method = link.get("method", "GET").upper()
                url = link["href"]
                next_body = link.get("body")
                if method == "POST":
                    # merge=true means the body is a delta on the request just sent.
                    payload = ({**(payload or {}), **next_body}
                               if link.get("merge") and next_body else next_body)
                    if payload is None:
                        break   # a POST next link with no body cannot be filtered
                else:
                    payload = next_body if link.get("merge") else None
        wrong = {i.get("collection") for i in items} - {coll}
        if wrong:
            raise RuntimeError(
                f"{self.name} returned collections {sorted(wrong)} for a "
                f"{coll} search; refusing to mix sensors")
        return [self._ref(i) for i in items[:limit]]

    def _ref(self, item: dict) -> SceneRef:
        p = item["properties"]
        return SceneRef(
            scene_id=item["id"],
            platform=p.get("platform", "unknown"),
            datetime=p["datetime"],
            collection=item.get("collection", self.collection),
            adapter=self.name,
            relative_orbit=p.get("sat:relative_orbit"),
            pass_direction=p.get("sat:orbit_state", "").upper() or None,
            source_uri=item.get("links", [{}])[0].get("href", item["id"]),
            item=item,
        )

    def load(self, scenes: SceneRef | list[SceneRef], aoi: dict, bands: list[str],
             resolution: float | None = None, target_crs: str | None = None,
             buffer_px: int = 0):
        """Windowed read into an xarray Dataset with dims ``time, y, x``.

        Every band and scene is reprojected to one AOI-derived analysis grid. Only the
        buffered AOI window is read from each source COG; whole scenes are never read.

        S2 arrays are surface reflectance. S1 RTC arrays are **linear gamma0 power**,
        not dB: the Wishart detector needs unlogged intensities, and every dB figure a
        detector or the UI wants is one `10*log10` away. Converting here would be
        lossy in the direction that matters."""
        import rasterio
        import xarray as xr
        from rasterio.mask import mask as rio_mask
        from rasterio.transform import from_origin
        from rasterio.warp import Resampling, reproject, transform_geom
        from shapely.geometry import mapping, shape

        scenes = [scenes] if isinstance(scenes, SceneRef) else list(scenes)
        if not scenes:
            raise ValueError("load requires at least one scene")
        first_href = self._sign(self._href(scenes[0], bands[0]))
        with rasterio.open(first_href) as first:
            crs = target_crs or str(first.crs)
            res = float(resolution or min(abs(first.transform.a), abs(first.transform.e)))
        analysis_geom = shape(transform_geom("EPSG:4326", crs, aoi))
        if buffer_px:
            analysis_geom = analysis_geom.buffer(buffer_px * res)
        minx, miny, maxx, maxy = analysis_geom.bounds
        width = max(1, int(np.ceil((maxx - minx) / res)))
        height = max(1, int(np.ceil((maxy - miny) / res)))
        transform = from_origin(minx, maxy, res, res)
        xs = minx + (np.arange(width) + 0.5) * res
        ys = maxy - (np.arange(height) + 0.5) * res

        datasets = []
        for scene in scenes:
            sar = scene.collection in SAR_COLLECTIONS
            variables = {}
            for band in bands:
                href = self._sign(self._href(scene, band))
                with rasterio.open(href) as src:
                    source_geom = transform_geom(crs, src.crs, mapping(analysis_geom))
                    clipped, clipped_transform = rio_mask(
                        src, [source_geom], crop=True, filled=True,
                        nodata=src.nodata if src.nodata is not None else 0, indexes=[1])
                    source = clipped[0].astype("float32")
                    source[source == (src.nodata if src.nodata is not None else 0)] = np.nan
                    dst = np.full((height, width), np.nan, "float32")
                    reproject(source, dst, src_transform=clipped_transform,
                              src_crs=src.crs, dst_transform=transform, dst_crs=crs,
                              src_nodata=np.nan, dst_nodata=np.nan,
                              resampling=(Resampling.nearest if band in
                                          {"SCL", "cloud_probability", "cs"}
                                          else Resampling.bilinear))
                if sar:
                    dst[~np.isfinite(dst) | (dst <= 0)] = np.nan
                elif band not in {"SCL", "cloud_probability", "cs"}:
                    dst = dst / 10000.0
                variables[band] = (("time", "y", "x"), dst[None])
            props = scene.item.get("properties", {})
            time_value = np.datetime64(scene.datetime.replace("Z", "").split("+")[0])
            ds = xr.Dataset(variables, coords={"time": [time_value],
                                                "y": ys, "x": xs})
            ds.attrs.update({"crs": crs, "transform": transform,
                             "resolution_m": res, "scene_id": scene.scene_id,
                             "solar_azimuth": props.get("s2:mean_solar_azimuth"),
                             "solar_zenith": props.get("s2:mean_solar_zenith")})
            datasets.append(ds)
        return xr.concat(datasets, dim="time") if len(datasets) > 1 else datasets[0]

    def _sign(self, href: str) -> str:
        """Planetary Computer blobs need a SAS token. It is handed out anonymously;
        no account, no credential in config."""
        if "blob.core.windows.net" not in href or "?" in href:
            return href
        acct, container = href.split("//")[1].split(".")[0], href.split("/")[3]
        return f"{href}?{sas_token(acct, container)}"

    @staticmethod
    def _href(scene: SceneRef, band: str) -> str:
        assets = scene.item["assets"]
        for key in (band, band.lower(), _ALIASES.get(band, band)):
            if key in assets:
                return assets[key]["href"]
        raise KeyError(f"band {band} not in {scene.scene_id}: {sorted(assets)}")

    def health(self) -> dict:
        if self.name == "cdse":
            # The catalogue is public, but its current S2 assets resolve to authenticated
            # s3://eodata objects. Until that credential exchange is implemented, calling
            # this adapter healthy would select a source that can search but cannot load.
            return {"adapter": self.name, "ok": False,
                    "error": "authenticated CDSE asset access is not configured"}
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(f"{self.base}/collections/{self.collection}")
            return {"adapter": self.name, "ok": r.status_code == 200,
                    "status": r.status_code}
        except Exception as e:  # noqa: BLE001
            return {"adapter": self.name, "ok": False, "error": str(e)}


@lru_cache(maxsize=8)
def _sas_token(account: str, container: str, _bucket: int = 0) -> str:
    r = httpx.get(f"{_SAS}/{account}/{container}", timeout=30)
    r.raise_for_status()
    return r.json()["token"]


def sas_token(account: str, container: str) -> str:
    # ponytail: tokens last ~24 h; a 30-minute cache bucket beats parsing msft:expiry.
    return _sas_token(account, container, int(time.time()) // 1800)


_ALIASES = {"B02": "blue", "B03": "green", "B04": "red", "B08": "nir",
            "B8A": "nir08", "B11": "swir16", "B12": "swir22", "SCL": "scl",
            "cloud_probability": "cloud_probability", "cs": "cs"}


def as_array_dict(data) -> dict:
    """Normalize xarray production loads and lightweight synthetic test adapters."""
    if isinstance(data, dict):
        return data
    if not hasattr(data, "data_vars"):
        raise TypeError(f"unsupported adapter load result: {type(data).__name__}")
    out = {name: np.asarray(value.isel(time=0).values, dtype="float32")
           for name, value in data.data_vars.items()}
    out["_crs"] = data.attrs["crs"]
    out["_transform"] = data.attrs["transform"]
    out["_resolution_m"] = data.attrs.get("resolution_m")
    out["_solar_azimuth"] = data.attrs.get("solar_azimuth")
    out["_solar_zenith"] = data.attrs.get("solar_zenith")
    return out


def load_scene(adapter, scene: SceneRef, aoi: dict, bands: list[str],
               resolution: float | None = None, target_crs: str | None = None,
               buffer_px: int = 0) -> dict:
    """Compatibility boundary for protocol adapters and small synthetic fixtures."""
    if isinstance(adapter, StacAdapter):
        loaded = adapter.load(scene, aoi, bands, resolution=resolution,
                              target_crs=target_crs, buffer_px=buffer_px)
    else:
        loaded = adapter.load(scene, aoi, bands)
    return as_array_dict(loaded)


def get_adapter(name: str) -> StacAdapter:
    return StacAdapter(name)


def first_healthy(preference: list[str], sensor: str | None = None) -> StacAdapter:
    """`sensor` ("S1"/"S2") restricts the candidates to sources that actually carry
    that data. Without it an optical project whose preference lists a radar source
    loads S1 scenes and every band lookup fails with `band B02 not in S1..._GRDH...`."""
    if sensor:
        preference = [n for n in preference if sensor_of(n) == sensor]
        if not preference:
            raise RuntimeError(f"NO_SOURCE_FOR_SENSOR: no configured adapter serves {sensor}")
    last = None
    for name in preference:
        a = get_adapter(name)
        h = a.health()
        if h["ok"]:
            return a
        last = h
    raise RuntimeError(f"SOURCE_UNREACHABLE: {json.dumps(last)}")
