from functools import partial

import pytest

import intake
import ui
from browser_forms import AuditLog
from gliner_client import Decision
from ui import DEMO_GUIDES, DEMO_NAMES, demo_guide


def test_each_demo_has_a_short_dynamic_guide():
    assert DEMO_GUIDES.keys() == DEMO_NAMES.keys()
    for name in DEMO_NAMES:
        guide = demo_guide(name)
        assert "<ol>" in guide
        assert guide.count("<li>") == 3
        if DEMO_NAMES[name] in ("stamp", "lab"):
            assert "d1" in guide  # the vision workflow names its own model family
            continue
        assert "d1-omni-600M" in guide  # the default decision model
        assert "GLiNER2.5 Multi" in demo_guide(name, "multi")


def test_request_stream_uses_edited_text_without_opening_a_browser(monkeypatch, tmp_path):
    monkeypatch.setattr(intake, "AuditLog", partial(AuditLog, output_dir=str(tmp_path)))
    calls = []

    class StubClient:
        def classify_many(self, text, schema):
            calls.append(text)
            return {
                "queue": Decision("benefits", 0.91),
                "priority": Decision("normal", 0.87),
                "needs_human": Decision("yes", 0.89),
            }

    def no_browser():
        raise AssertionError("A request simulation must not launch Playwright")

    monkeypatch.setattr(ui, "sync_playwright", no_browser)
    monkeypatch.setattr(ui, "make_client", lambda *args: StubClient())
    output = list(ui.stream_demo("helpdesk", subject="Edited subject", message="Edited message"))
    assert calls == ["[subject] Edited subject\n[body] Edited message"]
    assert all(packet["screenshot"] is None for packet in output)
    assert output[-1]["events"][-1]["event"] == "run_finished"
    tickets = [event for event in output[-1]["events"] if event["event"] == "ticket_routed"]
    assert len(tickets) == 1
    assert tickets[0]["subject"] == "Edited subject"
    assert tickets[0]["queue"] == "benefits"
    assert tickets[0]["review"] == "review_required"
    assert tickets[0]["ticket_id"].startswith("HD-")
    assert output[0]["events"] == [output[-1]["events"][0]]


def test_worker_errors_finish_the_stream_without_a_success_event(monkeypatch):
    def unavailable(*args):
        raise ConnectionError("Model service unavailable")

    monkeypatch.setattr(ui, "make_client", unavailable)
    output = list(ui.stream_demo("clinic", mode="inbox"))
    assert output[-1]["events"] == [{"event": "run_failed", "error": "Model service unavailable"}]


@pytest.mark.parametrize("subject,message", [("", "Message"), ("Subject", " "), ("A" * 201, "B"), ("A", "B" * 4001)])
def test_invalid_messages_are_rejected_before_starting_a_worker(subject, message):
    with pytest.raises(ValueError):
        list(ui.stream_demo("clinic", subject=subject, message=message))
