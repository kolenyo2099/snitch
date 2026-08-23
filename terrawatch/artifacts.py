"""Content-addressed artifact store. Immutable once written."""
import hashlib, json, os, shutil, tempfile

import numpy as np

from .config import data_dir
from .db import now


def _root() -> str:
    return os.path.join(data_dir(), "artifacts")


def path_for(sha: str) -> str:
    return os.path.join(_root(), sha[:2], sha[2:4], sha + ".bin")


def put_bytes(con, blob: bytes, media_type: str, meta: dict | None = None) -> int:
    sha = hashlib.sha256(blob).hexdigest()
    dest = path_for(sha)
    row = con.execute("SELECT id FROM artifact WHERE sha256=?", (sha,)).fetchone()
    if row and os.path.exists(dest):
        return row["id"]
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(dest))
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    os.replace(tmp, dest)          # atomic; artifacts are never edited in place
    if row:
        return row["id"]
    return con.execute(
        "INSERT INTO artifact(sha256, path, media_type, bytes, meta_json, created_at)"
        " VALUES (?,?,?,?,?,?)",
        (sha, dest, media_type, len(blob), json.dumps(meta or {}), now())).lastrowid


def put_file(con, src: str, media_type: str, meta: dict | None = None) -> int:
    with open(src, "rb") as f:
        return put_bytes(con, f.read(), media_type, meta)


def put_cog(con, array: np.ndarray, crs: str, transform, meta: dict | None = None,
            nodata=np.nan) -> int:
    """Write a single-band float32 COG and store it."""
    import rasterio
    from rasterio.io import MemoryFile

    a = np.asarray(array)
    profile = dict(driver="GTiff", height=a.shape[-2], width=a.shape[-1],
                   count=1 if a.ndim == 2 else a.shape[0],
                   dtype="float32" if a.dtype != bool else "uint8",
                   crs=crs, transform=transform, tiled=True,
                   blockxsize=256, blockysize=256, compress="deflate")
    if a.dtype != bool:
        profile["nodata"] = nodata
    with MemoryFile() as mem:
        with mem.open(**profile) as dst:
            dst.write(a.astype(profile["dtype"]) if a.ndim == 3
                      else a.astype(profile["dtype"])[None])
            dst.build_overviews([2, 4, 8], rasterio.enums.Resampling.average)
        blob = mem.read()
    return put_bytes(con, blob, "image/tiff; application=geotiff", meta)


def read(con, artifact_id: int) -> bytes:
    row = con.execute("SELECT path FROM artifact WHERE id=?", (artifact_id,)).fetchone()
    return open(row["path"], "rb").read()


def link_project(con, artifact_id: int, project_id: int, directory: str,
                 filename: str) -> str:
    """Expose an immutable artifact in the human-browsable per-project layout."""
    row = con.execute("SELECT path FROM artifact WHERE id=?", (artifact_id,)).fetchone()
    if not row:
        raise KeyError(f"artifact {artifact_id}")
    target_dir = os.path.join(data_dir(), "projects", str(project_id), directory)
    os.makedirs(target_dir, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
    target = os.path.join(target_dir, safe)
    if not os.path.exists(target):
        try:
            os.link(row["path"], target)
        except OSError:
            shutil.copyfile(row["path"], target)
    return target


def usage() -> dict:
    total = sum(os.path.getsize(os.path.join(dp, f))
                for dp, _, fs in os.walk(_root()) for f in fs) if os.path.isdir(_root()) else 0
    free = shutil.disk_usage(data_dir()).free if os.path.isdir(data_dir()) else 0
    return {"artifact_bytes": total, "disk_free_bytes": free}


def gc(con, dry_run: bool = True) -> dict:
    """Delete artifacts referenced by zero records. User-invoked only (spec §3.2)."""
    refs = set()
    for tbl, cols in [("project", ["baseline_artifact_id"]),
                      # a live baseline now hangs off the methodology; without this
                      # row every fitted baseline looks like an orphan to the sweep
                      ("project_methodology", ["baseline_artifact_id"]),
                      ("run", ["score_raster_id", "mask_raster_id"]),
                      ("alert", ["before_chip_id", "after_chip_id", "overlay_chip_id"])]:
        for c in cols:
            refs |= {r[0] for r in con.execute(
                f"SELECT {c} FROM {tbl} WHERE {c} IS NOT NULL")}
    for row in con.execute("SELECT summary_json FROM run WHERE summary_json IS NOT NULL"):
        try:
            aux = json.loads(row["summary_json"]).get("aux", {})
            for key in ("calibration_chips", "mask_layers"):
                refs |= {int(v) for v in aux.get(key, {}).values() if v}
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    orphans = [(r["id"], r["path"], r["bytes"]) for r in
               con.execute("SELECT id, path, bytes FROM artifact")
               if r["id"] not in refs]
    if not dry_run:
        for aid, p, _ in orphans:
            if os.path.exists(p):
                os.remove(p)
            con.execute("DELETE FROM artifact WHERE id=?", (aid,))
    return {"dry_run": dry_run, "count": len(orphans),
            "bytes": sum(b for _, _, b in orphans)}
