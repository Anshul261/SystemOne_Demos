import httpx
import pytest

from compare_options import routing_options
from gliner_client import Decision, DecisionError
from decision_clients import D1DecisionClient, make_client


def test_typed_questions_and_probabilities(monkeypatch):
    sent = []

    def fake_post(*args, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(
            200,
            json={"answers": {
                "request": {
                    "type": "choice", "choice": "book_appointment",
                    "probabilities": {"book_appointment": 0.8, "other": 0.2},
                },
                "priority": {
                    "type": "score", "score": 1.8,
                    "probabilities": {"0": 0.1, "1": 0.2, "2": 0.7},
                },
                "needs_human": {"type": "noul", "noul": 0.9},
                "topics:symptoms": {"type": "noul", "noul": 0.8},
                "topics:medication": {"type": "noul", "noul": 0.1},
            }},
            request=httpx.Request("POST", "http://test/system_one"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {
        "request": {"labels": {"book_appointment": "Schedule a visit", "other": "Other"}},
        "priority": {"labels": ["routine", "soon", "urgent"]},
        "needs_human": {"labels": ["yes", "no"], "prompt": "Does staff need to review this?"},
        "topics": {"labels": ["symptoms", "medication"], "multi_label": True, "cls_threshold": 0.4},
    }
    decisions = D1DecisionClient("omni", "http://test").classify_many("Need a nurse", schema)
    questions = sent[0]["questions"]
    assert len(sent) == 1
    assert sent[0]["state"] == "Need a nurse"
    assert questions["request"]["criteria"] == schema["request"]["labels"]
    assert questions["priority"]["type"] == "score"
    assert questions["needs_human"]["type"] == "noul"
    assert questions["topics:symptoms"]["type"] == "noul"
    assert decisions == {
        "request": Decision("book_appointment", 0.8),
        "priority": Decision("urgent", 0.7),
        "needs_human": Decision("yes", 0.9),
        "topics": [Decision("symptoms", 0.8)],
    }


def test_unknown_jpt_choice_is_rejected(monkeypatch):
    def fake_post(*args, **kwargs):
        return httpx.Response(
            200,
            json={"answers": {"queue": {
                "type": "choice", "choice": "invented", "probabilities": {"invented": 1.0}
            }}},
            request=httpx.Request("POST", "http://test/system_one"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(DecisionError, match="unknown"):
        D1DecisionClient("omni", "http://test").classify("message", "queue", {"it_access": "IT"}, "Route")


def test_option_sweep_preserves_the_correct_label_at_255():
    options = routing_options(255)
    assert len(options) == 255
    assert options["it_access"] == "Account login, permissions, or IT access"
    assert len(set(options.values())) == 255


def test_d1_requests_name_the_model_and_each_model_has_a_client(monkeypatch):
    sent = []

    def fake_post(url, **kwargs):
        sent.append((url, kwargs["json"]))
        return httpx.Response(200, json={"answers": {"source_field": {
            "type": "choice", "choice": "first_name", "probabilities": {"first_name": 0.9, "last_name": 0.1}}}},
            request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    decision = D1DecisionClient("3b", "http://test").classify_field("Given name", {"first_name": "First", "last_name": "Last"})
    assert decision.label == "first_name"
    url, body = sent[0]
    assert url == "http://test/system_one" and body["model"] == "3b"
    assert body["state"] == "Form field label: Given name"  # d1 reads the label as its state
    assert type(make_client("omni")).__name__ == "D1DecisionClient"
    assert make_client("multi").field_request("Phone #") == ("Phone #", None)  # Multi gets the bare label
    with pytest.raises(ValueError):
        make_client("gliner")
