"""Alerting rules, applied in the order given in spec §10."""
from __future__ import annotations
import json

import numpy as np

from . import config, db, geo
from .explain import no_alert_reason


def components(binary: np.ndarray, pixel_area_m2: float, mmu_m2: float):
    """Rule 1: label, drop components below the minimum mapping unit."""
    from scipy import ndimage
    lab, n = ndimage.label(binary)
    if n == 0:
        return np.zeros_like(binary, bool), [], 0.0
    sizes = ndimage.sum(binary, lab, range(1, n + 1)) * pixel_area_m2
    keep = np.where(sizes >= mmu_m2)[0] + 1
    mask = np.isin(lab, keep)
    kept = sorted(sizes[keep - 1].tolist(), reverse=True)
    return mask, kept, float(sum(kept))


def in_episode(con, project_id: int, methodology_id: int) -> bool:
    """Rule 2 state: is this methodology currently inside an alerting episode?

    Every rule below is scoped to one methodology. Two methodologies watching the same
    AOI measure different quantities on different scales, so pooling their runs would
    make the score distribution, the confirmation count and the incident history all
    meaningless."""
    r = con.execute("SELECT summary_json FROM run WHERE project_id=?"
                    " AND methodology_id=? AND status='ok'"
                    " ORDER BY started_at DESC LIMIT 1",
                    (project_id, methodology_id)).fetchone()
    if not r or not r["summary_json"]:
        return False
    return bool(json.loads(r["summary_json"]).get("in_episode"))


def _headline(summary: dict) -> float | None:
    """The polarity-correct extreme of a run's score. Falls back to score_p99 for
    summaries written before the headline existed."""
    value = summary.get("score_headline", summary.get("score_p99"))
    return value if value is not None else None


def severity(con, project_id: int, methodology_id: int, score: float) -> str:
    """Rule 6: position within this methodology's own historical score distribution."""
    hist = []
    for r in con.execute("SELECT summary_json FROM run WHERE project_id=?"
                         " AND methodology_id=? AND status='ok'"
                         " AND summary_json IS NOT NULL",
                         (project_id, methodology_id)):
        h = _headline(json.loads(r["summary_json"]))
        if h is not None:
            hist.append(h)
    if len(hist) < 10:
        return "low"
    if score >= np.percentile(hist, 99):
        return "high"
    if score >= np.percentile(hist, 90):
        return "medium"
    return "low"


def history(con, project_id: int, methodology_id: int) -> list[float]:
    return [h for h in (_headline(json.loads(r["summary_json"] or "{}"))
            for r in con.execute("SELECT summary_json FROM run WHERE project_id=?"
                                 " AND methodology_id=? AND status='ok'",
                                 (project_id, methodology_id))) if h is not None]


def confirmation(con, project_id: int, methodology_id: int, geometry: dict,
                 needed: int) -> tuple[str, int]:
    """Rule 3: count recent overlapping crossings to decide provisional vs confirmed."""
    if needed <= 1:
        return "confirmed", 1
    prior = [r for r in con.execute(
        "SELECT geometry_geojson FROM alert WHERE project_id=? AND methodology_id=?"
        " AND confidence='provisional' AND user_status != 'false'"
        " ORDER BY sensed_at DESC LIMIT 10", (project_id, methodology_id))]
    n = 1 + sum(1 for r in prior if geo.iou(json.loads(r["geometry_geojson"]),
                                            geometry) > 0.2)
    return ("confirmed" if n >= needed else "provisional"), n


def attach_incident(con, project_id: int, methodology_id: int, geometry: dict,
                    score: float, area_m2: float, sensed_at: str, title: str) -> int:
    """Rule 4: join an open incident on time window + IoU, else open a new one."""
    from datetime import datetime, timedelta
    window = config.get("alerts.incident_window_days", 30)
    cutoff = (datetime.fromisoformat(sensed_at) - timedelta(days=window)).isoformat()
    for r in con.execute("SELECT * FROM incident WHERE project_id=? AND"
                         " methodology_id=? AND state!='closed' AND opened_at >= ?",
                         (project_id, methodology_id, cutoff)):
        last = con.execute("SELECT geometry_geojson FROM alert WHERE incident_id=?"
                           " ORDER BY sensed_at DESC LIMIT 1", (r["id"],)).fetchone()
        if last and geo.iou(json.loads(last["geometry_geojson"]), geometry) > 0.2:
            con.execute("UPDATE incident SET peak_score=MAX(peak_score,?),"
                        " cumulative_area_m2=cumulative_area_m2+?, state='open'"
                        " WHERE id=?", (score, area_m2, r["id"]))
            return r["id"]
    return con.execute(
        "INSERT INTO incident(uuid,project_id,methodology_id,opened_at,state,"
        "peak_score,cumulative_area_m2,title) VALUES (?,?,?,?,'open',?,?,?)",
        (db.new_uuid(), project_id, methodology_id, sensed_at, score, area_m2,
         title)).lastrowid


def close_stale_incidents(con, project_id: int, methodology_id: int, today: str):
    from datetime import datetime, timedelta
    cd = config.get("alerts.cooldown_days", 45)
    cutoff = (datetime.fromisoformat(today) - timedelta(days=cd)).isoformat()
    con.execute(
        "UPDATE incident SET state='closed', closed_at=? WHERE project_id=?"
        " AND methodology_id=? AND state!='closed' AND COALESCE((SELECT"
        " MAX(sensed_at) FROM alert WHERE incident_id=incident.id), opened_at) < ?",
        (today, project_id, methodology_id, cutoff))


def retract_unconfirmed(con, project_id: int, methodology_id: int, sensed_at: str,
                        needed: int):
    """Rule 3: provisional alerts that never confirmed are marked, never deleted."""
    if needed <= 1:
        return
    from datetime import datetime, timedelta
    cutoff = (datetime.fromisoformat(sensed_at) - timedelta(days=needed * 20)).isoformat()
    con.execute("UPDATE alert SET user_status='unclear', user_note="
                "COALESCE(user_note,'') || ' [retracted: never confirmed]'"
                " WHERE project_id=? AND methodology_id=? AND"
                " confidence='provisional' AND user_status='new' AND sensed_at < ?",
                (project_id, methodology_id, cutoff))


def should_notify(con, incident_id: int, now_iso: str) -> bool:
    """Rule 5: one notification per incident per cooldown."""
    from datetime import datetime, timedelta
    hours = config.get("alerts.notification_cooldown_hours", 24)
    r = con.execute("SELECT MAX(raised_at) t FROM alert WHERE incident_id=?"
                    " AND id NOT IN (SELECT MAX(id) FROM alert WHERE incident_id=?)",
                    (incident_id, incident_id)).fetchone()
    if not r or not r["t"]:
        return True
    return datetime.fromisoformat(now_iso) - datetime.fromisoformat(r["t"]) \
        >= timedelta(hours=hours)


__all__ = ["components", "in_episode", "severity", "history", "confirmation",
           "attach_incident", "close_stale_incidents", "retract_unconfirmed",
           "should_notify", "no_alert_reason"]
