import json
from functools import partial

import pytest
from playwright.sync_api import sync_playwright

import intake
from browser_forms import AuditLog
from gliner_client import Decision, DecisionError
from intake import run_clinic, run_intake_simulation


def test_urgent_request_is_sent_to_clinical_review_even_when_category_is_scheduling(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(intake, "AuditLog", partial(AuditLog, output_dir=str(tmp_path)))
    class StubClient:
        def classify_many(self, text, schema):
            urgent = "chest pain" in text
            return {
                "request": Decision("book_appointment", 0.9),
                "priority": Decision("urgent" if urgent else "routine", 0.9),
                "needs_human": Decision("no", 0.9),
                "topics": [Decision("symptoms", 0.8)],
            }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            artifact = run_clinic(page, StubClient())
            urgent = page.locator('[data-ticket-id="CL-1045"]')
            assert urgent.get_attribute("data-queue") == "clinical_review"
            assert urgent.get_attribute("data-review") == "review_required"
            assert urgent.get_attribute("data-topics") == "symptoms"
            assert "5 / 5 routed" in page.locator("#progress").inner_text()
            records = [json.loads(line) for line in artifact.read_text().splitlines()]
            assert len([record for record in records if record["event"] == "ticket_routed"]) == 5
        finally:
            browser.close()


@pytest.mark.parametrize("clinic", [True, False])
def test_simulation_keeps_model_predictions_and_routing_rules_separate(monkeypatch, tmp_path, clinic):
    monkeypatch.setattr(intake, "AuditLog", partial(AuditLog, output_dir=str(tmp_path)))
    calls = []
    events = []
    ticket = {"id": "TEST-1", "subject": "Please help", "message": "A person needs to review this."}

    class StubClient:
        def classify_many(self, text, schema):
            calls.append((text, schema))
            result = {
                "request" if clinic else "queue": Decision("book_appointment" if clinic else "it_access", 0.9),
                "priority": Decision("urgent", 0.95),
                "needs_human": Decision("no", 0.9),
            }
            if clinic:
                result["topics"] = [Decision("scheduling", 0.7), Decision("symptoms", 0.8)]
            return result

    artifact = run_intake_simulation(
        StubClient(), clinic=clinic, tickets=[ticket],
        on_event=lambda record, screenshot: events.append((record, screenshot)),
    )
    assert len(calls) == 1
    assert calls[0][0] == "[subject] Please help\n[body] A person needs to review this."
    assert all(screenshot is None for _, screenshot in events)
    records = [record for record, _ in events]
    assert records == [json.loads(line) for line in artifact.read_text().splitlines()]
    assert [record["event"] for record in records[:3]] == [
        "simulation_started", "request_received", "classification_started"
    ]
    output = records[-2]
    assert output["event"] == "ticket_routed"
    assert output["queue"] == ("clinical_review" if clinic else "it_access")
    assert output["review"] == ("review_required" if clinic else "staff_queue")
    assert output["category"] == ("book_appointment" if clinic else "it_access")
    assert output["rules"]
    predictions = [record for record in records if record["event"] == "business_decision"]
    assert predictions[0]["predictions"][0]["confidence"] == 0.9
    assert all(record["model_ms"] >= 0 for record in predictions)


def test_low_confidence_cannot_produce_a_ticket(monkeypatch, tmp_path):
    monkeypatch.setattr(intake, "AuditLog", partial(AuditLog, output_dir=str(tmp_path)))
    events = []

    class StubClient:
        def classify_many(self, text, schema):
            return {
                "queue": Decision("it_access", 0.1),
                "priority": Decision("normal", 0.9),
                "needs_human": Decision("no", 0.9),
            }

    with pytest.raises(DecisionError, match="Low-confidence"):
        run_intake_simulation(
            StubClient(), clinic=False,
            on_event=lambda record, _: events.append(record),
        )
    assert not any(record["event"] in {"ticket_routed", "completed"} for record in events)
