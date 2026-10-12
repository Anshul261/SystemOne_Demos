"""Local request-routing workflows driven by GLiNER decisions."""

from __future__ import annotations

import json
import time
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
CLINIC_PRIORITIES = {
    "routine": "Can wait for a normal reply or appointment",
    "soon": "Needs attention within a few days, such as medicine running out",
    "urgent": "Needs a clinician now: severe symptoms happening right now",
}
CLINIC_TOPICS = ["symptoms", "medication", "lab_results", "scheduling", "billing"]

HELPDESK_QUEUES = {
    "payroll": "Paycheck amount, tax withholding, or pay schedule",
    "benefits": "Retirement contributions, insurance, or employee benefits",
    "it_access": "Account login, permissions, or IT access",
    "facilities": "Building maintenance and workplace equipment",
    "expense_reimbursement": "Employee travel and expense claims",
    "manager_approval": "Requests that require a manager's approval",
}
HELPDESK_PRIORITIES = {
    "low": "A question or minor issue with no deadline",
    "normal": "Should be handled within the week",
    "high": "Will block the employee's work soon, such as by tomorrow",
    "urgent": "Blocks the employee's work right now",
}


def _single(value: Decision | list[Decision], task: str) -> Decision:
    if isinstance(value, list):
        raise DecisionError(f"Expected a single {task} decision")
    if value.confidence is not None and value.confidence < 0.15:
        raise DecisionError(f"Low-confidence {task}: {value}")
    return value


def load_tickets(clinic: bool) -> list[dict[str, str]]:
    fixture = "clinic_requests.json" if clinic else "helpdesk_tickets.json"
    return json.loads((ROOT / "fixtures" / fixture).read_text(encoding="utf-8"))


def intake_schema(clinic: bool) -> dict[str, dict[str, Any]]:
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
    return schema


def classify_ticket(
    ticket: dict[str, str], gliner: GlinerClient, clinic: bool, audit: AuditLog
) -> dict[str, Decision | list[Decision]]:
    schema = intake_schema(clinic)
    text = f"[subject] {ticket['subject']}\n[body] {ticket['message']}"
    audit.write("classification_started", ticket_id=ticket["id"], text=text, schema=schema)
    started = time.monotonic()
    decisions = gliner.classify_many(text, schema)
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    for head, value in decisions.items():
        labels = value if isinstance(value, list) else [value]
        audit.write(
            "business_decision",
            ticket_id=ticket["id"],
            task=head,
            decision=", ".join(item.label for item in labels) or "none",
            confidence=labels[0].confidence if len(labels) == 1 else None,
            predictions=[{"label": item.label, "confidence": item.confidence} for item in labels],
            candidates=list(schema[head]["labels"]),
            model_ms=elapsed_ms,
        )
    return decisions


def route_decisions(
    decisions: dict[str, Decision | list[Decision]], clinic: bool
) -> dict[str, Any]:
    schema = intake_schema(clinic)
    for head, spec in schema.items():
        value = decisions[head]
        if spec.get("multi_label"):
            if not isinstance(value, list):
                raise DecisionError(f"Expected multiple {head} decisions")
            labels = value
        else:
            labels = [_single(value, head)]
        if any(item.label not in spec["labels"] for item in labels):
            raise DecisionError(f"Unknown {head} decision")

    task = "request" if clinic else "queue"
    category = decisions[task].label
    priority = decisions["priority"].label
    handoff = decisions["needs_human"].label
    topics = [item.label for item in decisions["topics"]] if clinic else []
    destination = CLINIC_QUEUES[category] if clinic else category
    rules = [
        f"Request type {category} maps to {destination}."
        if clinic else f"The predicted team selects the {destination} queue."
    ]
    if clinic and priority == "urgent":
        destination = "clinical_review"
        rules.append("Urgent clinic requests override the usual queue and go to clinical_review.")
    requires_review = handoff == "yes" or (clinic and destination == "clinical_review")
    if handoff == "yes":
        rules.append("The model flagged staff review, so review_required is set.")
    elif clinic and destination == "clinical_review":
        rules.append("The clinical_review queue always requires staff review.")
    else:
        rules.append("The request enters the staff queue with no extra review flag.")
    return {
        "category": category,
        "queue": destination,
        "priority": priority,
        "review": "review_required" if requires_review else "staff_queue",
        "tags": topics,
        "rules": rules,
    }


def run_intake_simulation(
    gliner: GlinerClient,
    *,
    clinic: bool,
    tickets: list[dict[str, str]] | None = None,
    on_event: Callable[[dict[str, Any], bytes | None], None] | None = None,
) -> Path:
    """Classify messages and produce local ticket records without a browser."""
    tickets = load_tickets(clinic) if tickets is None else tickets
    if not tickets or any(
        not ticket.get(field, "").strip()
        for ticket in tickets
        for field in ("id", "subject", "message")
    ):
        raise ValueError("Add a subject and message before sending a request.")
    name = "clinic-simulation" if clinic else "helpdesk-simulation"
    audit = AuditLog(name, on_event=on_event)
    audit.write("simulation_started", total=len(tickets), clinic=clinic)
    for index, ticket in enumerate(tickets, start=1):
        audit.write("request_received", ticket_id=ticket["id"], ticket=ticket, index=index)
        decisions = classify_ticket(ticket, gliner, clinic, audit)
        route = route_decisions(decisions, clinic)
        audit.write("routing_decided", ticket_id=ticket["id"], **route)
        audit.write("ticket_routed", ticket_id=ticket["id"], subject=ticket["subject"], **route)
    audit.write("completed", routed=len(tickets), result="Local ticket records produced")
    return audit.path


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
    tickets = load_tickets(clinic)
    categories = CLINIC_REQUESTS if clinic else HELPDESK_QUEUES
    priorities = CLINIC_PRIORITIES if clinic else HELPDESK_PRIORITIES

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
        visible_ticket = {
            "id": ticket["id"],
            "subject": card.locator("h2").inner_text(),
            "message": card.locator(".message").inner_text(),
        }
        decisions = classify_ticket(visible_ticket, gliner, clinic, audit)
        route = route_decisions(decisions, clinic)
        chosen = {field: route[field] for field in ("category", "queue", "priority", "review")}
        topics = route["tags"]
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
