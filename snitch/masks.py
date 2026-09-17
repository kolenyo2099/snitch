"""Mask chain. Each mask returns a boolean 'invalid' array plus a pixel count."""
import numpy as np

SCL_CLOUD = (3, 8, 9, 10)   # shadow, cloud medium/high, cirrus
SCL_SNOW = (11,)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    from scipy.ndimage import binary_dilation
    return binary_dilation(m, structure=np.ones((2 * r + 1, 2 * r + 1), bool))


def cloudscore_plus(data: dict, params: dict) -> np.ndarray | None:
    """Cloud Score+ clear-sky score: lower values are invalid/cloudy."""
    cs = data.get("cs")
    if cs is None:
        return None
    return np.nan_to_num(cs, nan=-np.inf) < float(params.get("cloudscore_threshold", 0.60))


def _shift(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """Translate a boolean mask without numpy.roll's edge wrapping."""
    out = np.zeros_like(m)
    sy0, sy1 = max(0, -dy), min(m.shape[0], m.shape[0] - dy)
    sx0, sx1 = max(0, -dx), min(m.shape[1], m.shape[1] - dx)
    if sy1 > sy0 and sx1 > sx0:
        out[sy0 + dy:sy1 + dy, sx0 + dx:sx1 + dx] = m[sy0:sy1, sx0:sx1]
    return out


def s2cloudless(data: dict, params: dict) -> np.ndarray | None:
    """s2cloudless probability fallback with solar-geometry shadow projection."""
    if data.get("cs") is not None:  # Cloud Score+ is the preferred mask.
        return None
    probability = data.get("cloud_probability")
    if probability is None:
        return None
    p = probability / 100.0 if np.nanmax(probability) > 1.0 else probability
    cloud = np.nan_to_num(p, nan=1.0) > float(params.get("s2cloudless_threshold", 0.40))
    azimuth = data.get("_solar_azimuth")
    zenith = data.get("_solar_zenith")
    resolution = float(data.get("_resolution_m") or params.get("resolution_m", 10.0))
    if azimuth is None or zenith is None:
        return _dilate(cloud, 2)
    height = float(params.get("cloud_projection_height_m", 2000.0))
    distance = min(int(round(np.tan(np.deg2rad(float(zenith))) * height / resolution)),
                   int(params.get("max_shadow_projection_px", 50)))
    direction = np.deg2rad((float(azimuth) + 180.0) % 360.0)
    dx, dy = np.sin(direction), -np.cos(direction)
    projected = cloud.copy()
    for step in range(1, distance + 1):
        projected |= _shift(cloud, int(round(dy * step)), int(round(dx * step)))
    return _dilate(projected, 2)


def scl_shadow(data: dict, params: dict) -> np.ndarray:
    scl = data.get("SCL")
    if scl is None:
        return np.zeros(next(iter(data.values())).shape, bool)
    return _dilate(np.isin(np.nan_to_num(scl, nan=0).astype(int), SCL_CLOUD), 2)


def snow(data: dict, params: dict) -> np.ndarray:
    scl = data.get("SCL")
    m = np.isin(np.nan_to_num(scl, nan=0).astype(int), SCL_SNOW) if scl is not None \
        else np.zeros(next(iter(data.values())).shape, bool)
    if "B03" in data and "B11" in data:
        with np.errstate(invalid="ignore", divide="ignore"):
            ndsi = (data["B03"] - data["B11"]) / (data["B03"] + data["B11"])
        m |= np.nan_to_num(ndsi, nan=0) > 0.4
    return m


def nodata(data: dict, params: dict) -> np.ndarray:
    return ~np.all([np.isfinite(v) for k, v in data.items()
                    if not k.startswith("_")], axis=0)


def slope(data: dict, params: dict) -> np.ndarray | None:
    """Terrain exclusion for the radar recipes (spec §8 R4, §4.3). Slopes above
    `max_slope_deg` are geometrically unreliable in SAR: foreshortening compresses
    them and layover/shadow destroys them outright. Returns None when no DEM tile
    could be fetched, which the caller must surface rather than swallow."""
    from . import dem
    aoi, crs, transform = params.get("_aoi"), data.get("_crs"), data.get("_transform")
    shape = next(v.shape for k, v in data.items() if not k.startswith("_"))
    if not (aoi and crs and transform is not None):
        return None
    s = dem.slope_deg(aoi, crs, transform, shape)
    if s is None:
        return None
    return np.nan_to_num(s, nan=0.0) > float(params.get("max_slope_deg", 5.0))


def hand(data: dict, params: dict) -> np.ndarray | None:
    """Exclude terrain too high above its D8-derived downstream drainage."""
    from . import dem
    aoi, crs, transform = params.get("_aoi"), data.get("_crs"), data.get("_transform")
    shape = next(v.shape for k, v in data.items() if not k.startswith("_"))
    if not (aoi and crs and transform is not None):
        return None
    height = dem.hand_m(aoi, crs, transform, shape,
                        float(params.get("hand_drainage_area_m2", 1_000_000)))
    if height is None:
        return None
    return np.nan_to_num(height, nan=0.0) > float(params.get("max_hand_m", 15.0))


def water_permanent(data: dict, params: dict) -> np.ndarray | None:
    """Optional static permanent-water layer, aligned by the imagery adapter."""
    layer = data.get("permanent_water")
    if layer is None:
        return None
    return np.nan_to_num(layer, nan=0.0) >= float(
        params.get("permanent_water_threshold", 0.5))


CHAIN = {"cloudscore_plus": cloudscore_plus, "s2cloudless": s2cloudless,
         "scl_shadow": scl_shadow, "snow": snow, "nodata": nodata,
         "slope": slope, "hand": hand, "water_permanent": water_permanent}

#: Probability layers a recipe may declare that a catalogue genuinely does not
#: publish (CDSE carries neither; Earth Search only carries s2cloudless). When the
#: scene has the SCL classification band, its cloud/shadow classes stand in —
#: recorded as a substitution, never silently.
_SCL_FALLBACKS = ("cloudscore_plus", "s2cloudless")


def apply_chain(data: dict, names: list[str], params: dict | None = None):
    """Returns (invalid_mask, {mask_name: pixels_removed}, valid_fraction)."""
    params = params or {}
    shape = next(v.shape for k, v in data.items() if not k.startswith("_"))
    invalid = np.zeros(shape, bool)
    summary = {}
    components = {}
    substitutes = {}
    for n in ["nodata"] + [x for x in names if x in CHAIN and x != "nodata"]:
        m = CHAIN[n](data, params)
        if m is None and n in _SCL_FALLBACKS and data.get("SCL") is not None:
            m = scl_shadow(data, params)
            substitutes[n] = "scl_shadow"
        if m is None:
            summary[n] = None      # declared by the recipe, unavailable at run time
            continue
        components[n] = m.astype(bool)
        summary[n] = int((m & ~invalid).sum())
        invalid |= m
    for n in names:
        if n not in CHAIN:
            summary[n] = None      # declared by the recipe, not implemented here
    valid_fraction = float(1.0 - invalid.mean())
    data["_mask_components"] = components
    data["_mask_substitutes"] = substitutes
    return invalid, summary, valid_fraction
