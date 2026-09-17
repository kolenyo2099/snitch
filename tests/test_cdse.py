"""The CDSE lane: credential exchange, band-name resolution, href rewriting — and
honesty about what is missing when it is not configured."""
import numpy as np
import pytest

from snitch import adapters, cdse, masks
from snitch.adapters import SceneRef, StacAdapter


def _scene(assets):
    return SceneRef(scene_id="S", platform="S2", datetime="2025-06-26T10:17:01",
                    collection="sentinel-2-l2a", adapter="cdse",
                    relative_orbit=None, pass_direction=None, source_uri="u",
                    item={"assets": assets})


@pytest.fixture
def no_creds(monkeypatch):
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    cdse.invalidate()


def test_cdse_band_resolution_understands_2025_suffixes():
    a = StacAdapter("cdse")
    s = _scene({"B04_20m": {"href": "s3://eodata/b04.jp2"},
                "SCL_20m": {"href": "s3://eodata/scl.jp2"}})
    assert a._href(s, "B04") == "s3://eodata/b04.jp2"
    assert a._href(s, "SCL") == "s3://eodata/scl.jp2"
    with pytest.raises(KeyError):
        a._href(s, "cs")


def test_health_unconfigured_is_false_and_actionable(no_creds):
    h = StacAdapter("cdse").health()
    assert h["ok"] is False and "CDSE_CLIENT_ID" in h["error"]


def test_sign_refuses_s3_when_unconfigured(no_creds):
    with pytest.raises(RuntimeError):
        StacAdapter("cdse")._sign("s3://eodata/Sentinel-2/x.jp2")


class _Resp:
    def __init__(self, status_code, body):
        self.status_code, self._b = status_code, body
        self.text = str(body)

    def json(self):
        return self._b


def test_credential_exchange_and_read_env(monkeypatch, no_creds):
    posts = []

    def fake_post(url, **kw):
        posts.append(url)
        if "identity" in url:
            assert kw["data"]["grant_type"] == "client_credentials"
            return _Resp(200, {"access_token": "tok", "expires_in": 600})
        assert kw["headers"]["Authorization"] == "Bearer tok"
        return _Resp(200, {"accesskey": "AKIA", "secretkey": "sec"})

    monkeypatch.setattr(cdse.httpx, "post", fake_post)
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "sec")

    access, secret = cdse.s3_credentials()
    assert (access, secret) == ("AKIA", "sec")
    env = cdse.read_env()
    assert env["AWS_ACCESS_KEY_ID"] == "AKIA"
    assert env["AWS_S3_ENDPOINT"] == "eodata.dataspace.copernicus.eu"
    assert env["AWS_VIRTUAL_HOSTING"] == "FALSE"

    # cached: a second read exchanges nothing new
    cdse.s3_credentials()
    assert len([u for u in posts if "identity" not in u]) == 1


def test_exchange_failure_names_the_cause(monkeypatch, no_creds):
    monkeypatch.setattr(cdse.httpx, "post",
                        lambda url, **kw: _Resp(401, {"error": "invalid_client"}))
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "wrong")
    with pytest.raises(RuntimeError, match="401"):
        cdse.s3_credentials()


def test_sign_rewrites_s3_when_configured(monkeypatch):
    monkeypatch.setattr(adapters, "sas_token", lambda acct, c: "sig=1")
    monkeypatch.setenv("CDSE_CLIENT_ID", "id")
    monkeypatch.setenv("CDSE_CLIENT_SECRET", "sec")
    assert StacAdapter("cdse")._sign("s3://eodata/Sentinel-2/x.jp2") == \
        "/vsis3/eodata/Sentinel-2/x.jp2"
    # planetary SAS signing is untouched
    signed = StacAdapter("planetary")._sign(
        "https://acct.blob.core.windows.net/c/cog.tif")
    assert signed.endswith("?sig=1")


def test_cloud_mask_substitutes_from_scl_and_records_it():
    scl = np.zeros((8, 8), "int16")
    scl[0, :] = 8                       # SCL 8 = cloud medium probability
    data = {"SCL": scl, "B04": np.full((8, 8), 0.1, "float32")}
    invalid, summary, vf = masks.apply_chain(data, ["cloudscore_plus"], {})
    assert summary["cloudscore_plus"] is not None          # not "absent"
    assert data["_mask_substitutes"] == {"cloudscore_plus": "scl_shadow"}
    assert invalid[0, :].all()                  # cloud row, plus 2-px dilation
    assert not invalid[4:, :].any()


def test_mask_stays_absent_without_scl_to_fall_back_on():
    data = {"B04": np.full((4, 4), 0.1, "float32")}
    invalid, summary, vf = masks.apply_chain(data, ["cloudscore_plus"], {})
    assert summary["cloudscore_plus"] is None
    assert data["_mask_substitutes"] == {}
