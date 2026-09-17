"""A project may watch one AOI with several methodologies at once."""
import json

import pytest
from fastapi.testclient import TestClient

from snitch import db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SNITCH_DATA_DIR", str(tmp_path))
    from snitch import config, db as _db
    config.config.cache_clear()
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "t.db"))
    from snitch import api, scheduler
    monkeypatch.setattr(scheduler, "start", lambda: type("S", (), {
        "add_job": lambda *a, **k: None, "remove_job": lambda *a, **k: None,
        "get_job": lambda *a, **k: None})())
    with TestClient(api.app) as c:
        yield c


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setenv("SNITCH_DATA_DIR", str(tmp_path))
    from snitch import config, db as _db
    config.config.cache_clear()
    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "t.db"))
    c = _db.connect(str(tmp_path / "t.db"))
    yield c
    c.close()

AOI = {"type": "Polygon", "coordinates": [[
    [5.60, 52.46], [5.82, 52.46], [5.82, 52.56], [5.60, 52.56], [5.60, 52.46]]]}


def _create(client, **kw):
    body = {"name": "multi", "aoi_geojson": AOI, **kw}
    r = client.post("/api/v1/projects", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_project_can_be_created_with_several_recipes(client):
    p = _create(client, recipe_ids=["vegetation_loss_optical", "water_extent_optical"])
    ids = [m["recipe_id"] for m in p["methodologies"]]
    assert ids == ["vegetation_loss_optical", "water_extent_optical"]
    # the primary is mirrored onto the project row for single-methodology clients
    assert p["recipe_id"] == "vegetation_loss_optical"


def test_each_methodology_keeps_its_own_parameters(client):
    p = _create(client, recipe_ids=["vegetation_loss_optical", "water_extent_optical"])
    a, b = p["methodologies"]
    client.post(f"/api/v1/projects/{p['uuid']}/threshold?methodology={b['id']}",
                json={"threshold": 9.5, "source": "calibrated"})
    ms = client.get(f"/api/v1/projects/{p['uuid']}/methodologies").json()["items"]
    by_id = {m["id"]: m for m in ms}
    assert by_id[b["id"]]["params"]["threshold"] == 9.5
    assert by_id[a["id"]]["params"].get("threshold") != 9.5


def test_a_second_methodology_can_be_added_and_removed(client):
    p = _create(client, recipe_id="vegetation_loss_optical")
    added = client.post(f"/api/v1/projects/{p['uuid']}/methodologies",
                        json={"recipe_id": "water_extent_optical"})
    assert added.status_code == 201, added.text
    assert len(client.get(f"/api/v1/projects/{p['uuid']}").json()["methodologies"]) == 2
    dup = client.post(f"/api/v1/projects/{p['uuid']}/methodologies",
                      json={"recipe_id": "water_extent_optical"})
    assert dup.status_code == 409
    gone = client.delete(
        f"/api/v1/projects/{p['uuid']}/methodologies/{added.json()['id']}")
    assert gone.status_code == 204
    assert len(client.get(f"/api/v1/projects/{p['uuid']}").json()["methodologies"]) == 1


def test_the_last_methodology_cannot_be_removed(client):
    p = _create(client, recipe_id="vegetation_loss_optical")
    only = p["methodologies"][0]["id"]
    r = client.delete(f"/api/v1/projects/{p['uuid']}/methodologies/{only}")
    assert r.status_code == 400
    assert "at least one" in r.json()["detail"]


def test_a_radar_recipe_is_refused_when_the_sources_cannot_serve_it(client):
    """The optical default preference cannot load S1, and silently falling back to a
    radar source is what produced the `band B02 not in S1..._GRDH` failures."""
    # without a relative orbit, radar is refused on geometry grounds first
    p = _create(client, recipe_id="vegetation_loss_optical")
    r = client.post(f"/api/v1/projects/{p['uuid']}/methodologies",
                    json={"recipe_id": "flood_radar"})
    assert r.status_code == 400
    assert "relative orbit" in r.json()["detail"]

    # with an orbit set, it is the optical-only source list that refuses it
    p2 = _create(client, recipe_id="vegetation_loss_optical", s1_relative_orbit=88)
    r2 = client.post(f"/api/v1/projects/{p2['uuid']}/methodologies",
                     json={"recipe_id": "flood_radar"})
    assert r2.status_code == 400
    assert "S1" in r2.json()["detail"]


def test_mixing_sensors_requires_a_preference_that_serves_both(client):
    r = client.post("/api/v1/projects", json={
        "name": "mixed", "aoi_geojson": AOI, "s1_relative_orbit": 88,
        "recipe_ids": ["vegetation_loss_optical", "flood_radar"],
        "adapter_preference": ["earthsearch"]})
    assert r.status_code == 400
    assert "S1" in r.json()["detail"]

    ok = client.post("/api/v1/projects", json={
        "name": "mixed", "aoi_geojson": AOI, "s1_relative_orbit": 88,
        "recipe_ids": ["vegetation_loss_optical", "flood_radar"],
        "adapter_preference": ["earthsearch", "planetary"]})
    assert ok.status_code == 201, ok.text
    assert len(ok.json()["methodologies"]) == 2


def test_existing_single_recipe_projects_adopt_a_methodology(con):
    """A row written before the table existed must keep working without a migration
    step, because the baseline and threshold live on the methodology now."""
    t = db.now()
    pid = con.execute(
        "INSERT INTO project(uuid,name,aoi_geojson,aoi_area_km2,analysis_crs,"
        "recipe_id,recipe_version,params_json,adapter_preference,schedule_cron,"
        "status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,'draft',?,?)",
        (db.new_uuid(), "legacy", json.dumps(AOI), 1.0, "EPSG:32631",
         "vegetation_loss_optical", "1.0", json.dumps({"threshold": 3.3}),
         json.dumps(["earthsearch"]), "0 3 * * *", t, t)).lastrowid
    rows = db.ensure_methodologies(con, pid)
    assert len(rows) == 1
    assert rows[0]["recipe_id"] == "vegetation_loss_optical"
    assert json.loads(rows[0]["params_json"])["threshold"] == 3.3
    # idempotent: reading again must not create a second one
    assert len(db.ensure_methodologies(con, pid)) == 1
