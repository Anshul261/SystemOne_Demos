"""Local request-routing workflows driven by GLiNER decisions."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from playwright.sync_api import Page

from browser_forms import AuditLog, page_state
from gliner_client import Decision, DecisionError, GlinerClient

ROOT = Path(__file__).resolve().parent
BOARD_URL = (ROOT / "fixtures" / "intake_board.html").as_uri()

CLINIC_REQUESTS = {
    "book_appointment": "Schedule a visit with clinic staff",
    "refill_prescription": "Request a prescription refill for staff to review",
    "test_results": "Ask clinic staff about laboratory or imaging results",
    "referral": "Ask for a referral to a specialist",
    "billing_question": "Ask about a charge or invoice",
    "cancel_appointment": "Cancel an existing appointment",
    "symptom_question": "Ask a clinician about symptoms without a scheduling request",
    "other": "Another clinic request needing manual triage",
}
CLINIC_QUEUES = {
    "book_appointment": "scheduling",
    "refill_prescription": "medication_desk",
    "test_results": "nursing",
    "referral": "referrals",
    "billing_question": "billing",
    "cancel_appointment": "scheduling",
    "symptom_question": "clinical_review",
    "other": "clinical_review",
}
CLINIC_PRIORITIES = ["routine", "soon", "urgent"]
CLINIC_TOPICS = ["symptoms", "medication", "lab_results", "scheduling", "billing"]

HELPDESK_QUEUES = {
    "payroll": "Paycheck amount, tax withholding, or pay schedule",
    "benefits": "Retirement contributions, insurance, or employee benefits",
    "it_access": "Account login, permissions, or IT access",
    "facilities": "Building maintenance and workplace equipment",
    "expense_reimbursement": "Employee travel and expense claims",
    "manager_approval": "Requests that require a manager's approval",
}
HELPDESK_PRIORITIES = ["low", "normal", "high", "urgent"]


def _single(value: Decision | list[Decision], task: str) -> Decision:
    if isinstance(value, list):
        raise DecisionError(f"Expected a single {task} decision")
    if value.confidence is not None and value.confidence < 0.15:
        raise DecisionError(f"Low-confidence {task}: {value}")
    return value


def run_intake(
    page: Page,
    gliner: GlinerClient,
    *,
    clinic: bool,
    on_event: Callable[[dict[str, Any], bytes | None], None] | None = None,
) -> Path:
    name = "clinic-requests" if clinic else "employee-helpdesk"
    audit = AuditLog(
        name,
        page=page,
        on_event=on_event,
        screenshot_events={"ticket_routed", "completed"},
    )
    fixture = "clinic_requests.json" if clinic else "helpdesk_tickets.json"
    tickets = json.loads((ROOT / "fixtures" / fixture).read_text(encoding="utf-8"))
    categories = CLINIC_REQUESTS if clinic else HELPDESK_QUEUES
    priorities = CLINIC_PRIORITIES if clinic else HELPDESK_PRIORITIES
    task = "request" if clinic else "queue"
    schema: dict[str, dict[str, Any]] = {
        task: {"labels": categories},
        "priority": {"labels": priorities},
        "needs_human": {
            "labels": ["yes", "no"],
            "prompt": "Does the sender ask for a person or require staff review before any action?",
        },
    }
    if clinic:
        schema["topics"] = {
            "labels": CLINIC_TOPICS,
            "multi_label": True,
            "cls_threshold": 0.4,
        }

    page.goto(BOARD_URL)
    page.evaluate(
        "config => window.loadTickets(config)",
        {
            "title": "Clinic request intake" if clinic else "Employee help desk",
            "categoryLabel": "Request type" if clinic else "Team",
            "tickets": tickets,
            "categories": list(categories),
            "queues": (
                list(dict.fromkeys(CLINIC_QUEUES.values()))
                if clinic else list(categories)
            ),
            "priorities": priorities,
            "topics": CLINIC_TOPICS if clinic else [],
        },
    )
    for ticket in tickets:
        card = page.locator(".ticket").filter(
            has=page.get_by_text(ticket["id"], exact=True)
        )
        text = (
            f"[subject] {card.locator('h2').inner_text()}\n"
            f"[body] {card.locator('.message').inner_text()}"
        )
        decisions = gliner.classify_many(text, schema)
        for head, value in decisions.items():
            labels = value if isinstance(value, list) else [value]
            audit.write(
                "business_decision",
                ticket_id=ticket["id"],
                task=head,
                decision=", ".join(item.label for item in labels) or "none",
                confidence=labels[0].confidence if len(labels) == 1 else None,
                candidates=list(schema[head]["labels"]),
            )

        category = _single(decisions[task], task).label
        priority = _single(decisions["priority"], "priority").label
        handoff = _single(decisions["needs_human"], "needs_human").label
        topics = [item.label for item in decisions["topics"]] if clinic else []
        queue = CLINIC_QUEUES[category] if clinic else category
        if clinic and priority == "urgent":
            queue = "clinical_review"
        requires_review = handoff == "yes" or (clinic and queue == "clinical_review")
        chosen = {
            "category": category,
            "queue": queue,
            "priority": priority,
            "review": "review_required" if requires_review else "staff_queue",
        }
        for field, value in chosen.items():
            card.locator(f'select[data-field="{field}"]').select_option(value=value)
        card.get_by_role("button", name="Apply routing").click()
        for field, value in chosen.items():
            if card.get_attribute(f"data-{field}") != value:
                raise RuntimeError(f"Ticket {ticket['id']} did not save {field}={value}")
        if clinic:
            page.evaluate(
                "data => window.tagTicket(data.id, data.tags)",
                {"id": ticket["id"], "tags": topics},
            )
            if card.get_attribute("data-topics") != ",".join(topics):
                raise RuntimeError(f"Ticket {ticket['id']} did not save its topic tags")
        card.scroll_into_view_if_needed()
        audit.write("ticket_routed", ticket_id=ticket["id"], tags=topics, **chosen)

    if page.locator(".ticket[data-review]").count() != len(tickets):
        raise RuntimeError("Not every request reached a staff queue")
    audit.write("completed", routed=len(tickets), result=page_state(page))
    return audit.path


def run_clinic(page: Page, gliner: GlinerClient, on_event=None) -> Path:
    return run_intake(page, gliner, clinic=True, on_event=on_event)


def run_helpdesk(page: Page, gliner: GlinerClient, on_event=None) -> Path:
    return run_intake(page, gliner, clinic=False, on_event=on_event)
