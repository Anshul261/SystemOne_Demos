from __future__ import annotations

import base64
import json
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

from collections import OrderedDict

from fastapi import HTTPException
from fastapi.responses import Response
from PIL import Image

import document_extract
import lab_report
import stamp_check
from d1_client import D1Client
from extract_client import ExtractClient
from intake import intake_schema, load_tickets, run_intake_simulation
from jpt_client import make_client
from run import DEMOS

GLINER_URL = os.getenv("GLINER_URL", "http://127.0.0.1:8010")
JPT_URL = os.getenv("JPT_URL", "http://127.0.0.1:8085")
D1_URL = os.getenv("D1_URL", "http://127.0.0.1:8020")
EXTRACT_URL = os.getenv("EXTRACT_URL", "http://127.0.0.1:8030")
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
    "Document Stamp Check": "stamp",
    "Long-Span Extraction": "extract",
    "Lab Report Reader": "lab",
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
    "Document Stamp Check": (
        "<strong>Required-stamp verification from an image</strong>"
        "<ol><li>Pick a sample certificate or upload a scan or phone photo.</li>"
        "<li>d1 reads the stamp region and answers narrow visual questions in one forward pass.</li>"
        "<li>Calibrated thresholds and rules accept, reject with a reason, or send to review.</li></ol>"
    ),
    "Long-Span Extraction": (
        "<strong>Spans of any length, located on the page</strong>"
        "<ol><li>Open a real PDF or upload your own.</li>"
        "<li>GLiNER2.5 pairs span starts and ends, so a clause can run as long as it needs.</li>"
        "<li>Every span is mapped back to the lines it covers on the page.</li></ol>"
    ),
    "Lab Report Reader": (
        "<strong>A scanned lab report, read into checked results</strong>"
        "<ol><li>OCR finds every cell on the page.</li>"
        "<li>d1 decides the table's columns, which lines are tests, the units, and the lab's own highlights.</li>"
        "<li>Code pairs each test with its result and range, and flags values outside the range.</li></ol>"
    ),
}

def demo_guide(display_name: str, model: str = "gliner") -> str:
    guide = DEMO_GUIDES[display_name]
    return guide.replace("GLiNER", "JPT") if model == "jpt" else guide


app = gr.Server(title="SystemOne Decision Lab")
app.mount("/assets", StaticFiles(directory=WEB), name="assets")
app.mount("/stamp-samples", StaticFiles(directory=stamp_check.SAMPLES), name="stamp-samples")


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
        "stamp": stamp_config(),
        "extract": {
            "models": [{"id": key, "name": name, "size": document_extract.MODEL_SIZES[key]}
                       for key, name in document_extract.MODEL_NAMES.items()],
            "documents": [{k: v for k, v in doc.items() if k != "file"} for doc in document_extract.load_samples()],
            "max_labels": document_extract.MAX_LABELS,
        },
        "lab": {
            "models": [{"id": "3b", "name": "d1-3B"}, {"id": "omni", "name": "d1-omni-600M"}],
            "documents": lab_samples(),
        },
    }


def lab_samples() -> list[dict[str, Any]]:
    return json.loads((document_extract.DOCUMENTS / "lab_samples.json").read_text(encoding="utf-8"))["documents"]


def stamp_config() -> dict[str, Any]:
    calibration = stamp_check.CALIBRATION["models"]
    return {
        "models": [{"id": key, "name": name} for key, name in stamp_check.MODEL_NAMES.items()],
        "samples": stamp_check.load_samples(),
        "checks": stamp_check.CHECKS,
        "reliability": {
            model: {check: {"calib_auroc": spec["calib_auroc"], "test_auroc": spec["test_auroc"],
                            "trusted": stamp_check.reliable(model, check)}
                    for check, spec in calibration[model]["checks"].items()}
            for model in stamp_check.MODEL_NAMES
        },
        "reliable_auroc": stamp_check.RELIABLE_AUROC,
        "region": stamp_check.REGION,
        "reasons": stamp_check.REASONS,
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


def data_url(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


@app.api(name="check_document", concurrency_limit=1, stream_every=0.1)
def stream_stamp_check(model: str = "omni", mode: str = "single", sample: str = "", upload: str = "") -> Iterator[dict[str, Any]]:
    """Stream each document, the stamp region, every check and the verdict."""
    if model not in stamp_check.MODEL_NAMES:
        raise ValueError("Choose d1-omni-600M or d1-3B.")
    samples = {item["id"]: item for item in stamp_check.load_samples()}
    if mode == "batch":
        chosen = list(samples.values())
    elif mode == "single" and upload:
        chosen = None
    elif mode == "single" and sample in samples:
        chosen = [samples[sample]]
    else:
        raise ValueError("Choose a sample document or upload an image.")
    documents = (
        [(stamp_check.decode_upload(upload), {"id": f"UP-{uuid4().hex[:6].upper()}", "source": "upload"})]
        if chosen is None
        else [(Image.open(stamp_check.SAMPLES / item["file"]).convert("RGB"), {**item, "source": "sample"})
              for item in chosen]
    )
    events: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    started = time.monotonic()

    def publish(record: dict[str, Any], image: bytes | None) -> None:
        if cancelled.is_set():
            raise RunCancelled()
        events.put((record, image))

    def worker() -> None:
        try:
            artifact = stamp_check.run_stamp_check(D1Client(D1_URL), model, documents, on_event=publish)
            publish({"event": "run_finished", "artifact": artifact.name}, None)
        except RunCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            message = str(exc)
            if "Connection refused" in message or "ConnectError" in type(exc).__name__:
                message = f"The d1 service is not reachable at {D1_URL}. Start it with ./start-d1.sh."
            events.put(({"event": "run_failed", "error": message}, None))
        finally:
            events.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    records = []
    current: dict[str, Any] = {}
    try:
        while True:
            item = events.get()
            if item is None:
                break
            record, image = item
            records.append(record)
            # Every update carries the current document's images, so merged stream updates lose nothing.
            if record["event"] == "document_received":
                current = {"doc_id": record["doc_id"], "page": data_url(image) if image else None, "region": None}
            elif record["event"] == "region_selected" and image:
                current = {**current, "region": data_url(image)}
            yield {"events": list(records), "doc_images": current, "elapsed_s": round(time.monotonic() - started, 2)}
    finally:
        cancelled.set()
        worker_thread.join()


PAGE_IMAGES: OrderedDict[str, bytes] = OrderedDict()
PAGE_IMAGES_LOCK = threading.Lock()


@app.get("/extract-pages/{key}.jpg")
def extract_page_image(key: str):
    with PAGE_IMAGES_LOCK:
        image = PAGE_IMAGES.get(key)
    if image is None:
        raise HTTPException(404, "Page image expired; extract the page again.")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


@app.api(name="extract_document", concurrency_limit=1, stream_every=0.1)
def stream_extraction(
    model: str = "multi",
    document: str = "sfip",
    upload: str = "",
    pages: str = "one",
    page: int = 1,
    schema: str = "",
    threshold: float = 0.5,
    segment: bool = True,
) -> Iterator[dict[str, Any]]:
    """Stream each page image, its spans with line boxes, and a run summary."""
    if model not in document_extract.MODEL_NAMES:
        raise ValueError("Choose GLiNER2.5 Multi or Base.")
    if upload:
        pdf = document_extract.decode_document(upload)
        doc = {"id": f"upload-{uuid4().hex[:6]}", "title": "Your document", "source": "upload"}
    else:
        doc = next((d for d in document_extract.load_samples() if d["id"] == document), None)
        if doc is None:
            raise ValueError("Choose a sample document or upload a PDF.")
        pdf = (document_extract.DOCUMENTS / doc["file"]).read_bytes()
    try:
        labels = json.loads(schema) if schema else doc.get("schema", {})
    except json.JSONDecodeError as exc:
        raise ValueError("The label schema is not valid JSON.") from exc
    labels = document_extract.validate_schema(labels)
    if pages not in {"one", "all"} or page < 1:
        raise ValueError("Choose one page or all pages.")
    if not 0.05 <= threshold <= 0.99:
        raise ValueError("Use a threshold between 0.05 and 0.99.")
    events: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    started = time.monotonic()

    def publish(record: dict[str, Any], image: bytes | None) -> None:
        if cancelled.is_set():
            raise RunCancelled()
        if image and record.get("image_key"):
            with PAGE_IMAGES_LOCK:
                PAGE_IMAGES[record["image_key"]] = image
                PAGE_IMAGES.move_to_end(record["image_key"])
                while len(PAGE_IMAGES) > 160:
                    PAGE_IMAGES.popitem(last=False)
        events.put(record)

    def worker() -> None:
        try:
            artifact = document_extract.run_extraction(
                ExtractClient(EXTRACT_URL), model, doc, pdf, "all" if pages == "all" else [page],
                labels, threshold, on_event=publish, segment=bool(segment))
            publish({"event": "run_finished", "artifact": artifact.name}, None)
        except RunCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            message = str(exc)
            if "Connection refused" in message or "ConnectError" in type(exc).__name__:
                message = f"The extraction service is not reachable at {EXTRACT_URL}. Start it with ./start-extract.sh."
            events.put({"event": "run_failed", "error": message})
        finally:
            events.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    records = []
    try:
        while True:
            record = events.get()
            if record is None:
                break
            records.append(record)
            yield {"events": list(records), "elapsed_s": round(time.monotonic() - started, 2)}
    finally:
        cancelled.set()
        worker_thread.join()


@app.api(name="read_lab_report", concurrency_limit=1, stream_every=0.1)
def stream_lab_report(model: str = "3b", document: str = "evya", upload: str = "") -> Iterator[dict[str, Any]]:
    """Stream OCR, each d1 reading stage, the paired records and, for samples, the answer-key check."""
    if model not in {"3b", "omni"}:
        raise ValueError("Choose d1-3B or d1-omni-600M.")
    truth = None
    if upload:
        data = document_extract.decode_document(upload)
        if data.startswith(b"%PDF"):
            raise ValueError("Upload the report as an image (JPEG, PNG or WebP).")
        doc = {"id": f"upload-{uuid4().hex[:6]}", "title": "Your report"}
    else:
        doc = next((d for d in lab_samples() if d["id"] == document), None)
        if doc is None:
            raise ValueError("Choose a sample report or upload one.")
        data = (document_extract.DOCUMENTS / doc["file"]).read_bytes()
        truth = json.loads((document_extract.DOCUMENTS / doc["truth"]).read_text(encoding="utf-8"))
    events: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    started = time.monotonic()
    key = "lab-" + __import__("hashlib").sha256(data).hexdigest()[:16]

    def publish(record: dict[str, Any], image: bytes | None) -> None:
        if cancelled.is_set():
            raise RunCancelled()
        if image:
            with PAGE_IMAGES_LOCK:
                PAGE_IMAGES[key] = image
            record = {**record, "image_key": key}
        events.put(record)

    def worker() -> None:
        try:
            records_holder: dict[str, Any] = {}

            def capture(record: dict[str, Any], image: bytes | None) -> None:
                if record["event"] == "report_read":
                    records_holder["records"] = record["records"]
                publish(record, image)

            artifact = lab_report.run_lab_report(ExtractClient(EXTRACT_URL), D1Client(D1_URL), doc, data, model,
                                                 on_event=capture)
            if truth is not None and "records" in records_holder:
                result = lab_report.score(records_holder["records"], truth)
                publish({"event": "report_scored", "totals": result["totals"], "rows": result["rows"],
                         "records": records_holder["records"], "extra_records": result["extra_records"]}, None)
            publish({"event": "run_finished", "artifact": artifact.name}, None)
        except RunCancelled:
            pass
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            message = str(exc)
            if "Connection refused" in message or "ConnectError" in type(exc).__name__:
                message = "A model service is not reachable. Start ./start-extract.sh and ./start-d1.sh."
            events.put({"event": "run_failed", "error": message})
        finally:
            events.put(None)

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    records = []
    try:
        while True:
            record = events.get()
            if record is None:
                break
            records.append(record)
            yield {"events": list(records), "elapsed_s": round(time.monotonic() - started, 2)}
    finally:
        cancelled.set()
        worker_thread.join()


if __name__ == "__main__":
    app.launch(server_name=HOST, server_port=PORT)
