import httpx
import pytest

from gliner_client import Decision, DecisionError, GlinerClient


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


def test_multiple_heads_and_multilabel_tags_share_one_request(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(
            200,
            json={
                "result": {
                    "request": {"label": "book_appointment", "confidence": 0.85},
                    "topics": [
                        {"label": "symptoms", "confidence": 0.7},
                        {"label": "scheduling", "confidence": 0.8},
                    ],
                }
            },
            request=httpx.Request("POST", "http://test/classify"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {
        "request": {"labels": {"book_appointment": "Schedule a visit", "other": "Other"}},
        "topics": {
            "labels": ["symptoms", "scheduling"],
            "multi_label": True,
            "cls_threshold": 0.4,
        },
    }
    result = GlinerClient("http://test").classify_many("Can I book a visit?", schema)
    assert len(sent) == 1
    assert sent[0] == {
        "text": "Can I book a visit?",
        "schema": schema,
        "include_confidence": True,
    }
    assert result["request"] == Decision("book_appointment", 0.85)
    assert result["topics"] == [Decision("symptoms", 0.7), Decision("scheduling", 0.8)]
