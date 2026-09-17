"""Notification channels. All optional, all pluggable (spec §13.4)."""
from __future__ import annotations
import json, os, smtplib, subprocess
from email.message import EmailMessage
from urllib.parse import quote

import httpx

from . import config
from .log import log

CHANNELS: dict[str, callable] = {}


def channel(name):
    def deco(fn):
        CHANNELS[name] = fn
        return fn
    return deco


def payload(con, alert_uuid: str) -> dict:
    a = con.execute(
        "SELECT a.*, p.name AS project_name, p.uuid AS project_uuid FROM alert a"
        " JOIN project p ON p.id=a.project_id WHERE a.uuid=?", (alert_uuid,)).fetchone()
    base = config.get("ui.base_url", "http://localhost:8000")
    return {"alert_uuid": a["uuid"], "project": a["project_name"],
            "sensed_at": a["sensed_at"], "severity": a["severity"],
            "confidence": a["confidence"], "score": a["score"],
            "threshold": a["threshold"],
            "changed_area_ha": round(a["changed_area_m2"] / 10_000, 2),
            "explanation": a["explanation_text"],
            "caveats": json.loads(a["caveats_json"]),
            "url": f"{base}/projects/{a['project_uuid']}/alerts/{a['uuid']}"}


def _text(p: dict) -> str:
    caveats = ("\nCaveats: " + ", ".join(p["caveats"])) if p["caveats"] else ""
    return (f"[{p['severity']}/{p['confidence']}] {p['project']} — {p['sensed_at'][:10]}\n"
            f"{p['explanation']}{caveats}\n{p['url']}")


@channel("webhook")
def _webhook(p, cfg):
    httpx.post(cfg["url"], json=p, timeout=20).raise_for_status()


@channel("slack")
def _slack(p, cfg):
    httpx.post(cfg["webhook_url"], json={"text": _text(p)}, timeout=20).raise_for_status()


@channel("matrix")
def _matrix(p, cfg):
    token = os.environ.get(cfg.get("token_env", "MATRIX_TOKEN"), "")
    # The room id lands in the URL path; left raw, a "?", "#" or "/" inside it
    # would rewrite the request target.
    room = quote(cfg["room_id"], safe="")
    httpx.post(f"{cfg['homeserver']}/_matrix/client/v3/rooms/{room}"
               "/send/m.room.message",
               headers={"Authorization": f"Bearer {token}"},
               json={"msgtype": "m.text", "body": _text(p)}, timeout=20
               ).raise_for_status()


@channel("email")
def _email(p, cfg):
    msg = EmailMessage()
    msg["Subject"] = f"Snitch {p['severity']}: {p['project']} {p['sensed_at'][:10]}"
    msg["From"], msg["To"] = cfg["from"], ", ".join(cfg["to"])
    msg.set_content(_text(p))
    # Without a timeout a hung SMTP server parks the single worker thread forever —
    # the one outbound call in the package that smtplib does not bound by default.
    with smtplib.SMTP(cfg["smtp_host"], cfg.get("smtp_port", 587), timeout=30) as s:
        if os.environ.get("SMTP_PASSWORD"):
            s.starttls()
            s.login(cfg["from"], os.environ["SMTP_PASSWORD"])
        s.send_message(msg)


@channel("desktop")
def _desktop(p, cfg):
    # The body is passed as an argument, never interpolated into the AppleScript
    # source: alert text is data, and a trailing backslash inside an interpolated
    # string literal can re-quote the script — up to `do shell script`.
    subprocess.run(["osascript", "-e", "on run argv",
                    "-e", 'display notification (item 1 of argv) with title "Snitch"',
                    "-e", "end run", "--", _text(p)[:200]],
                   check=False)


def send(con, alert_uuid: str) -> list[str]:
    p = payload(con, alert_uuid)
    sent = []
    for name, fn in CHANNELS.items():
        cfg = config.get(f"notifications.{name}") or {}
        if not cfg.get("enabled"):
            continue
        try:
            fn(p, cfg)
            sent.append(name)
        except Exception as e:  # noqa: BLE001
            log.warning("notification failed",
                        extra={"extra": {"channel": name, "error": str(e)}})
    return sent
