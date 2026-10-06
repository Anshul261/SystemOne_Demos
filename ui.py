from __future__ import annotations

import base64
import os
import queue
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import gradio as gr
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright

from intake import intake_schema, load_tickets, run_intake_simulation
from jpt_client import make_client
from run import DEMOS

GLINER_URL = os.getenv("GLINER_URL", "http://127.0.0.1:8010")
JPT_URL = os.getenv("JPT_URL", "http://127.0.0.1:8085")
HOST = os.getenv("DEMO_UI_HOST", "127.0.0.1")
PORT = int(os.getenv("DEMO_UI_PORT", "7863"))
WEB = Path(__file__).resolve().parent / "web"

DEMO_NAMES = {
    "RPA Challenge": "rpa",
    "ParaBank Claim Payment": "parabank",
    "Piko Resilient Intake": "piko",
    "Piko Excel Challenge": "piko-excel",
    "Clinic Requests": "clinic",
    "Employee Help Desk": "helpdesk",
}

DEMO_GUIDES = {
    "RPA Challenge": (
        "<strong>Bulk form matching</strong>"
        "<ol><li>Read one person from the downloaded spreadsheet.</li>"
        "<li>GLiNER classifies each visible form label as a source column.</li>"
        "<li>Playwright enters that trusted value, submits, and scans the shuffled form again.</li></ol>"
    ),
    "ParaBank Claim Payment": (
        "<strong>Claim decision and payment entry</strong>"
        "<ol><li>GLiNER reviews claim status, documents, and fraud review.</li>"
        "<li>It selects an allowed action such as pay, review, or stop.</li>"
        "<li>If approved, GLiNER maps claim data to the payment form and Playwright submits it.</li></ol>"
    ),
    "Piko Resilient Intake": (
        "<strong>Changing form and interruption handling</strong>"
        "<ol><li>The form changes its field and button IDs on purpose.</li>"
        "<li>GLiNER classifies each visible field and the next allowed action.</li>"
        "<li>Playwright follows those choices, including confirming the surprise verification dialog.</li></ol>"
    ),
    "Piko Excel Challenge": (
        "<strong>Timed spreadsheet-to-form automation</strong>"
        "<ol><li>Playwright downloads the Excel file, then starts the website timer.</li>"
        "<li>GLiNER classifies each shuffled form label as one spreadsheet column.</li>"
        "<li>Playwright enters ten rows, submits each record, and verifies the final score.</li></ol>"
    ),
    "Clinic Requests": (
        "<strong>Patient message routing</strong>"
        "<ol><li>Send a patient message or play the five-request sample inbox.</li>"
        "<li>GLiNER predicts request type, priority, staff review, and topic tags.</li>"
        "<li>Routing rules turn the predictions into a ticket for the right clinic team.</li></ol>"
    ),
    "Employee Help Desk": (
        "<strong>Shared employee inbox</strong>"
        "<ol><li>Send an employee message or play the four-request sample inbox.</li>"
        "<li>GLiNER predicts a team, priority, and staff-review need.</li>"
        "<li>Routing rules produce a ticket and a simulated acknowledgment.</li></ol>"
    ),
}

def demo_guide(display_name: str, model: str = "gliner") -> str:
    guide = DEMO_GUIDES[display_name]
    return guide.replace("GLiNER", "JPT") if model == "jpt" else guide


app = gr.Server(title="SystemOne Decision Lab")
app.mount("/assets", StaticFiles(directory=WEB), name="assets")


@app.get("/")
def homepage():
    return FileResponse(WEB / "index.html")


@app.get("/demo-config")
def demo_config():
    return {
        "workflows": [
            {"id": key, "name": name, "guide": DEMO_GUIDES[name]}
            for name, key in DEMO_NAMES.items()
        ],
        "samples": {"clinic": load_tickets(True), "helpdesk": load_tickets(False)},
        "schemas": {"clinic": intake_schema(True), "helpdesk": intake_schema(False)},
        "models": ["gliner", "jpt"] if os.getenv("JPT_URL") or not os.getenv("SPACE_ID") else ["gliner"],
    }


class RunCancelled(Exception):
    pass


@app.api(name="run_demo", concurrency_limit=1, stream_every=0.1)
def stream_demo(
    workflow: str,
    model: str = "gliner",
    mode: str = "single",
    subject: str = "",
    message: str = "",
) -> Iterator[dict[str, Any]]:
    """Stream model decisions, routing records, and browser screenshots."""
    if workflow not in DEMOS or model not in demo_config()["models"]:
        raise ValueError("Choose an available workflow and model.")
    if mode not in {"single", "inbox"}:
        raise ValueError("Choose a single request or the sample inbox.")
    simulated = workflow in {"clinic", "helpdesk"}
    tickets = None
    if simulated and mode == "single":
        if not subject.strip() or not message.strip():
            raise ValueError("Add a subject and message before sending a request.")
        if len(subject) > 200 or len(message) > 4000:
            raise ValueError("Use at most 200 characters for the subject and 4,000 for the message.")
        sample = next(
            (ticket for ticket in load_tickets(workflow == "clinic")
             if ticket["subject"] == subject and ticket["message"] == message),
            None,
        )
        prefix = "CL" if workflow == "clinic" else "HD"
        tickets = [{
            "id": sample["id"] if sample else f"{prefix}-{uuid4().hex[:8].upper()}",
            "subject": subject.strip(),
            "message": message.strip(),
        }]
    events: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    started = time.monotonic()

    def publish(record: dict[str, Any], screenshot: bytes | None) -> None:
        if cancelled.is_set():
            raise RunCancelled()
        events.put((record, screenshot))

    def worker() -> None:
        try:
            client = make_client(model, GLINER_URL, JPT_URL)
            if simulated:
                artifact = run_intake_simulation(
                    client, clinic=workflow == "clinic", tickets=tickets, on_event=publish
                )
                publish({"event": "run_finished", "artifact": artifact.name}, None)
                return
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1440, "height": 900}
                )
                page = context.new_page()
                try:
                    artifact = DEMOS[workflow](page, client, publish)
                    publish(
                        {"event": "run_finished", "artifact": artifact.name},
                        page.screenshot(type="jpeg", quality=72),
                    )
                finally:
                    context.close()
                    browser.close()
        except RunCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            events.put(({"event": "run_failed", "error": str(exc)}, None))
        finally:
            events.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    records = []
    latest_image = None
    try:
        while True:
            item = events.get()
            if item is None:
                break
            record, screenshot = item
            records.append(record)
            if screenshot:
                latest_image = "data:image/jpeg;base64," + base64.b64encode(screenshot).decode("ascii")
            yield {
                "events": list(records),
                "screenshot": latest_image,
                "elapsed_s": round(time.monotonic() - started, 2),
            }
    finally:
        cancelled.set()
        # Keep the shared queue slot until the browser or model call has stopped.
        # Cancellation takes effect at the next audit event.
        worker_thread.join()


if __name__ == "__main__":
    app.launch(server_name=HOST, server_port=PORT)
