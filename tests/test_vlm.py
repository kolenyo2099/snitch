import json

from terrawatch import vlm


def test_vlm_disabled_never_reads_artifacts(monkeypatch):
    monkeypatch.setattr(vlm.config, "get", lambda key, default=None: False)
    monkeypatch.setattr(vlm.artifacts, "read",
                        lambda *args: (_ for _ in ()).throw(AssertionError("read")))
    assert vlm.describe(None, {"before": 1, "after": 2, "overlay": 3}, "summary") is None


def test_vlm_is_advisory_and_uses_all_three_chips(monkeypatch):
    values = {"explanations.vlm_enabled": True,
              "explanations.vlm_endpoint": "https://vlm.invalid/chat",
              "explanations.vlm_model": "vision-test"}
    monkeypatch.setattr(vlm.config, "get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(vlm.artifacts, "read", lambda con, aid: f"png-{aid}".encode())
    captured = {}

    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "  cautious description  "}}]}

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, endpoint, **kwargs):
            captured.update(endpoint=endpoint, **kwargs)
            return Response()

    monkeypatch.setattr(vlm.httpx, "Client", Client)
    assert vlm.describe(None, {"before": 1, "after": 2, "overlay": 3},
                        "deterministic") == "cautious description"
    body = captured["json"]
    assert body["temperature"] == 0
    assert sum(part["type"] == "image_url"
               for part in body["messages"][0]["content"]) == 3
    assert "Do not assign confidence" in body["messages"][0]["content"][0]["text"]
