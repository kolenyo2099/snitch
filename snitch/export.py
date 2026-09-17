"""Evidence bundles (spec §15). Self-contained, hashable, third-party readable."""
from __future__ import annotations
import hashlib, io, json, os, subprocess, zipfile

from . import artifacts, config, db
from .recipes import REGISTRY as RECIPES
from .worker import handler

SCHEMA_VERSION = "1.0"
MAX_SOURCE_COG_BYTES = 200 * 1024 * 1024


def _readme_md(scope: str) -> str:
    return f"""# Snitch evidence bundle

This archive is a self-contained record of {scope}. Start with **method.md** for the
plain-language claim, method, threshold choice, limitations, and citation.

## File guide

- `method.md` — what was measured and how to interpret it.
- `manifest.json` — SHA-256 hashes for detecting any changed file.
- `project.json`, `runs.json`, `alerts.json` — recorded settings and results.
- `observations.json`, `sources.json` — exact source scenes and STAC provenance.
- `diagnostics.json` — data-quality problems reported during processing.
- `rasters/` — GeoTIFF score and valid-mask evidence.
- `chips/` — identically stretched before, after, and overlay previews.

The deterministic explanation is the claim Snitch made. Optional machine-generated
vision prose is deliberately excluded. Scores retain the units stated in `method.md` and
must not be interpreted as a universal confidence percentage.

To verify integrity, compute SHA-256 for each file and compare it with `manifest.json`.
"""


def _version() -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                                text=True, cwd=os.path.dirname(
                                    os.path.dirname(os.path.abspath(__file__)))
                                ).stdout.strip() or None
    except Exception:  # noqa: BLE001
        commit = None
    return {"snitch_version": "1.0", "git_commit": commit}


def _method_md(project, recipe, run, alert=None) -> str:
    params = json.loads(run["params_json"]) if run else json.loads(project["params_json"])
    prov = params.get("threshold_provenance", {"source": "default"})
    lines = [
        f"# Method — {recipe['display_name']}",
        "",
        f"**Question asked:** {recipe['plain_question']}",
        f"**Recipe:** `{recipe['id']}` version {recipe['version']}",
        f"**Detector:** `{run['detector_id']}` version {run['detector_version']}"
        if run else "",
        f"**Sensor:** {recipe['sensor']} at {recipe.get('resolution_m', '?')} m",
        f"**Bands used:** {', '.join(recipe['bands'])}",
        f"**Mask chain:** {', '.join(recipe['mask_chain'])}",
        "",
        "## Citation",
        "",
        f"> {recipe['reference']['citation']}",
        "",
        f"<{recipe['reference']['url']}>",
        "",
        recipe["reference"].get("note", ""),
        "",
        "## Threshold and how it was chosen",
        "",
        f"- Value: **{params.get('threshold')}** ({run['detector_id'] if run else ''})",
        f"- Source: **{prov.get('source', 'default')}**"
        + (f" ({prov['default_kind']})" if prov.get("default_kind") else "")
        + (f" on {prov['at']}" if prov.get("at") else ""),
        f"- Calibration artifact: {prov.get('backtest_artifact_id') or 'none'}",
        f"- Minimum mapping unit: {params.get('min_mapping_unit_m2')} m²",
        f"- Consecutive confirmations required: "
        f"{params.get('consecutive_confirmations', 1)}",
        "",
        "## Stated limitations of this method",
        "",
    ] + [f"- {l}" for l in recipe["limitations"]]
    if alert:
        lines += ["", "## What was claimed", "", alert["explanation_text"], "",
                  f"Caveats recorded: {', '.join(json.loads(alert['caveats_json'])) or 'none'}"]
    lines += ["", "## Reproducing this result", "",
              "Every parameter above is recorded in `run.json`. The source scenes are "
              "listed in `observations.json` with their STAC items and source URIs. "
              "Re-running the named detector version with those parameters over those "
              "scenes reproduces `score.tif` exactly.", ""]
    return "\n".join(l for l in lines if l is not None)


def build(con, *, project_uuid: str | None = None, run_uuid: str | None = None,
          alert_uuid: str | None = None) -> str:
    alert = run = None
    if alert_uuid:
        alert = con.execute("SELECT * FROM alert WHERE uuid=?", (alert_uuid,)).fetchone()
        run = con.execute("SELECT * FROM run WHERE id=?", (alert["run_id"],)).fetchone()
    elif run_uuid:
        run = con.execute("SELECT * FROM run WHERE uuid=?", (run_uuid,)).fetchone()
    pid = (alert or run)["project_id"] if (alert or run) else con.execute(
        "SELECT id FROM project WHERE uuid=?", (project_uuid,)).fetchone()["id"]
    project = con.execute("SELECT * FROM project WHERE id=?", (pid,)).fetchone()
    # An export is always of one methodology's work: a specific alert or run names it,
    # and a whole-project export takes the first unless one is asked for.
    mid = (alert or run)["methodology_id"] if (alert or run) else None
    meth = db.methodology(con, pid, mid)
    recipe = RECIPES[meth["recipe_id"]]

    runs = [run] if run else list(con.execute(
        "SELECT * FROM run WHERE project_id=? ORDER BY started_at", (pid,)))
    alerts = [alert] if alert else list(con.execute(
        "SELECT * FROM alert WHERE project_id=? ORDER BY sensed_at", (pid,)))
    obs_ids = {r["target_observation_id"] for r in runs if r["target_observation_id"]}
    for r in runs:
        obs_ids |= set(json.loads(r["reference_observation_ids"] or "[]"))
    observations = [dict(o) for o in con.execute(
        "SELECT * FROM observation WHERE id IN (%s)"
        % ",".join("?" * len(obs_ids or {0})), tuple(obs_ids or {0}))]
    diagnostics = [dict(d) for d in con.execute(
        "SELECT * FROM diagnostic WHERE project_id=? ORDER BY occurred_at", (pid,))]

    # Small text entries are written straight into the archive; rasters and chips
    # stream from the artifact store a megabyte at a time, so a project with a
    # hundred score rasters never holds more than one chunk per file in memory.
    out_dir = os.path.join(config.data_dir(), "projects", str(project["id"]), "exports")
    os.makedirs(out_dir, exist_ok=True)
    stem = alert_uuid or run_uuid or project["uuid"]
    path = os.path.join(out_dir, f"snitch_{stem}.zip")
    manifest_files: dict[str, dict] = {}
    skipped: list[dict] = []
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:

        def add(name: str, blob: bytes):
            z.writestr(name, blob)
            manifest_files[name] = {"sha256": hashlib.sha256(blob).hexdigest(),
                                    "bytes": len(blob)}

        add("project.json", json.dumps(dict(project), indent=2).encode())
        add("runs.json", json.dumps([dict(r) for r in runs], indent=2).encode())
        add("alerts.json", json.dumps([dict(a) for a in alerts], indent=2).encode())
        add("observations.json", json.dumps(observations, indent=2).encode())
        add("diagnostics.json", json.dumps(diagnostics, indent=2).encode())
        add("recipe.yaml", json.dumps(recipe, indent=2).encode())
        add("method.md", _method_md(project, recipe, runs[-1] if runs else None,
                                    alert).encode())
        add("README.md", _readme_md(
            f"alert {alert_uuid}" if alert_uuid else
            f"run {run_uuid}" if run_uuid else f"project {project['uuid']}").encode())
        # source references: STAC items plus exact source URIs (§15)
        add("sources.json", json.dumps(
            [{"scene_id": o["scene_id"], "source_uri": o["source_uri"],
              "adapter": o["adapter"], "stac_item": json.loads(o["stac_item_json"]),
              "note": "Pixels are read as HTTP range requests from the asset hrefs in the "
                      "STAC item; whole scenes are never downloaded."}
             for o in observations], indent=2).encode())

        def add_file(name: str, artifact_id: int):
            src = artifacts.local_path(con, artifact_id)
            if not src or not os.path.exists(src):
                return
            size = os.path.getsize(src)
            if size > MAX_SOURCE_COG_BYTES:
                skipped.append({"name": name, "artifact_id": artifact_id,
                                "bytes": size})
                return
            h = hashlib.sha256()
            with z.open(name, "w") as dst, open(src, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
                    dst.write(chunk)
            manifest_files[name] = {"sha256": h.hexdigest(), "bytes": size}

        for r in runs:
            for col, name in [("score_raster_id", "score"), ("mask_raster_id", "mask")]:
                if r[col]:
                    add_file(f"rasters/{r['uuid']}_{name}.tif", r[col])
        for a in alerts:
            for col, name in [("before_chip_id", "before"), ("after_chip_id", "after"),
                              ("overlay_chip_id", "overlay")]:
                if a[col]:
                    add_file(f"chips/{a['uuid']}_{name}.png", a[col])
        # §12.2: the VLM explanation is not exported by default.

        manifest = {"schema_version": SCHEMA_VERSION, "exported_at": db.now(),
                    **_version(),
                    "scope": {"project": project["uuid"], "run": run_uuid,
                              "alert": alert_uuid},
                    "files": {n: manifest_files[n] for n in sorted(manifest_files)},
                    "skipped_oversize": skipped,
                    "readme": "This bundle is self-contained. Start with method.md."}
        add("manifest.json", json.dumps(manifest, indent=2).encode())
    return path


@handler("export")
def _export_job(con, job, payload):
    return {"path": build(con, **payload)}
