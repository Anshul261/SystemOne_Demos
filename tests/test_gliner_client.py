import httpx
import pytest

from gliner_client import DecisionError, GlinerClient


def test_classify_rejects_unknown_label(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={"result": {"source_field": {"label": "invented", "confidence": 0.9}}},
            request=httpx.Request("POST", "http://test/classify"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = GlinerClient("http://test")
    with pytest.raises(DecisionError, match="unknown"):
        client.classify("state", "source_field", {"name": "Name"}, "Choose")


def test_field_classification_is_cached(monkeypatch):
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"result": {"source_field": {"label": "email", "confidence": 0.8}}},
            request=httpx.Request("POST", "http://test/classify"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    client = GlinerClient("http://test")
    candidates = {"email": "Value for Email"}
    assert client.classify_field("Email", candidates).label == "email"
    assert client.classify_field("Email", candidates).label == "email"
    assert calls == 1
