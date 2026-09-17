"""AOI helpers: area, analysis CRS, and geometry vectorisation."""
import json
import math

from shapely.geometry import mapping, shape


def clean(aoi: dict) -> dict:
    """Normalise a user-drawn geometry before it is stored or sent to a STAC API.

    Hand-drawn polygons routinely carry duplicate consecutive vertices (a stray
    double-click) or self-intersections (a bowtie). Earth Search rejects the former
    with a bare 400 -- "Cannot determine orientation: edges adjacent to (x,y)
    coincide" -- which surfaced as an unexplained coverage-check failure. buffer(0)
    drops the duplicates and splits the bowtie into a valid MultiPolygon.
    """
    fixed = shape(aoi).buffer(0)
    if fixed.is_empty:
        raise ValueError("That shape encloses no area. Draw it again.")
    return mapping(fixed)


def utm_crs(aoi: dict) -> str:
    """UTM zone EPSG code for the AOI centroid — the project's analysis CRS."""
    c = shape(aoi).centroid
    zone = int((c.x + 180) // 6) + 1
    return f"EPSG:{326 if c.y >= 0 else 327}{zone:02d}"


def area_km2(aoi: dict) -> float:
    """Geodesic area via an equal-area projection centred on the AOI."""
    from pyproj import Geod
    g = shape(aoi)
    geod = Geod(ellps="WGS84")
    polys = g.geoms if g.geom_type == "MultiPolygon" else [g]
    return sum(abs(geod.geometry_area_perimeter(p)[0]) for p in polys) / 1e6


def pixel_area_m2(transform) -> float:
    return abs(transform.a * transform.e)


def vectorise(mask, transform, crs: str) -> dict:
    """Binary mask -> GeoJSON MultiPolygon in EPSG:4326."""
    from pyproj import Transformer
    from rasterio.features import shapes
    from shapely.ops import transform as sh_transform
    from shapely.geometry import shape as sh_shape
    import numpy as np

    tr = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
    polys = [sh_transform(tr, sh_shape(geom))
             for geom, val in shapes(mask.astype("uint8"), mask=mask,
                                     transform=transform) if val == 1]
    if not polys:
        return {"type": "MultiPolygon", "coordinates": []}
    from shapely.geometry import MultiPolygon
    from shapely.ops import unary_union
    u = unary_union(polys)
    return mapping(u if u.geom_type == "MultiPolygon" else MultiPolygon([u]))


def iou(a: dict, b: dict) -> float:
    ga, gb = shape(a).buffer(0), shape(b).buffer(0)
    u = ga.union(gb).area
    return (ga.intersection(gb).area / u) if u else 0.0


def bbox(aoi: dict) -> tuple:
    return shape(aoi).bounds


def suggest_cron(aoi: dict, sensor: str) -> str:
    """Poll cadence from the real revisit at this latitude (spec §13.2 step 4).
    S2 revisit is 5 days at the equator, tightening toward the poles from swath
    overlap; S1 is 6 days in the current C/D configuration."""
    lat = abs(shape(aoi).centroid.y)
    if sensor == "S1":
        days = 6
    else:
        days = 5 if lat < 30 else (3 if lat < 60 else 2)
    hour = 3
    return f"0 {hour} */{days} * *" if days > 1 else f"0 {hour} * * *"
