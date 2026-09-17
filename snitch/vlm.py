"""Optional, isolated vision-language explanation layer.

This module can only add prose after an alert decision and never receives authority to
change the run, severity, confidence, geometry, or notification decision.
"""
from __future__ import annotations
import base64
import os

import httpx

from . import artifacts, config


def _data_url(blob: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(blob).decode("ascii")


def describe(con, chip_ids: dict, deterministic_summary: str) -> str | None:
    if not config.get("explanations.vlm_enabled", False):
        return None
    endpoint = config.get("explanations.vlm_endpoint")
    model = config.get("explanations.vlm_model")
    ids = [chip_ids.get(k) for k in ("before", "after", "overlay")]
    if not endpoint or not model or not all(ids):
        return None
    from urllib.parse import urlparse
    u = urlparse(endpoint)
    if u.scheme != "https" and (u.hostname or "") not in (
            "localhost", "127.0.0.1", "::1"):
        # The bearer key and the site's before/after imagery ride in this request.
        from .log import log
        log.warning("vlm endpoint refused", extra={"extra": {
            "reason": "explanations.vlm_endpoint must be https (localhost excepted)"}})
        return None
    content = [{"type": "text", "text": (
        "Describe only visually apparent differences that support or qualify this "
        "deterministic satellite-change summary. Do not assign confidence, severity, "
        "or decide whether change occurred. State uncertainty plainly. Summary: "
        + deterministic_summary)}]
    for label, artifact_id in zip(("Before", "After", "Change overlay"), ids):
        content += [{"type": "text", "text": label},
                    {"type": "image_url",
                     "image_url": {"url": _data_url(artifacts.read(con, artifact_id)),
                                   "detail": "low"}}]
    headers = {"content-type": "application/json"}
    if token := os.environ.get("SNITCH_VLM_API_KEY"):
        headers["authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=45) as client:
        response = client.post(endpoint, headers=headers,
                               json={"model": model, "temperature": 0,
                                     "messages": [{"role": "user", "content": content}]})
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
