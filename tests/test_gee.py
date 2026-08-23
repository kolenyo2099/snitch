"""The optional GEE lane: toggle, quota refusal, the xee bridge, batch export,
local persistence, and the run marking. No network, no earthengine-api install:
`ee` and `xee` are stand-ins injected into sys.modules, which is possible only
because the real ones are imported lazily.
"""
import json
import sys
import types
from datetime import datetime, timezone

import numpy as np
import pytest
import xarray as xr
from affine import Affine

from terrawatch import config, db, gee

AOI = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.02, 0.0], [0.02, 0.02],
                                           [0.0, 0.02], [0.0, 0.0]]]}
SA = json.dumps({"client_email": "tw@example.invalid", "private_key": "x"})


@pytest.fixture
def con(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    c.execute("INSERT INTO project(id,uuid,name,aoi_geojson,aoi_area_km2,analysis_crs,"
              "recipe_id,recipe_version,params_json,adapter_preference,schedule_cron,"
              "status,created_at,updated_at) VALUES (1,'u','p',?,4.0,'EPSG:32631',"
              "'r1','1','{}','[\"earthsearch\"]','0 3 * * *','draft',?,?)",
              (json.dumps(AOI), db.now(), db.now()))
    return c


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("TW_ADAPTERS__GEE__ENABLED", "true")
    monkeypatch.setenv("TW_ADAPTERS__GEE__PROJECT_ID", "tw-test")
    monkeypatch.setenv("GEE_SERVICE_ACCOUNT_JSON", SA)
    config.config.cache_clear()
    yield
    config.config.cache_clear()


# --- fake earthengine ------------------------------------------------------

class _Img:
    def __init__(self, tag="img"):
        self.tag = tag

    def select(self, *a):
        return self

    def addBands(self, other):
        return self

    def get(self, key):
        return _Img(key)

    def toBands(self):
        return self


class _Coll(_Img):
    def __init__(self):
        super().__init__("coll")
        self.calls = []

    def _note(self, what):
        self.calls.append(what)
        return self

    filterBounds = lambda self, g: self._note("bounds")       # noqa: E731
    filterDate = lambda self, a, b: self._note(("date", a, b))  # noqa: E731
    filter = lambda self, f: self._note("filter")             # noqa: E731
    limit = lambda self, n: self._note(("limit", n))          # noqa: E731
    map = lambda self, fn: self._note("map")                  # noqa: E731

    def select(self, src, dst=None):
        return self._note(("select", src, dst))

    def getInfo(self):
        return {"features": [
            {"id": f"COPERNICUS/S2_SR_HARMONIZED/20240{i}01T000000_X",
             "geometry": AOI,
             "properties": {"system:time_start": int(datetime(2024, i, 1,
                                                     tzinfo=timezone.utc).timestamp() * 1000),
                            "SPACECRAFT_NAME": "Sentinel-2A",
                            "CLOUDY_PIXEL_PERCENTAGE": 4.0,
                            "SENSING_ORBIT_NUMBER": 22,
                            "SENSING_ORBIT_DIRECTION": "descending",
                            "MEAN_SOLAR_AZIMUTH_ANGLE": 150.0,
                            "MEAN_SOLAR_ZENITH_ANGLE": 30.0}}
            for i in (1, 2)]}


class _Task:
    started = []

    def __init__(self, kwargs):
        self.kwargs = kwargs

    def start(self):
        _Task.started.append(self.kwargs)

    def status(self):
        return {"state": "COMPLETED"}


def _fake_ee(monkeypatch):
    ee = types.ModuleType("ee")
    ee.ImageCollection = lambda x=None: x if isinstance(x, _Coll) else _Coll()
    ee.Image = lambda x: _Img()
    ee.Geometry = lambda aoi: types.SimpleNamespace(buffer=lambda d: "geom")
    ee.Filter = types.SimpleNamespace(lt=lambda *a: "f", inList=lambda *a: "f",
                                      equals=lambda **k: "f")
    ee.Join = types.SimpleNamespace(saveFirst=lambda n: types.SimpleNamespace(
        apply=lambda a, b, c: _Coll()))
    ee.ServiceAccountCredentials = lambda *a, **k: "creds"
    ee.Initialize = lambda *a, **k: None
    ee.batch = types.SimpleNamespace(Export=types.SimpleNamespace(
        image=types.SimpleNamespace(toCloudStorage=lambda **kw: _Task(kw))))
    monkeypatch.setitem(sys.modules, "ee", ee)
    monkeypatch.setitem(sys.modules, "xee", types.ModuleType("xee"))
    return ee


def _fake_open_dataset(monkeypatch, shape=(2, 4, 5)):
    """xee hands back lon/lat dims and raw DN; the adapter must normalise both."""
    def open_dataset(source, engine=None, geometry=None, crs=None, scale=None, **kw):
        t, ny, nx = shape
        data = np.arange(t * ny * nx, dtype="float32").reshape(t, ny, nx) * 100
        return xr.Dataset({"B04": (("time", "lat", "lon"), data),
                           "cs": (("time", "lat", "lon"), np.full(shape, 0.9, "float32"))},
                          coords={"time": np.arange(t),
                                  "lat": np.arange(ny, dtype=float) * 10.0,
                                  "lon": np.arange(nx, dtype=float) * 10.0})
    monkeypatch.setattr(xr, "open_dataset", open_dataset)


# --- toggle ----------------------------------------------------------------

def test_off_by_default():
    config.config.cache_clear()
    assert gee.configured() is False
    assert gee.adapter_for(None, {"id": 1, "params_json": '{"gee_enabled": true}'},
                           "baseline") is None


def test_project_toggle_needs_both(enabled):
    assert gee.configured() is True
    assert gee.project_enabled({"params_json": "{}"}) is False
    assert gee.project_enabled({"params_json": '{"gee_enabled": true}'}) is True


def test_forward_monitoring_never_uses_gee(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    p = {"id": 1, "params_json": '{"gee_enabled": true}', "aoi_area_km2": 4.0}
    assert gee.adapter_for(con, p, "forward") is None
    assert gee.adapter_for(con, p, "baseline") is not None


# --- quota -----------------------------------------------------------------

def test_estimate_scales_with_work():
    small = gee.estimate_eecu(area_km2=5, n_scenes=10, n_bands=4)
    assert small > 0
    assert gee.estimate_eecu(5, 20, 4) == pytest.approx(2 * small)


def test_refuses_above_remaining_budget(con, enabled):
    gee.record(con, 1, "load", 99.5)
    with pytest.raises(gee.QuotaExceeded):
        gee.check_budget(con, 5.0, project_id=1)
    codes = [r["code"] for r in con.execute("SELECT code FROM diagnostic")]
    assert codes == ["QUOTA_EXHAUSTED"]
    assert con.execute("SELECT COUNT(*) n FROM gee_usage").fetchone()["n"] == 1


def test_quota_low_warns_but_allows(con, enabled, monkeypatch):
    monkeypatch.setenv("TW_ADAPTERS__GEE__DAILY_EECU_CAP", "1000")
    config.config.cache_clear()
    gee.record(con, 1, "load", 85.0)
    gee.check_budget(con, 1.0, project_id=1)
    row = con.execute("SELECT code, severity FROM diagnostic").fetchone()
    assert (row["code"], row["severity"]) == ("QUOTA_LOW", "warning")


def test_daily_cap_refuses_before_monthly(con, enabled):
    gee.record(con, 1, "load", 9.0)          # under the 100 monthly, over the 10 daily
    with pytest.raises(gee.QuotaExceeded, match="daily"):
        gee.check_budget(con, 2.0, project_id=1)


def test_adapter_for_falls_back_to_local_on_quota(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    gee.record(con, 1, "load", 100.0)
    assert gee.adapter_for(con, {"id": 1, "params_json": '{"gee_enabled": true}',
                                 "aoi_area_km2": 4.0}, "baseline") is None


# --- the bridge ------------------------------------------------------------

def test_search_returns_sceneref(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    scenes = gee.GeeAdapter(con, 1).search(AOI, datetime(2024, 1, 1, tzinfo=timezone.utc),
                                           datetime(2024, 3, 1, tzinfo=timezone.utc))
    assert [s.adapter for s in scenes] == ["gee", "gee"]
    assert scenes[0].relative_orbit == 22 and scenes[0].pass_direction == "DESCENDING"
    assert scenes[0].datetime.endswith("Z") and scenes[0].cloud_cover == 4.0


def test_load_matches_the_local_convention(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    _fake_open_dataset(monkeypatch)
    a = gee.GeeAdapter(con, 1)
    scenes = a.search(AOI, datetime(2024, 1, 1, tzinfo=timezone.utc),
                      datetime(2024, 3, 1, tzinfo=timezone.utc))
    ds = a.load(scenes, AOI, ["B04", "cs"], resolution=10.0, target_crs="EPSG:32631")
    assert ds.sizes["time"] == 2 and ("y", "x") == ds["B04"].dims[1:]
    assert ds.attrs["crs"] == "EPSG:32631" and isinstance(ds.attrs["transform"], Affine)
    assert ds.attrs["solar_zenith"] == 30.0
    # reflectance is rescaled, Cloud Score+ is not
    assert float(ds["B04"].max()) < 1.0 and float(ds["cs"].max()) == pytest.approx(0.9)
    # the same normaliser the local adapter feeds detectors with
    from terrawatch.adapters import as_array_dict
    arrays = as_array_dict(ds)
    assert set(arrays) >= {"B04", "cs", "_crs", "_transform"}
    prov = ds.attrs["gee"]
    assert prov["algorithm_version"] == gee.ALGORITHM_VERSION and len(prov["asset_ids"]) == 2
    assert prov["cloud_score_plus"] == gee.CS_PLUS and prov["mode"] == "xee"
    assert con.execute("SELECT COUNT(*) n FROM gee_usage").fetchone()["n"] == 1


def test_substantial_work_uses_batch_export_not_download(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    monkeypatch.setenv("TW_ADAPTERS__GEE__BATCH_EXPORT_EECU", "0.0")
    monkeypatch.setenv("TW_ADAPTERS__GEE__EXPORT_BUCKET", "tw-bucket")
    config.config.cache_clear()
    _Task.started.clear()
    monkeypatch.setattr(xr, "open_dataset", lambda *a, **k: xr.Dataset(
        {"B04": (("time", "y", "x"), np.zeros((1, 3, 3), "float32"))},
        coords={"time": [0], "y": [0.0, 10.0, 20.0], "x": [0.0, 10.0, 20.0]}))
    monkeypatch.setitem(sys.modules, "rioxarray", types.ModuleType("rioxarray"))
    a = gee.GeeAdapter(con, 1)
    scenes = a.search(AOI, datetime(2024, 1, 1, tzinfo=timezone.utc),
                      datetime(2024, 3, 1, tzinfo=timezone.utc))
    ds = a.load(scenes[:1], AOI, ["B04"], resolution=10.0, target_crs="EPSG:32631")
    assert ds.attrs["gee"]["mode"] == "batch_export"
    assert _Task.started and _Task.started[0]["bucket"] == "tw-bucket"


def test_batch_export_without_a_bucket_refuses(con, enabled, monkeypatch):
    _fake_ee(monkeypatch)
    monkeypatch.setenv("TW_ADAPTERS__GEE__BATCH_EXPORT_EECU", "0.0")
    config.config.cache_clear()
    a = gee.GeeAdapter(con, 1)
    scenes = a.search(AOI, datetime(2024, 1, 1, tzinfo=timezone.utc),
                      datetime(2024, 3, 1, tzinfo=timezone.utc))
    with pytest.raises(gee.QuotaExceeded, match="export_bucket"):
        a.load(scenes[:1], AOI, ["B04"], resolution=10.0)


# --- local persistence -----------------------------------------------------

def test_everything_computed_is_stored_locally(con, enabled):
    data = {"B04": np.ones((64, 64), "float32"), "_crs": "EPSG:32631",
            "_transform": Affine(10, 0, 500000, 0, -10, 100000)}
    out = gee.persist(con, data, 1, "baseline",
                      {"asset_ids": ["COPERNICUS/S2_SR_HARMONIZED/a"],
                       "dates": ["2024-01-01"], "script": "harmonic fit"})
    row = con.execute("SELECT media_type, path, meta_json FROM artifact WHERE id=?",
                      (out["sidecar_artifact_id"],)).fetchone()
    assert row["media_type"] == "application/json"
    side = json.loads(open(row["path"]).read())
    assert side["compute_backend"] == "gee"
    assert side["algorithm_version"] == gee.ALGORITHM_VERSION
    assert side["asset_ids"] and side["dates"] and side["script"]
    cog = con.execute("SELECT media_type FROM artifact WHERE id=?",
                      (out["bands"]["B04"],)).fetchone()
    assert cog["media_type"].startswith("image/tiff")
