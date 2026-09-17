"""Copernicus Data Space authenticated pixel access.

The CDSE catalogue search is public, but its S2 assets resolve to s3://eodata
objects on CDSE's own S3-compatible endpoint. Reading them needs short-lived S3
keys, exchanged with an API client's OAuth token:

  1. POST client_id + client_secret to the identity service -> bearer token
  2. POST the bearer token to the S3-credentials endpoint -> access/secret key
  3. read /vsis3/eodata/... through GDAL with those keys until they expire

The API client is created once in the CDSE portal (User Settings -> API clients);
its id and secret reach this module through CDSE_CLIENT_ID / CDSE_CLIENT_SECRET.
Every endpoint and the S3 region are config-overridable under adapters.cdse.*,
because this exchange runs against a live third party that can move.
"""
from __future__ import annotations
import threading
import time

import httpx

from . import config

_IDENTITY_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
_S3_API_URL = "https://catalogue.dataspace.copernicus.eu/api/v1/s3"
#: CDSE S3 keys are short-lived; re-exchange before the assumed expiry rather
#: than parsing the response's dates. A read that still fails with 403 calls
#: invalidate() and the next attempt exchanges fresh.
_S3_VALIDITY_S = 3000

_lock = threading.Lock()
_token: tuple[float, str] | None = None          # (expires_at, token)
_s3: tuple[float, str, str] | None = None        # (expires_at, access, secret)


def _get(dotted: str, default: str) -> str:
    return str(config.get(dotted, default))


def configured() -> bool:
    import os
    return bool(os.environ.get("CDSE_CLIENT_ID")) and \
        bool(os.environ.get("CDSE_CLIENT_SECRET"))


def invalidate() -> None:
    global _s3
    with _lock:
        _s3 = None


def token() -> str:
    global _token
    with _lock:
        if _token and _token[0] > time.time():
            return _token[1]
    import os
    r = httpx.post(_get("adapters.cdse.identity_url", _IDENTITY_URL),
                   data={"grant_type": "client_credentials",
                         "client_id": os.environ["CDSE_CLIENT_ID"],
                         "client_secret": os.environ["CDSE_CLIENT_SECRET"]},
                   timeout=30)
    if r.status_code != 200:
        raise RuntimeError(
            f"CDSE rejected the API client credentials (HTTP {r.status_code}): "
            "check CDSE_CLIENT_ID / CDSE_CLIENT_SECRET")
    body = r.json()
    ttl = int(body.get("expires_in", 600))
    with _lock:
        _token = (time.time() + max(ttl - 60, 30), body["access_token"])
    return body["access_token"]


def s3_credentials() -> tuple[str, str]:
    """A usable (access key, secret key) pair, exchanging a fresh one when needed."""
    global _s3
    with _lock:
        if _s3 and _s3[0] > time.time():
            return _s3[1], _s3[2]
    r = httpx.post(_get("adapters.cdse.s3_api_url", _S3_API_URL),
                   headers={"Authorization": f"Bearer {token()}"},
                   timeout=30)
    if r.status_code != 200:
        raise RuntimeError(
            f"CDSE S3 credential exchange failed (HTTP {r.status_code}): "
            f"{r.text[:200]}")
    body = r.json()
    access, secret = body.get("accesskey"), body.get("secretkey")
    if not access or not secret:
        raise RuntimeError(f"CDSE S3 credential response was not usable: {r.text[:200]}")
    with _lock:
        _s3 = (time.time() + _S3_VALIDITY_S, access, secret)
    return access, secret


def read_env() -> dict:
    """GDAL /vsis3 configuration for the s3://eodata hrefs CDSE hands out."""
    if not configured():
        return {}
    access, secret = s3_credentials()
    return {"AWS_ACCESS_KEY_ID": access,
            "AWS_SECRET_ACCESS_KEY": secret,
            "AWS_S3_ENDPOINT": _get("adapters.cdse.s3_endpoint",
                                    "eodata.dataspace.copernicus.eu"),
            # path-style: the eodata bucket does not answer on virtual-host names
            "AWS_VIRTUAL_HOSTING": "FALSE",
            "AWS_HTTPS": "YES",
            "AWS_REGION": _get("adapters.cdse.s3_region", "eu-central-1"),
            "AWS_NO_SIGN_REQUEST": "NO"}
