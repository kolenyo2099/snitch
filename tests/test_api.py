"""API smoke: the surface exists, projects round-trip, health is honest."""
import json
import numpy as np
import pytest
from affine import Affine
from fastapi.testclient import TestClient

AOI = {"type": "Polygon", "coordinates": [[[0.0, 0.0], [0.02, 0.0], [0.02, 0.02],
                                           [0.0, 0.02], [0.0, 0.0]]]}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SNITCH_DATA_DIR", str(tmp_path))
    from snitch import config, db
    config.config.cache_clear()
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    from snitch import api, scheduler
    monkeypatch.setattr(scheduler, "start", lambda: type("S", (), {
        "add_job": lambda *a, **k: None, "remove_job": lambda *a, **k: None,
        "get_job": lambda *a, **k: None})())
    with TestClient(api.app) as c:
        yield c


def test_health_reports_credential_presence_never_values(client, monkeypatch):
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "hunter2")
    r = client.get("/api/v1/health").json()
    assert r["ok"] and "queue" in r and "storage" in r
    assert "hunter2" not in json.dumps(r)


def test_project_lifecycle(client):
    p = client.post("/api/v1/projects", json={
        "name": "Site A", "aoi_geojson": AOI,
        "recipe_id": "vegetation_loss_optical"}).json()
    assert p["status"] == "draft" and p["aoi_area_km2"] > 0
    assert p["analysis_crs"].startswith("EPSG:326")
    assert p["schedule_cron"]                      # derived from real revisit cadence

    uuid = p["uuid"]
    assert client.post(f"/api/v1/projects/{uuid}/activate").json()["status"] == "active"
    assert client.post(f"/api/v1/projects/{uuid}/pause").json()["status"] == "paused"
    assert client.get(f"/api/v1/projects/{uuid}/health").json()["status"] == "paused"
    assert client.delete(f"/api/v1/projects/{uuid}").status_code == 204
    assert client.get(f"/api/v1/projects/{uuid}").status_code == 404


def test_params_are_not_patchable_history_stays_interpretable(client):
    u = client.post("/api/v1/projects", json={
        "name": "x", "aoi_geojson": AOI,
        "recipe_id": "vegetation_loss_optical"}).json()["uuid"]
    r = client.patch(f"/api/v1/projects/{u}", json={"params": {"threshold": 9}})
    assert r.status_code == 400 and "reanalyse" in r.json()["detail"]


def test_threshold_records_its_provenance(client):
    u = client.post("/api/v1/projects", json={
        "name": "x", "aoi_geojson": AOI,
        "recipe_id": "vegetation_loss_optical"}).json()["uuid"]
    p = client.post(f"/api/v1/projects/{u}/threshold",
                    json={"threshold": 2.4, "source": "calibrated"}).json()
    prov = p["params"]["threshold_provenance"]
    assert p["params"]["threshold"] == 2.4 and prov["source"] == "calibrated"

    p = client.post(f"/api/v1/projects/{u}/threshold",
                    json={"threshold": 3.0, "source": "default"}).json()
    assert p["params"]["threshold_provenance"]["default_kind"] == "heuristic"


def test_registries_carry_references(client):
    recipes = client.get("/api/v1/recipes").json()
    assert recipes and all(r["reference"]["url"].startswith("http") for r in recipes)
    dets = client.get("/api/v1/detectors").json()
    assert all(d["threshold_semantics"] and d["score_units"] for d in dets)


def test_radar_recipe_requires_an_orbit(client, monkeypatch):
    from snitch.recipes import REGISTRY
    REGISTRY["_fake_s1"] = {**REGISTRY["vegetation_loss_optical"],
                            "id": "_fake_s1", "sensor": "S1"}
    r = client.post("/api/v1/projects", json={"name": "x", "aoi_geojson": AOI,
                                              "recipe_id": "_fake_s1"})
    assert r.status_code == 400 and "relative orbit" in r.json()["detail"]
    REGISTRY.pop("_fake_s1")


def test_dual_sensor_recipe_requires_and_records_its_radar_orbit(client):
    body = {"name": "works", "aoi_geojson": AOI,
            "recipe_id": "construction_optical"}
    refused = client.post("/api/v1/projects", json=body)
    assert refused.status_code == 400 and "relative orbit" in refused.json()["detail"]
    created = client.post("/api/v1/projects", json={
        **body, "s1_relative_orbit": 42, "s1_pass_direction": "DESCENDING",
    }).json()
    assert created["s1_relative_orbit"] == 42
    assert created["s1_pass_direction"] == "DESCENDING"


def test_json_columns_are_decoded_for_clients(client):
    p = client.post("/api/v1/projects", json={
        "name": "x", "aoi_geojson": AOI,
        "recipe_id": "vegetation_loss_optical"}).json()
    assert p["aoi_geojson"]["type"] == "Polygon"
    assert p["aoi"]["type"] == "Polygon"
    assert isinstance(p["adapter_preference"], list)
    assert isinstance(p["params"], dict) and "threshold" in p["params"]


def test_mask_artifact_has_browser_renderable_overlay(client):
    from snitch import artifacts, db
    con = db.connect()
    aid = artifacts.put_cog(con, np.pad(np.ones((4, 4)), 2), "EPSG:4326",
                            Affine(0.01, 0, -1, 0, -0.01, 1))
    con.close()
    meta = client.get(f"/api/v1/artifacts/{aid}/overlay").json()
    assert len(meta["coordinates"]) == 4
    image = client.get(f"/api/v1/artifacts/{aid}/overlay.png")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")


def test_acknowledged_diagnostic_leaves_dashboard_feed(client):
    from snitch import db
    con = db.connect()
    db.diagnostic(con, "RUN_FAILED", "error", "fixture failure")
    diag_id = con.execute("SELECT id FROM diagnostic ORDER BY id DESC").fetchone()["id"]
    con.close()
    assert any(item.get("id") == diag_id for item in client.get("/api/v1/feed").json()["items"])
    client.post(f"/api/v1/diagnostics/{diag_id}/acknowledge")
    assert not any(item.get("id") == diag_id
                   for item in client.get("/api/v1/feed").json()["items"])

    con = db.connect()
    db.diagnostic(con, "RUN_FAILED", "error", "resolved fixture")
    resolved_id = con.execute("SELECT id FROM diagnostic ORDER BY id DESC").fetchone()["id"]
    con.execute("UPDATE diagnostic SET resolved_at=? WHERE id=?", (db.now(), resolved_id))
    con.close()
    assert not any(item.get("id") == resolved_id
                   for item in client.get("/api/v1/feed").json()["items"])


def test_gee_toggle_refuses_without_configuration_and_health_reports_quota(client):
    r = client.get("/api/v1/health").json()
    assert r["gee"]["configured"] is False
    assert r["gee"]["monthly_used"] == 0 and r["gee"]["monthly_budget"] > 0

    p = client.post("/api/v1/projects", json={
        "name": "Site GEE", "aoi_geojson": AOI,
        "recipe_id": "vegetation_loss_optical"}).json()
    on = client.post(f"/api/v1/projects/{p['uuid']}/gee", json={"enabled": True})
    assert on.status_code == 400          # no service account: stay on the local path
    off = client.post(f"/api/v1/projects/{p['uuid']}/gee", json={"enabled": False})
    assert off.json()["gee_enabled"] is False


# --- UX audit regressions --------------------------------------------------------

def test_artifact_path_survives_a_moved_data_dir(client, tmp_path):
    """The store is content-addressed, so a reader must derive the path from the
    digest rather than trusting the absolute path recorded at write time. A database
    written inside a container recorded /data/artifacts/... and served 404s for every
    chip when the same store was opened from the host."""
    from snitch import artifacts, db
    con = db.connect()
    aid = artifacts.put_bytes(con, b"\x89PNG-ish", "image/png")
    con.execute("UPDATE artifact SET path='/nonexistent/container/path.bin' WHERE id=?",
                (aid,))
    con.commit()
    con.close()
    r = client.get(f"/api/v1/artifacts/{aid}/raw")
    assert r.status_code == 200 and r.content == b"\x89PNG-ish"


def test_feed_hides_diagnostics_from_deleted_projects_but_keeps_global_ones(client):
    from snitch import db
    con = db.connect()
    pid = con.execute(
        "INSERT INTO project(uuid, name, aoi_geojson, aoi_area_km2, analysis_crs,"
        " recipe_id, recipe_version, params_json, adapter_preference, schedule_cron,"
        " status, created_at, updated_at) VALUES"
        " ('gone-uuid','Gone','{}',1,'EPSG:4326','construction_optical','1.0','{}',"
        " '[]','0 3 * * *','active',datetime('now'),datetime('now'))").lastrowid
    db.diagnostic(con, "RUN_FAILED", "error", "belongs to a project", project_id=pid)
    db.diagnostic(con, "RUN_FAILED", "error", "global, no project")
    con.commit(); con.close()

    codes = [i["message"] for i in client.get("/api/v1/feed").json()["items"]]
    assert "belongs to a project" in codes and "global, no project" in codes

    client.delete("/api/v1/projects/gone-uuid")
    codes = [i["message"] for i in client.get("/api/v1/feed").json()["items"]]
    assert "belongs to a project" not in codes, "deleted project still leaks into the feed"
    assert "global, no project" in codes, "a project-less diagnostic must survive"


def test_diagnostics_group_and_acknowledge_by_code(client):
    """609 identical warnings are one problem; they must be clearable in one action."""
    from snitch import db
    con = db.connect()
    for i in range(7):
        db.diagnostic(con, "ADAPTER_FALLBACK", "warning", f"fallback {i}")
    db.diagnostic(con, "MASK_UNAVAILABLE", "warning", "no mask")
    con.commit(); con.close()

    groups = {g["code"]: g for g in
              client.get("/api/v1/diagnostics/summary").json()["items"]}
    assert groups["ADAPTER_FALLBACK"]["n"] == 7
    assert groups["MASK_UNAVAILABLE"]["n"] == 1

    r = client.post("/api/v1/diagnostics/acknowledge",
                    json={"code": "ADAPTER_FALLBACK"})
    assert r.json()["acknowledged"] == 7
    left = {g["code"] for g in
            client.get("/api/v1/diagnostics/summary").json()["items"]}
    assert "ADAPTER_FALLBACK" not in left and "MASK_UNAVAILABLE" in left


def test_capped_diagnostics_page_reports_the_true_total(client):
    """A page of 100 out of 609 that does not say so is a silent truncation."""
    from snitch import db
    con = db.connect()
    for i in range(12):
        db.diagnostic(con, "ADAPTER_FALLBACK", "warning", f"n{i}")
    con.commit(); con.close()
    body = client.get("/api/v1/diagnostics?limit=5").json()
    assert len(body["items"]) == 5 and body["total"] == 12


def test_health_reports_a_queue_with_no_worker_draining_it(client):
    from snitch import db
    con = db.connect()
    con.execute(
        "INSERT INTO job(kind, payload_json, status, available_at, created_at,"
        " updated_at) VALUES ('backtest','{}','queued',"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes'),"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes'),"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now','-30 minutes'))")
    con.commit(); con.close()
    w = client.get("/api/v1/health").json()["worker"]
    assert w["stalled_jobs"] == 1 and w["ok"] is False and "worker" in w["hint"]


def test_a_job_deferred_into_the_future_is_not_called_stalled(client):
    from snitch import db
    con = db.connect()
    con.execute(
        "INSERT INTO job(kind, payload_json, status, available_at, created_at,"
        " updated_at) VALUES ('poll','{}','queued',"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now','+2 hours'),"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now'),"
        " strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))")
    con.commit(); con.close()
    assert client.get("/api/v1/health").json()["worker"]["ok"] is True


def test_bulk_triage_marks_many_alerts_in_one_call(client):
    """A triage queue cleared one card at a time is a queue you stop clearing."""
    from snitch import db
    con = db.connect()
    pid = con.execute(
        "INSERT INTO project(uuid, name, aoi_geojson, aoi_area_km2, analysis_crs,"
        " recipe_id, recipe_version, params_json, adapter_preference, schedule_cron,"
        " status, created_at, updated_at) VALUES"
        " ('bulk-uuid','Bulk','{}',1,'EPSG:4326','construction_optical','1.0','{}',"
        " '[]','0 3 * * *','active',datetime('now'),datetime('now'))").lastrowid
    rid = con.execute(
        "INSERT INTO run(uuid, project_id, kind, detector_id, detector_version,"
        " params_json, started_at, status, compute_backend) VALUES"
        " ('bulk-run',?,'backtest','builtup_dual','1.0','{}',datetime('now'),'ok','local')",
        (pid,)).lastrowid
    uuids = []
    for i in range(3):
        u = f"bulk-alert-{i}"
        uuids.append(u)
        con.execute(
            "INSERT INTO alert(uuid, project_id, run_id, raised_at, sensed_at,"
            " severity, confidence, score, threshold, changed_area_m2,"
            " changed_fraction, n_components, largest_component_m2, geometry_geojson,"
            " explanation_text, caveats_json, user_status) VALUES"
            " (?,?,?,datetime('now'),datetime('now'),'high','provisional',3.0,2.5,"
            " 5000.0,0.01,1,5000.0,'{}','test crossing','[]','new')", (u, pid, rid))
    con.commit(); con.close()

    r = client.post("/api/v1/alerts/triage",
                    json={"uuids": uuids, "user_status": "false"})
    assert r.json()["updated"] == 3

    from snitch import db as _db
    con = _db.connect()
    statuses = [row["user_status"] for row in con.execute(
        "SELECT user_status FROM alert WHERE uuid IN (?,?,?)", uuids)]
    con.close()
    assert statuses == ["false", "false", "false"]


def test_config_ui_roundtrip(client, monkeypatch, tmp_path):
    """Settings edits go through the API: applied live, secrets never returned,
    environment overrides refused, file rewritten with a backup."""
    import yaml
    from snitch import config as cfgmod
    yml = tmp_path / "config.yaml"
    yml.write_text(yaml.safe_dump({
        "storage": {"gc_enabled": False, "max_artifact_gb": 50, "data_dir": "./data"},
        "alerts": {"cooldown_days": 45},
        "explanations": {"vlm_endpoint": None},
        # trust_local off: this test is about password enforcement, which only
        # applies to callers the loopback trust does not cover.
        "ui": {"password": "sekret", "trust_local": False},
    }))
    monkeypatch.setattr(cfgmod, "PATH", str(yml))
    monkeypatch.setenv("SNITCH_ALERTS__COOLDOWN_DAYS", "60")
    cfgmod.config.cache_clear()
    auth = {"x-snitch-password": "sekret"}
    try:
        keys = client.get("/api/v1/config", headers=auth).json()["keys"]

        # §16: the password is never returned, only its presence.
        assert keys["ui.password"]["value"] is None
        assert keys["ui.password"]["set"] is True
        # An env override is reported with its effective value.
        assert keys["alerts.cooldown_days"]["value"] == 60
        assert keys["alerts.cooldown_days"]["env"] == "SNITCH_ALERTS__COOLDOWN_DAYS"

        # Env-overridden keys are refused: the variable would win on restart.
        r = client.put("/api/v1/config", headers=auth,
                       json={"values": {"alerts.cooldown_days": 10}})
        assert "overridden" in r.json()["rejected"]["alerts.cooldown_days"]

        # Unknown keys are refused; the file defines the schema.
        r = client.put("/api/v1/config", headers=auth,
                       json={"values": {"nope.key": 1}})
        assert "unknown" in r.json()["rejected"]["nope.key"]

        # A normal edit applies live in this process and lands on disk.
        r = client.put("/api/v1/config", headers=auth,
                       json={"values": {"storage.max_artifact_gb": 20}})
        assert r.json()["applied"]["storage.max_artifact_gb"] == "live"
        assert cfgmod.get("storage.max_artifact_gb") == 20
        on_disk = yaml.safe_load(yml.read_text())
        assert on_disk["storage"]["max_artifact_gb"] == 20
        assert (tmp_path / "config.yaml.orig").exists(), "hand-edited original kept"

        # A restart-only key is applied but labelled.
        r = client.put("/api/v1/config", headers=auth,
                       json={"values": {"storage.data_dir": "./data2"}})
        assert r.json()["applied"]["storage.data_dir"] == "restart"

        # The write-only password can be set; it still never comes back.
        r = client.put("/api/v1/config", headers=auth,
                       json={"values": {"ui.password": "nueva"}})
        assert r.json()["applied"]["ui.password"] == "live"
        # Even the middleware now enforces the new password.
        assert client.get("/api/v1/config",
                          headers=auth).status_code == 401
        assert client.get("/api/v1/config",
                          headers={"x-snitch-password": "nueva"}
                          ).json()["keys"]["ui.password"]["value"] is None
        assert cfgmod.get("ui.password") == "nueva"
    finally:
        cfgmod.config.cache_clear()


def test_a_recipe_with_no_default_for_a_parameter_refuses_until_it_is_given(client):
    """PWTT cannot invent a conflict start date, so a project without one is refused
    at creation rather than accepted and failed on every run it schedules."""
    body = {"name": "pwtt", "aoi_geojson": AOI, "recipe_ids": ["battle_damage_radar"],
            "s1_relative_orbit": 43}
    r = client.post("/api/v1/projects", json=body)
    assert r.status_code == 400 and "war_start" in r.json()["detail"]

    r = client.post("/api/v1/projects",
                    json={**body, "params": {"war_start": "2022-02-24"}})
    assert r.status_code == 201, r.text
    assert r.json()["params"]["war_start"] == "2022-02-24"


def test_password_gate_loopback_trust_and_session_cookie(client, monkeypatch):
    """No password + loopback trust: open to this machine only. With a password and
    trust_local off: header or cookie required — and the cookie alone must carry
    <img>-style requests that cannot send headers."""
    from snitch import api as api_mod, config as cfg
    monkeypatch.setenv("SNITCH_UI__PASSWORD", "sekret")
    monkeypatch.setenv("SNITCH_UI__TRUST_LOCAL", "false")
    cfg.config.cache_clear()
    try:
        # health stays reachable for probes, but liveness only
        assert client.get("/api/v1/health").json() == {"ok": True}
        assert client.get("/api/v1/projects").status_code == 401
        # login: wrong password refused (with backoff tracking), right one mints a cookie
        assert client.post("/api/v1/auth/login",
                           json={"password": "nope"}).status_code == 401
        r = client.post("/api/v1/auth/login", json={"password": "sekret"})
        assert r.status_code == 200
        # the cookie alone now authenticates, no custom header
        assert client.get("/api/v1/projects").status_code == 200
        client.post("/api/v1/auth/logout")
        assert client.get("/api/v1/projects").status_code == 401
        # header path still works for non-browser clients
        h = {"x-snitch-password": "sekret"}
        assert client.get("/api/v1/projects", headers=h).status_code == 200
        # https validation: an outbound endpoint on plain http is rejected at write time
        r = client.put("/api/v1/config", headers=h,
                       json={"values": {"notifications.webhook.url":
                                        "http://evil.example/hook"}})
        assert "notifications.webhook.url" in r.json()["rejected"]
    finally:
        monkeypatch.delenv("SNITCH_UI__PASSWORD")
        monkeypatch.delenv("SNITCH_UI__TRUST_LOCAL")
        cfg.config.cache_clear()


def test_project_creation_refuses_areas_past_the_pixel_cap(client, monkeypatch):
    from snitch import config as cfg
    monkeypatch.setenv("SNITCH_ADAPTERS__MAX_PIXELS", "1000")
    cfg.config.cache_clear()
    try:
        # the fixture AOI is ~7.6 km² ≈ 76k px at 10 m — far past 1,000 px
        r = client.post("/api/v1/projects", json={
            "name": "too big", "aoi_geojson": AOI, "recipe_ids": ["general_change_optical"]})
        assert r.status_code == 400 and "pixel" in r.json()["detail"].lower()
    finally:
        monkeypatch.delenv("SNITCH_ADAPTERS__MAX_PIXELS")
        cfg.config.cache_clear()
