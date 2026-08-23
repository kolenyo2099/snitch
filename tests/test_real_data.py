"""Live smoke test against the public Earth Search catalogue. No credentials.

Run with:  pytest tests/test_real_data.py -m live -v --no-header
Skipped by default so CI stays offline.
"""
import os
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from terrawatch import adapters

pytestmark = pytest.mark.skipif(
    os.environ.get("TERRAWATCH_LIVE") != "1",
    reason="live network test; set TERRAWATCH_LIVE=1 to run")

# ~2 km box over Rondônia — cloud-light, covered by every S2 orbit.
AOI = {"type": "Polygon", "coordinates": [[
    [-63.02, -9.02], [-63.00, -9.02], [-63.00, -9.00], [-63.02, -9.00], [-63.02, -9.02]]]}


@pytest.fixture(scope="module")
def scene():
    end = datetime.now(timezone.utc)
    a = adapters.get_adapter("earthsearch")
    scenes = a.search(AOI, end - timedelta(days=60), end, max_cloud=20, limit=20)
    assert scenes, "no S2 scene found over the AOI in the last 60 days"
    return a, scenes[0]


def test_search_returns_optical_scenes(scene):
    _, s = scene
    assert s.collection == "sentinel-2-l2a"
    assert s.collection not in adapters.SAR_COLLECTIONS


def test_windowed_load_returns_real_reflectance(scene):
    """The regression that broke production: unsigned S3 reads and band lookup."""
    a, s = scene
    data = adapters.load_scene(a, s, AOI, ["B02", "B04", "B08", "SCL"], resolution=10)
    for band in ("B02", "B04", "B08"):
        arr = data[band]
        assert arr.size > 100, f"{band} window is empty"
        finite = arr[np.isfinite(arr)]
        assert finite.size, f"{band} is all-NaN — window missed the raster"
        assert 0.0 <= np.nanmedian(finite) <= 1.6, f"{band} not scaled reflectance"
    assert set(np.unique(data["SCL"][np.isfinite(data["SCL"])])) <= set(range(12))
    assert data["_crs"].startswith("EPSG:")


def test_optical_project_never_selects_a_radar_adapter():
    """No network. The exact bug from the logs: `band B02 not in S1C_..._GRDH_...`."""
    with pytest.raises(RuntimeError, match="NO_SOURCE_FOR_SENSOR"):
        adapters.first_healthy(["planetary"], sensor="S2")
    assert adapters.sensor_of("planetary") == "S1"
    assert adapters.sensor_of("earthsearch") == "S2"


def test_pagination_never_leaks_another_collection():
    """Root cause of the production failure: the STAC `next` link is method=POST with
    the filters in its body. Re-issuing it without that body returns the unfiltered
    catalogue — S1 GRD scenes that fail on band B02, footprint, and bucket auth."""
    end = datetime.now(timezone.utc)
    a = adapters.get_adapter("earthsearch")
    # limit > one page (Earth Search pages at ~50) so pagination is exercised.
    scenes = a.search(AOI, end - timedelta(days=365), end, max_cloud=100, limit=180)
    assert len(scenes) > 60, f"only {len(scenes)} scenes — pagination stopped early"
    assert {s.collection for s in scenes} == {"sentinel-2-l2a"}
    assert not [s for s in scenes if s.scene_id.startswith("S1")]
