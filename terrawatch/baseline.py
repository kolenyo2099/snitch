"""Baseline serialisation: a multi-band COG plus a JSON sidecar (spec §7.1)."""
import io, json

import numpy as np

from . import artifacts


def save(con, baseline: dict, crs: str, transform, observation_ids: list[int],
         date_range: tuple[str, str], detector_id: str, detector_version: str,
         adapter: str | None = None) -> int:
    names, planes = [], []
    for idx in sorted(baseline["coef"]):
        coef = baseline["coef"][idx]
        for k in range(coef.shape[0]):
            names.append(f"{idx}:coef{k}")
            planes.append(coef[k])
        names.append(f"{idx}:sigma")
        planes.append(baseline["sigma"][idx])
    meta = {"bands": names, "harmonics": baseline["harmonics"],
            "n_obs": baseline["n_obs"], "day0": baseline["day0"],
            "observation_ids": observation_ids,
            "fitted_from": date_range[0], "fitted_to": date_range[1],
            "detector_id": detector_id, "detector_version": detector_version,
            # The serving adapter: compared against each run's adapter so a run fed
            # by a different source than its baseline is flagged
            # (PROVENANCE_DISCONTINUITY). Baselines fitted before this field existed
            # read as unknown, which matches every adapter rather than failing.
            "adapter": adapter}
    return artifacts.put_cog(con, np.stack(planes).astype("float32"), crs, transform,
                             meta=meta)


def load(con, artifact_id: int) -> dict:
    import rasterio
    row = con.execute("SELECT path, meta_json FROM artifact WHERE id=?",
                      (artifact_id,)).fetchone()
    meta = json.loads(row["meta_json"])
    with rasterio.open(row["path"]) as src:
        stack = src.read().astype("float32")
    out = {"harmonics": meta["harmonics"], "n_obs": meta["n_obs"],
           "day0": meta["day0"], "coef": {}, "sigma": {}, "meta": meta}
    coefs: dict[str, list] = {}
    for name, plane in zip(meta["bands"], stack):
        idx, part = name.split(":")
        if part == "sigma":
            out["sigma"][idx] = plane
        else:
            coefs.setdefault(idx, []).append(plane)
    for idx, planes in coefs.items():
        out["coef"][idx] = np.stack(planes)
    return out
