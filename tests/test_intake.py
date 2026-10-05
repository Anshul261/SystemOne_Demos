import json
from functools import partial

from playwright.sync_api import sync_playwright

import intake
from browser_forms import AuditLog
from gliner_client import Decision
from intake import run_clinic


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
