"""P1 adapter contract: windowed mixed-resolution COGs become one xarray grid."""
from datetime import datetime, timezone

import numpy as np
import rasterio
import xarray as xr
from affine import Affine

from snitch.adapters import SceneRef, StacAdapter, as_array_dict


AOI = {"type": "Polygon", "coordinates": [[
    [2.9991, 0.0002], [3.0010, 0.0002], [3.0010, 0.0020],
    [2.9991, 0.0020], [2.9991, 0.0002],
]]}


def _tif(path, value, resolution, size):
    transform = Affine(resolution, 0, 499800, 0, -resolution, 400)
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=1,
                       dtype="uint16", crs="EPSG:32631", transform=transform,
                       nodata=0) as dst:
        dst.write(np.full((1, size, size), value, "uint16"))


def _scene(red, swir, date="2025-06-01T10:00:00Z"):
    return SceneRef(
        scene_id=f"S2_{date[:10]}", platform="S2A", datetime=date,
        collection="sentinel-2-l2a", adapter="earthsearch",
        relative_orbit=None, pass_direction=None, source_uri="fixture",
        item={"id": f"S2_{date[:10]}", "collection": "sentinel-2-l2a",
              "properties": {"datetime": date, "platform": "S2A",
                             "s2:mean_solar_azimuth": 140.0,
                             "s2:mean_solar_zenith": 30.0},
              "assets": {"red": {"href": str(red)},
                         "swir16": {"href": str(swir)}}})


def test_load_returns_time_y_x_dataset_on_one_analysis_grid(tmp_path):
    red, swir = tmp_path / "red.tif", tmp_path / "swir.tif"
    _tif(red, 2000, 10, 60)
    _tif(swir, 4000, 20, 30)
    adapter = StacAdapter("earthsearch")
    ds = adapter.load(_scene(red, swir), AOI, ["B04", "B11"], resolution=10,
                      target_crs="EPSG:32631")
    assert isinstance(ds, xr.Dataset)
    assert ds.sizes["time"] == 1 and ds.sizes["x"] > 10 and ds.sizes["y"] > 10
    assert ds["B04"].shape == ds["B11"].shape
    assert np.isclose(float(ds["B04"].median()), 0.2, atol=1e-3)
    assert np.isclose(float(ds["B11"].median()), 0.4, atol=1e-3)
    assert ds.attrs["crs"] == "EPSG:32631"

    arrays = as_array_dict(ds)
    assert arrays["B04"].ndim == 2
    assert arrays["_solar_azimuth"] == 140.0


def test_buffer_expands_the_window_and_multiple_scenes_stack_in_time(tmp_path):
    red, swir = tmp_path / "red.tif", tmp_path / "swir.tif"
    _tif(red, 2000, 10, 60)
    _tif(swir, 4000, 20, 30)
    adapter = StacAdapter("earthsearch")
    scene = _scene(red, swir)
    plain = adapter.load(scene, AOI, ["B04"], resolution=10,
                         target_crs="EPSG:32631")
    buffered = adapter.load(scene, AOI, ["B04"], resolution=10,
                            target_crs="EPSG:32631", buffer_px=3)
    assert buffered.sizes["x"] == plain.sizes["x"] + 6
    assert buffered.sizes["y"] == plain.sizes["y"] + 6

    second = _scene(red, swir, "2025-06-11T10:00:00Z")
    stacked = adapter.load([scene, second], AOI, ["B04"], resolution=10,
                           target_crs="EPSG:32631")
    assert stacked.sizes["time"] == 2
    assert str(stacked.time.values[0])[:10] == "2025-06-01"
