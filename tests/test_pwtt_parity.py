"""The PWTT lane must return the same numbers as the reference PWTT app.

Both apps are pointed at one AOI and one pair of dates; every value in the summary
must match exactly. The test needs Earth Engine credentials and a checkout of the
reference app (PWTT_APP_PATH, defaulting to a sibling directory), so it skips
rather than fails where those are absent.
"""
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("pwtt")
ee = pytest.importorskip("ee")

APP = Path(os.environ.get("PWTT_APP_PATH",
                          Path(__file__).resolve().parents[2] / "PWTT"))

# A few blocks of Mariupol: small enough to be a cheap Earth Engine request, damaged
# enough that the summary is not all zeros.
AOI = {"type": "Polygon", "coordinates": [[[37.52, 47.09], [37.56, 47.09],
                                           [37.56, 47.11], [37.52, 47.11],
                                           [37.52, 47.09]]]}
PARAMS = {"war_start": "2022-02-24", "inference_start": "2022-07-01",
          "pre_interval": 12.0, "post_interval": 2.0, "threshold": 3.3}


@pytest.mark.skipif(not (APP / "backend/app/services/pipeline_service.py").exists(),
                    reason="reference PWTT app not available")
def test_snitch_matches_the_reference_app():
    from snitch import pwtt as lane
    try:
        lane._init()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Earth Engine is not usable here: {e}")

    ours = lane.assess(AOI, **PARAMS)

    sys.path.insert(0, str(APP))
    from backend.app.services import pipeline_service as reference

    aoi = ee.Geometry(AOI)
    theirs = reference._compute_summary(reference.run_pwtt_image(aoi, PARAMS), aoi)
    theirs["coverage"] = reference.check_sentinel1_coverage(aoi, PARAMS)

    assert {k: ours[k] for k in theirs} == theirs


def test_a_window_reaching_back_before_the_conflict_is_refused_not_scored():
    """Offline guard: a post-event window that starts before the conflict would
    compare the reference period against itself."""
    import numpy as np
    from snitch.detectors import REGISTRY

    target = {"VV": np.zeros((4, 4), "float32"), "VH": np.zeros((4, 4), "float32"),
              "_crs": "EPSG:32637", "_transform": None}
    result = REGISTRY["pwtt_damage"].score(
        0.0, target, None, {"_aoi": AOI, "_sensed_at": "2022-03-01T00:00:00Z",
                            "war_start": "2022-02-24"})
    assert not result.valid.any()
    assert np.isnan(result.score).all()
    assert "before the conflict date" in result.notes[0]


def test_pwtt_runs_as_an_ordinary_methodology(tmp_path, monkeypatch):
    """End to end through the normal pipeline: a project on this recipe, one real
    acquisition, one scored run — the same path every other methodology takes."""
    from datetime import datetime, timedelta, timezone

    from snitch import adapters, db, pipeline
    from snitch.detectors import REGISTRY
    from snitch import pwtt as lane

    try:
        lane._init()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Earth Engine is not usable here: {e}")

    assert REGISTRY["pwtt_damage"].spec.needs_baseline is False, \
        "a baseline job must never be scheduled for this method"

    end = datetime(2022, 9, 1, tzinfo=timezone.utc)
    adapter = adapters.get_adapter("planetary")
    scenes = adapter.search(AOI, end - timedelta(days=20), end,
                            collection=adapters.RTC_COLLECTION, max_cloud=100)
    # Same admission rule the poller applies: a scene that barely clips the AOI is
    # not a scene this project can be scored on.
    from shapely.geometry import shape
    aoi_shape = shape(AOI)
    scenes = [s for s in scenes
              if shape(s.item["geometry"]).intersection(aoi_shape).area
              / aoi_shape.area >= 0.99]
    if not scenes:
        pytest.skip("no Sentinel-1 RTC scene covering the AOI in the test window")
    scene = max(scenes, key=lambda s: s.datetime)

    monkeypatch.setenv("SNITCH_DATA_DIR", str(tmp_path))
    from snitch import config
    config.config.cache_clear()
    con = db.connect(str(tmp_path / "t.db"))
    project_id = con.execute(
        "INSERT INTO project(uuid,name,aoi_geojson,aoi_area_km2,analysis_crs,recipe_id,"
        "recipe_version,params_json,adapter_preference,s1_relative_orbit,"
        "s1_pass_direction,schedule_cron,status,created_at,updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'active',?,?)",
        (db.new_uuid(), "pwtt", __import__("json").dumps(AOI), 8.0, "EPSG:32637",
         "battle_damage_radar", "1.0",
         __import__("json").dumps({"war_start": "2022-02-24", "threshold": 3.3,
                                   "min_mapping_unit_m2": 300,
                                   "consecutive_confirmations": 1}),
         '["planetary"]', scene.relative_orbit, scene.pass_direction, "0 3 * * *",
         db.now(), db.now())).lastrowid
    methodology = db.ensure_methodologies(con, project_id)[0]

    out = pipeline.run_scene(con, None, {
        "project_id": project_id, "methodology_id": methodology["id"],
        "scene_id": scene.scene_id, "adapter": "planetary", "item": scene.item,
        "kind": "forward"})
    assert "deferred" not in out, "this method must not wait on a baseline"
    assert "gated" not in out, ("the scene was gated: " + str(
        [dict(r) for r in con.execute("SELECT code,message FROM diagnostic")]))

    run = con.execute("SELECT * FROM run WHERE project_id=?", (project_id,)).fetchone()
    assert run["status"] == "ok", run["error_json"]
    assert run["detector_id"] == "pwtt_damage"
    assert run["compute_backend"] == "gee"
    summary = __import__("json").loads(run["summary_json"])
    assert summary["units"] == "t_statistic"
    assert summary["valid_pixels"] > 0
    # the Earth Engine work is billed to the same ledger the GEE adapter uses
    assert con.execute("SELECT COUNT(*) n FROM gee_usage WHERE kind='pwtt_run'"
                       ).fetchone()["n"] == 1
