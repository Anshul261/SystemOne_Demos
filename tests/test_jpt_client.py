import httpx
import pytest

from compare_options import routing_options
from gliner_client import Decision, DecisionError
from jpt_client import JptClient


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
            request=httpx.Request("POST", "http://test/v1/systemone"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {
        "request": {"labels": {"book_appointment": "Schedule a visit", "other": "Other"}},
        "priority": {"labels": ["routine", "soon", "urgent"]},
        "needs_human": {"labels": ["yes", "no"], "prompt": "Does staff need to review this?"},
        "topics": {"labels": ["symptoms", "medication"], "multi_label": True, "cls_threshold": 0.4},
    }
    decisions = JptClient("http://test").classify_many("Need a nurse", schema)
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
            request=httpx.Request("POST", "http://test/v1/systemone"),
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with pytest.raises(DecisionError, match="unknown"):
        JptClient("http://test").classify("message", "queue", {"it_access": "IT"}, "Route")


def test_option_sweep_preserves_the_correct_label_at_255():
    options = routing_options(255)
    assert len(options) == 255
    assert options["it_access"] == "Account login, permissions, or IT access"
    assert len(set(options.values())) == 255
