"""End-to-end spine: discover -> gate -> baseline -> score -> alert -> export.

Runs entirely against a synthetic adapter, so it is a regression suite that needs
no network. Real cached-scene golden fixtures land in P8.
"""
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from affine import Affine

from terrawatch import adapters, artifacts, db, geo, worker
from terrawatch.adapters import SceneRef

AOI = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.02, 0.0], [0.02, 0.02],
                                           [0.0, 0.02], [0.0, 0.0]]]}
SHAPE = (40, 40)
TRANSFORM = Affine(10.0, 0, 500000, 0, -10.0, 100000)
CRS = "EPSG:32631"
CLEAR = slice(10, 26)


class FakeAdapter:
    """Two years of clean history, then a clearing, then a fully clouded scene."""
    name = "fake"
    event_date = None
    cloudy_date = None

    def __init__(self, name="fake"):
        self.name = name

    def health(self):
        return {"adapter": self.name, "ok": True}

    def search(self, aoi, start, end, collection=None, limit=500, max_cloud=90.0):
        out = []
        d = datetime(2023, 1, 5, tzinfo=timezone.utc)
        while d < datetime.now(timezone.utc):
            if start <= d <= end:
                out.append(self._scene(d))
            d += timedelta(days=10)
        return out[:limit]

    def _scene(self, d):
        iso = d.isoformat().replace("+00:00", "Z")
        return SceneRef(scene_id=f"S2_{iso[:10]}", platform="S2A", datetime=iso,
                        collection="sentinel-2-l2a", adapter=self.name,
                        relative_orbit=22, pass_direction=None,
                        source_uri=f"https://example.invalid/{iso[:10]}",
                        item={"id": f"S2_{iso[:10]}", "collection": "sentinel-2-l2a",
                              "geometry": {"type": "Polygon", "coordinates":
                                           [[[-1, -1], [1, -1], [1, 1], [-1, 1],
                                             [-1, -1]]]},
                              "properties": {"datetime": iso, "platform": "S2A",
                                             "eo:cloud_cover": 5.0},
                              "links": [{"href": f"https://example.invalid/{iso[:10]}"}],
                              "assets": {}})

    def load(self, scene, aoi, bands):
        day = (datetime.fromisoformat(scene.datetime.replace("Z", "+00:00"))
               - datetime(2015, 1, 1, tzinfo=timezone.utc)).days
        rng = np.random.default_rng(day)
        ndvi = np.full(SHAPE, 0.7 + 0.15 * np.sin(2 * np.pi * day / 365.25)) \
            + rng.normal(0, 0.01, SHAPE)
        scl = np.zeros(SHAPE, "float32")
        if self.event_date and scene.datetime[:10] >= self.event_date:
            ndvi[CLEAR, CLEAR] -= 0.5
        if self.cloudy_date and scene.datetime[:10] == self.cloudy_date:
            scl[:] = 9
        b08 = np.full(SHAPE, 0.3, "float32")
        b04 = (b08 * (1 - ndvi) / (1 + ndvi)).astype("float32")
        data = {"B03": np.full(SHAPE, 0.1, "float32"), "B04": b04, "B08": b08,
                "B11": np.full(SHAPE, 0.25, "float32"), "B12": b04.copy(),
                "SCL": scl}
        data["_crs"], data["_transform"] = CRS, TRANSFORM
        return {k: data[k] for k in bands} | {"_crs": CRS, "_transform": TRANSFORM}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TW_DATA_DIR", str(tmp_path))
    from terrawatch import config
    config.config.cache_clear()
    fake = FakeAdapter()
    monkeypatch.setattr(adapters, "first_healthy", lambda prefs, sensor=None: fake)
    monkeypatch.setattr(adapters, "get_adapter", lambda name: fake)
    from terrawatch import pipeline
    monkeypatch.setattr(pipeline.adapters, "first_healthy", lambda prefs, sensor=None: fake)
    monkeypatch.setattr(pipeline.adapters, "get_adapter", lambda name: fake)
    monkeypatch.setattr(pipeline, "_render_chips",
                        lambda *a, **k: {"before": None, "after": None,
                                         "overlay": None})
    con = db.connect(str(tmp_path / "t.db"))
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    worker._load_handlers()
    yield con, fake, pipeline
    con.close()


def _project(con, **kw):
    now = db.now()
    pid = con.execute(
        "INSERT INTO project(uuid,name,aoi_geojson,aoi_area_km2,analysis_crs,recipe_id,"
        "recipe_version,params_json,adapter_preference,schedule_cron,status,created_at,"
        "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'active',?,?)",
        (db.new_uuid(), "test", json.dumps(AOI), geo.area_km2(AOI), CRS,
         "vegetation_loss_optical", "1.0",
         json.dumps({"threshold": 3.0, "min_mapping_unit_m2": 2000,
                     "consecutive_confirmations": 1, "harmonics": 2, **kw}),
         json.dumps(["fake"]), "0 3 */5 * *", now, now)).lastrowid
    return con.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone()


def _drain(con, limit=200):
    n = 0
    while worker.run_one(con) and n < limit:
        n += 1
    return n


def test_phase_correlation_correction_shifts_all_bands_without_edge_wrap():
    from scipy.ndimage import shift as nd_shift
    from terrawatch.pipeline import _coregister, _misregistration

    rng = np.random.default_rng(42)
    reference = rng.normal(size=(64, 64)).astype("float32")
    target = nd_shift(reference, (2.0, -3.0), order=1, mode="constant", cval=0)
    shift_yx, magnitude, estimable = _misregistration(reference, target)

    assert estimable
    assert shift_yx == pytest.approx((-2.0, 3.0), abs=0.3)
    assert magnitude == pytest.approx(np.hypot(2, 3), abs=0.3)

    data = {"B04": target.copy(), "SCL": np.ones((64, 64), "float32")}
    invalid = _coregister(data, np.zeros((64, 64), bool), shift_yx)
    interior = np.s_[5:-5, 5:-5]
    assert np.mean(np.abs(data["B04"][interior] - reference[interior])) < 0.03
    assert invalid[:3].any() or invalid[:, -4:].any()
    assert np.isnan(data["B04"][invalid]).all()


def test_saturation_reports_only_widespread_optical_clipping():
    from terrawatch.pipeline import _saturation

    data = {"B04": np.full((10, 10), 0.3), "B08": np.full((10, 10), 0.4),
            "VV": np.full((10, 10), 1.0)}
    data["B04"][:3] = 1.0
    fraction, bands = _saturation(data)
    assert fraction == pytest.approx(0.3)
    assert bands == ["B04"]

    data["B04"][:] = 0.3
    data["B04"][0, 0] = 1.0
    assert _saturation(data)[1] == []


def test_clearing_raises_one_explained_alert(env):
    con, fake, pipeline = env
    p = _project(con)
    fake.event_date = "2025-06-01"
    db.enqueue(con, "baseline", {"project_id": p["id"], "before": "2025-01-01"}, p["id"])
    _drain(con)
    assert con.execute("SELECT baseline_artifact_id FROM project_methodology"
                       " WHERE project_id=?",
                       (p["id"],)).fetchone()["baseline_artifact_id"]

    scene = fake._scene(datetime(2025, 6, 15, tzinfo=timezone.utc))
    pipeline.run_scene(con, None, {"project_id": p["id"], "scene_id": scene.scene_id,
                                   "adapter": "fake", "item": scene.item,
                                   "kind": "forward"})
    a = con.execute("SELECT * FROM alert").fetchone()
    assert a, "a 2.5 ha clearing must raise an alert"
    assert a["changed_area_m2"] > 20_000
    assert a["n_components"] == 1
    assert "hectares" in a["explanation_text"]
    assert "consistent with vegetation" in a["explanation_text"]
    assert json.loads(a["geometry_geojson"])["type"] == "MultiPolygon"

    run = con.execute("SELECT * FROM run WHERE id=?", (a["run_id"],)).fetchone()
    assert run["status"] == "ok" and run["compute_backend"] == "local"
    s = json.loads(run["summary_json"])
    assert {"score_p50", "score_p99", "changed_area_m2", "n_components",
            "largest_component_m2", "direction", "notes", "valid_fraction"} <= s.keys()


def test_quiet_scene_records_why_no_alert(env):
    con, fake, pipeline = env
    p = _project(con)
    db.enqueue(con, "baseline", {"project_id": p["id"], "before": "2025-01-01"}, p["id"])
    _drain(con)
    scene = fake._scene(datetime(2025, 6, 15, tzinfo=timezone.utc))
    out = pipeline.run_scene(con, None, {"project_id": p["id"],
                                         "scene_id": scene.scene_id, "adapter": "fake",
                                         "item": scene.item, "kind": "forward"})
    assert out["alert"] is None
    run = con.execute("SELECT * FROM run WHERE uuid=?", (out["run"],)).fetchone()
    assert run["skip_reason"] in ("below_threshold", "below_mmu")
    assert json.loads(run["summary_json"])["no_alert_reason_text"]
    assert con.execute("SELECT COUNT(*) n FROM alert").fetchone()["n"] == 0


def test_a_clouded_month_is_loud_and_produces_no_alert(env):
    con, fake, pipeline = env
    p = _project(con)
    db.enqueue(con, "baseline", {"project_id": p["id"], "before": "2025-01-01"}, p["id"])
    _drain(con)
    for i in range(3):
        d = datetime(2025, 6, 5 + i * 10, tzinfo=timezone.utc)
        fake.cloudy_date = d.isoformat()[:10]
        s = fake._scene(d)
        pipeline.run_scene(con, None, {"project_id": p["id"], "scene_id": s.scene_id,
                                       "adapter": "fake", "item": s.item,
                                       "kind": "forward"})
    codes = {r["code"] for r in con.execute("SELECT code FROM diagnostic")}
    assert "LOW_VALID_FRACTION" in codes
    assert "PERSISTENT_OCCLUSION" in codes
    assert con.execute("SELECT COUNT(*) n FROM alert").fetchone()["n"] == 0
    obs = con.execute("SELECT status FROM observation WHERE sensed_at LIKE '2025-06%'"
                      " AND status!='usable'").fetchall()
    assert obs, "gated dates must still be recorded as observations, not vanish"


def test_evidence_bundle_is_self_contained(env):
    con, fake, pipeline = env
    from terrawatch.export import build
    p = _project(con)
    fake.event_date = "2025-06-01"
    db.enqueue(con, "baseline", {"project_id": p["id"], "before": "2025-01-01"}, p["id"])
    _drain(con)
    s = fake._scene(datetime(2025, 6, 15, tzinfo=timezone.utc))
    pipeline.run_scene(con, None, {"project_id": p["id"], "scene_id": s.scene_id,
                                   "adapter": "fake", "item": s.item, "kind": "forward"})
    a = con.execute("SELECT uuid FROM alert").fetchone()["uuid"]

    import zipfile, hashlib
    path = build(con, alert_uuid=a)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        assert {"manifest.json", "method.md", "runs.json", "alerts.json",
                "observations.json", "diagnostics.json", "sources.json"} <= names
        assert any(n.endswith("_score.tif") for n in names)
        manifest = json.loads(z.read("manifest.json"))
        for name, meta in manifest["files"].items():
            assert hashlib.sha256(z.read(name)).hexdigest() == meta["sha256"]
        method = z.read("method.md").decode()
        assert "doi.org/10.1016/j.rse.2014.01.011" in method
        assert "Cannot distinguish harvest from illegal clearing." in method
        assert "Threshold and how it was chosen" in method


def test_properties_hold(env):
    con, fake, pipeline = env
    import rasterio, io
    p = _project(con)
    fake.event_date = "2025-06-01"
    db.enqueue(con, "baseline", {"project_id": p["id"], "before": "2025-01-01"}, p["id"])
    _drain(con)
    s = fake._scene(datetime(2025, 6, 15, tzinfo=timezone.utc))
    pipeline.run_scene(con, None, {"project_id": p["id"], "scene_id": s.scene_id,
                                   "adapter": "fake", "item": s.item, "kind": "forward"})
    run = con.execute("SELECT * FROM run WHERE status='ok'").fetchone()

    # every alert resolves to a run, an observation, and at least one artifact
    for a in con.execute("SELECT * FROM alert"):
        r = con.execute("SELECT * FROM run WHERE id=?", (a["run_id"],)).fetchone()
        assert r and r["target_observation_id"]
        assert con.execute("SELECT COUNT(*) n FROM observation WHERE id=?",
                           (r["target_observation_id"],)).fetchone()["n"] == 1
        assert r["score_raster_id"] and r["mask_raster_id"]

    # no score values where the mask says invalid
    with rasterio.open(io.BytesIO(artifacts.read(con, run["score_raster_id"]))) as src:
        score = src.read(1)
    with rasterio.open(io.BytesIO(artifacts.read(con, run["mask_raster_id"]))) as src:
        valid = src.read(1) > 0.5
    assert np.isnan(score[~valid]).all() or not (~valid).any()

    # runs are never mutated after finished_at
    assert run["finished_at"]
    before = dict(run)
    _drain(con)
    after = dict(con.execute("SELECT * FROM run WHERE id=?", (run["id"],)).fetchone())
    assert before == after


def test_gee_serves_baseline_and_backtest_but_never_forward(env, monkeypatch):
    """The whole point of the optional backend: it swaps in for the expensive,
    historical work, marks every run it served, and leaves forward monitoring local."""
    con, fake, pipeline = env
    gee_fake = FakeAdapter("gee")
    gee_fake.event_date = fake.event_date
    monkeypatch.setattr(pipeline.gee, "adapter_for",
                        lambda c, p, purpose: gee_fake if purpose in
                        ("baseline", "backtest") else None)
    monkeypatch.setattr(pipeline.gee, "GeeAdapter", lambda c, pid: gee_fake)
    p = _project(con)

    pipeline.backtest(con, None, {"project_id": p["id"], "years": 1})
    backtests = con.execute("SELECT compute_backend FROM run WHERE kind='backtest'"
                            ).fetchall()
    assert backtests and {r["compute_backend"] for r in backtests} == {"gee"}

    params = json.loads(con.execute(
        "SELECT params_json FROM project_methodology WHERE project_id=?",
        (p["id"],)).fetchone()["params_json"])
    sidecar = json.loads(artifacts.read(con, params["_gee_sidecar_artifact_id"]))
    assert sidecar["compute_backend"] == "gee" and sidecar["asset_ids"]
    assert sidecar["dates"] and sidecar["script"]

    scene = fake._scene(datetime(2025, 6, 15, tzinfo=timezone.utc))
    pipeline.run_scene(con, None, {"project_id": p["id"], "scene_id": scene.scene_id,
                                   "adapter": "fake", "item": scene.item,
                                   "kind": "forward"})
    forward = con.execute("SELECT compute_backend FROM run WHERE kind='forward'"
                          ).fetchone()
    assert forward["compute_backend"] == "local"
