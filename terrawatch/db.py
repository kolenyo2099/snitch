"""SQLite schema, connection, and the job queue."""
import json, os, sqlite3, uuid
from datetime import datetime, timezone

from .config import data_dir
from .log import log

# One resolution for every stored thing: the config layer handles TW_DATA_DIR and the
# storage.data_dir key, so the database, artifacts and logs cannot end up in different
# trees depending on which module resolved the path first.
DATA_DIR = data_dir()
DB_PATH = os.path.join(DATA_DIR, "terrawatch.db")
# Kept separate from DB_PATH on purpose; see scheduler.start().
SCHEDULER_DB_PATH = os.path.join(DATA_DIR, "scheduler.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
  id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  description TEXT, aoi_geojson TEXT NOT NULL, aoi_area_km2 REAL NOT NULL,
  analysis_crs TEXT NOT NULL, recipe_id TEXT NOT NULL, recipe_version TEXT NOT NULL,
  params_json TEXT NOT NULL, adapter_preference TEXT NOT NULL,
  s1_relative_orbit INTEGER, s1_pass_direction TEXT, schedule_cron TEXT NOT NULL,
  status TEXT NOT NULL, baseline_start TEXT, baseline_end TEXT,
  baseline_artifact_id INTEGER REFERENCES artifact(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

-- What a project measures. A project may run several methodologies over the same
-- AOI at once; each keeps its own parameters, threshold and baseline, because a
-- baseline fitted for one detector means nothing to another.
CREATE TABLE IF NOT EXISTS project_methodology (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES project(id),
  recipe_id TEXT NOT NULL, recipe_version TEXT NOT NULL, params_json TEXT NOT NULL,
  baseline_start TEXT, baseline_end TEXT,
  baseline_artifact_id INTEGER REFERENCES artifact(id),
  status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  UNIQUE(project_id, recipe_id));

CREATE TABLE IF NOT EXISTS artifact (
  id INTEGER PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, path TEXT NOT NULL,
  media_type TEXT NOT NULL, bytes INTEGER NOT NULL, meta_json TEXT,
  created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS observation (
  id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES project(id),
  scene_id TEXT NOT NULL, platform TEXT NOT NULL, collection TEXT NOT NULL,
  adapter TEXT NOT NULL, sensed_at TEXT NOT NULL, discovered_at TEXT NOT NULL,
  relative_orbit INTEGER, pass_direction TEXT, valid_fraction REAL NOT NULL,
  cloud_fraction REAL, mask_summary_json TEXT NOT NULL, status TEXT NOT NULL,
  rejection_reason TEXT, source_uri TEXT NOT NULL, stac_item_json TEXT NOT NULL,
  UNIQUE(project_id, scene_id));

CREATE TABLE IF NOT EXISTS run (
  id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE,
  project_id INTEGER NOT NULL REFERENCES project(id), kind TEXT NOT NULL,
  detector_id TEXT NOT NULL, detector_version TEXT NOT NULL, params_json TEXT NOT NULL,
  target_observation_id INTEGER REFERENCES observation(id),
  reference_observation_ids TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, skip_reason TEXT, error_json TEXT,
  score_raster_id INTEGER REFERENCES artifact(id),
  mask_raster_id INTEGER REFERENCES artifact(id), summary_json TEXT,
  supersedes_run_id INTEGER REFERENCES run(id), compute_backend TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS incident (
  id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE,
  project_id INTEGER NOT NULL REFERENCES project(id), opened_at TEXT NOT NULL,
  closed_at TEXT, state TEXT NOT NULL, peak_score REAL NOT NULL,
  cumulative_area_m2 REAL NOT NULL, title TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS alert (
  id INTEGER PRIMARY KEY, uuid TEXT NOT NULL UNIQUE,
  project_id INTEGER NOT NULL REFERENCES project(id),
  run_id INTEGER NOT NULL REFERENCES run(id),
  incident_id INTEGER REFERENCES incident(id), raised_at TEXT NOT NULL,
  sensed_at TEXT NOT NULL, severity TEXT NOT NULL, confidence TEXT NOT NULL,
  score REAL NOT NULL, threshold REAL NOT NULL, changed_area_m2 REAL NOT NULL,
  changed_fraction REAL NOT NULL, n_components INTEGER NOT NULL,
  largest_component_m2 REAL NOT NULL, geometry_geojson TEXT NOT NULL,
  direction_json TEXT, explanation_text TEXT NOT NULL, explanation_llm TEXT,
  before_chip_id INTEGER REFERENCES artifact(id),
  after_chip_id INTEGER REFERENCES artifact(id),
  overlay_chip_id INTEGER REFERENCES artifact(id), caveats_json TEXT NOT NULL,
  user_status TEXT NOT NULL DEFAULT 'new', user_note TEXT);

CREATE TABLE IF NOT EXISTS diagnostic (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id),
  code TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL,
  detail_json TEXT, occurred_at TEXT NOT NULL, resolved_at TEXT,
  acknowledged INTEGER NOT NULL DEFAULT 0);

CREATE TABLE IF NOT EXISTS job (
  id INTEGER PRIMARY KEY, kind TEXT NOT NULL,
  project_id INTEGER REFERENCES project(id), payload_json TEXT NOT NULL,
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3, lease_expires_at TEXT,
  available_at TEXT NOT NULL, last_error TEXT,
  progress_done INTEGER NOT NULL DEFAULT 0, progress_total INTEGER,
  progress_note TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS gee_usage (
  id INTEGER PRIMARY KEY, project_id INTEGER REFERENCES project(id),
  kind TEXT NOT NULL, eecu REAL NOT NULL, detail_json TEXT,
  occurred_at TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS ix_meth ON project_methodology(project_id);
CREATE INDEX IF NOT EXISTS ix_gee ON gee_usage(occurred_at);
CREATE INDEX IF NOT EXISTS ix_obs ON observation(project_id, sensed_at);
CREATE INDEX IF NOT EXISTS ix_run ON run(project_id, started_at);
CREATE INDEX IF NOT EXISTS ix_alert ON alert(project_id, sensed_at);
CREATE INDEX IF NOT EXISTS ix_diag ON diagnostic(project_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_job ON job(status, available_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_uuid() -> str:
    return str(uuid.uuid4())


_CHECKED = False


def _integrity_check(con, path: str) -> None:
    """Fail loudly on a damaged file instead of writing more into it. Once per
    process: quick_check is cheap but not free, and every job leases a connection."""
    global _CHECKED
    result = con.execute("PRAGMA quick_check(1)").fetchone()[0]
    _CHECKED = True
    if result != "ok":
        raise sqlite3.DatabaseError(
            f"{path} failed its integrity check ({result}). Refusing to open it; "
            f"recover with: sqlite3 {path} .recover | sqlite3 {path}.recovered")


def connect(path: str | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # check_same_thread=False: FastAPI runs a sync dependency's teardown on whatever
    # worker thread is free, which is not always the one that opened the connection.
    # Each request still gets its own connection, so no two threads share one.
    con = sqlite3.connect(path, timeout=10, isolation_level=None,
                          check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA synchronous=FULL")
    if not _CHECKED:
        _integrity_check(con, path)
    con.executescript(SCHEMA)
    _migrate(con)
    return con


def _columns(con, table: str) -> set[str]:
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def _migrate(con) -> None:
    """Attribute every scored result to the methodology that produced it. Runs on a
    database written before projects could hold more than one."""
    for table in ("run", "alert", "incident"):
        if "methodology_id" not in _columns(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN methodology_id INTEGER"
                        f" REFERENCES project_methodology(id)")
    for col, decl in (("progress_done", "INTEGER NOT NULL DEFAULT 0"),
                      ("progress_total", "INTEGER"), ("progress_note", "TEXT")):
        if col not in _columns(con, "job"):
            con.execute(f"ALTER TABLE job ADD COLUMN {col} {decl}")


def ensure_methodologies(con, project_id: int) -> list:
    """Every project has at least one methodology. Projects created before this table
    existed carry theirs on the project row, so adopt that on first read rather than
    demanding a migration step that a fresh test fixture would never run."""
    rows = con.execute("SELECT * FROM project_methodology WHERE project_id=?"
                       " ORDER BY id", (project_id,)).fetchall()
    if rows:
        return rows
    p = con.execute("SELECT * FROM project WHERE id=?", (project_id,)).fetchone()
    if not p:
        raise KeyError(f"project {project_id}")
    t = now()
    mid = con.execute(
        "INSERT INTO project_methodology(project_id,recipe_id,recipe_version,"
        "params_json,baseline_start,baseline_end,baseline_artifact_id,status,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (project_id, p["recipe_id"], p["recipe_version"], p["params_json"],
         p["baseline_start"], p["baseline_end"], p["baseline_artifact_id"],
         p["status"], t, t)).lastrowid
    for table in ("run", "alert", "incident"):
        con.execute(f"UPDATE {table} SET methodology_id=? WHERE project_id=?"
                    f" AND methodology_id IS NULL", (mid, project_id))
    return con.execute("SELECT * FROM project_methodology WHERE project_id=?"
                       " ORDER BY id", (project_id,)).fetchall()


def methodology(con, project_id: int, methodology_id: int | None = None):
    """The addressed methodology, or the project's first when none is named."""
    rows = ensure_methodologies(con, project_id)
    if methodology_id is None:
        return rows[0]
    for r in rows:
        if r["id"] == methodology_id:
            return r
    raise KeyError(f"methodology {methodology_id} is not part of project {project_id}")


# --- job queue -------------------------------------------------------------

def enqueue(con, kind: str, payload: dict, project_id: int | None = None,
            available_at: str | None = None) -> int:
    t = now()
    cur = con.execute(
        "INSERT INTO job(kind, project_id, payload_json, status, available_at,"
        " created_at, updated_at) VALUES (?,?,?,'queued',?,?,?)",
        (kind, project_id, json.dumps(payload), available_at or t, t, t))
    return cur.lastrowid


def lease(con, lease_seconds: int = 300):
    """Atomically claim one due job. Returns a Row or None."""
    from datetime import timedelta
    t = now()
    expires = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
               ).isoformat(timespec="seconds")
    con.execute("BEGIN IMMEDIATE")
    try:
        row = con.execute(
            "UPDATE job SET status='leased', attempts=attempts+1,"
            " lease_expires_at=?, updated_at=? WHERE id = ("
            "  SELECT id FROM job WHERE available_at<=? AND ("
            "    status='queued' OR (status='leased' AND lease_expires_at<?))"
            "  ORDER BY available_at LIMIT 1) RETURNING *",
            (expires, t, t, t)).fetchone()
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return row


def progress(con, job, done: int, total: int | None = None,
             note: str | None = None) -> None:
    """Report how far a long job has got, so the UI can show a real bar instead of a
    spinner. Best-effort: handlers are also called directly (from tests and from
    ``backtest``, which reuses its own job row), and a failed progress write must
    never fail the job itself."""
    if job is None:
        return
    job_id = job["id"] if not isinstance(job, int) else job
    try:
        con.execute("UPDATE job SET progress_done=?, progress_total=COALESCE(?,"
                    "progress_total), progress_note=COALESCE(?,progress_note),"
                    " updated_at=? WHERE id=?", (done, total, note, now(), job_id))
    except Exception:  # noqa: BLE001
        log.warning("progress update failed", extra={"extra": {"job": job_id}})


def finish(con, job_id: int, error: str | None = None):
    row = con.execute("SELECT attempts, max_attempts FROM job WHERE id=?",
                      (job_id,)).fetchone()
    if error is None:
        status, avail = "done", now()
    elif row["attempts"] >= row["max_attempts"]:
        status, avail = "failed", now()
    else:
        status, avail = "queued", now()  # ponytail: immediate retry, add backoff if a flaky source hammers us
    con.execute("UPDATE job SET status=?, last_error=?, available_at=?, updated_at=?"
                " WHERE id=?", (status, error, avail, now(), job_id))
    return status


def diagnostic(con, code: str, severity: str, message: str,
               project_id: int | None = None, detail: dict | None = None):
    from .explain import DIAGNOSTICS
    expected_severity, remedy = DIAGNOSTICS.get(
        code, (severity, "Review the diagnostic details and retry when the cause is resolved."))
    # Preserve an intentionally stronger call-site severity, but normalize ordinary
    # writes to the stable taxonomy.
    levels = {"info": 0, "warning": 1, "error": 2}
    severity = max((severity, expected_severity), key=lambda s: levels.get(s, 0))
    detail = {**(detail or {}), "remedy": (detail or {}).get("remedy", remedy)}
    con.execute(
        "INSERT INTO diagnostic(project_id, code, severity, message, detail_json,"
        " occurred_at) VALUES (?,?,?,?,?,?)",
        (project_id, code, severity, message, json.dumps(detail), now()))
