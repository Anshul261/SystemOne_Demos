from __future__ import annotations

import io
import os
import queue
import threading
import time
from typing import Any

import gradio as gr
from PIL import Image
from playwright.sync_api import sync_playwright

from gliner_client import GlinerClient
from run import DEMOS

GLINER_URL = os.getenv("GLINER_URL", "http://127.0.0.1:8010")
HOST = os.getenv("DEMO_UI_HOST", "127.0.0.1")
PORT = int(os.getenv("DEMO_UI_PORT", "7863"))

DEMO_NAMES = {
    "RPA Challenge": "rpa",
    "ParaBank Claim Payment": "parabank",
    "Piko Resilient Intake": "piko",
    "Piko Excel Challenge": "piko-excel",
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
}

CSS = """
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root { --ink:#e8f5ed; --muted:#86a994; --panel:#0d1712; --line:#203c2c; --lime:#a8f0b8; --amber:#f6c76b; }
.gradio-container { background: radial-gradient(circle at 18% 0%, #193525 0, #08100c 38%, #050806 100%); color:var(--ink); font-family:'DM Sans',sans-serif; }
.app-shell { max-width:1440px !important; margin:auto !important; }
.hero { padding:28px 4px 18px; border-bottom:1px solid var(--line); margin-bottom:18px; }
.eyebrow { color:var(--lime); font:500 12px 'IBM Plex Mono',monospace; letter-spacing:.16em; text-transform:uppercase; }
.hero h1 { color:#edf9f0 !important; margin:7px 0 5px; font-size:clamp(30px,4vw,54px); line-height:1; letter-spacing:-.04em; }
.hero p { color:var(--muted); max-width:760px; font-size:15px; }
.panel { background:rgba(13,23,18,.88) !important; border:1px solid var(--line) !important; border-radius:3px !important; box-shadow:none !important; }
.panel label, .panel .label-wrap { color:#d8ede0 !important; }
.demo-guide { background:#101d16 !important; border-left:3px solid var(--lime) !important; padding:12px 14px !important; margin:2px 0 12px !important; }
.demo-guide strong { color:#edf9f0; font-size:14px; }
.demo-guide ol { color:#9fc0ac; margin:8px 0 0 18px; padding:0; font-size:13px; line-height:1.5; }
.demo-guide li { margin:3px 0; }
.run-button { background:var(--lime) !important; color:#07110a !important; border:0 !important; font-weight:700 !important; }
.metric { font-family:'IBM Plex Mono',monospace; color:var(--amber); }
.decision-table { font-family:'IBM Plex Mono',monospace; font-size:12px; }
footer { display:none !important; }
"""


def event_row(record: dict[str, Any]) -> list[Any]:
    event = record["event"]
    if event == "field_classification":
        subject = record["field"]["label"]
    else:
        subject = record.get("claim_id", record.get("expected", "workflow"))
    confidence = record.get("confidence")
    return [
        event.replace("_", " "),
        subject,
        record.get("decision", record.get("result", "complete")),
        "" if confidence is None else f"{confidence:.3f}",
    ]


def demo_guide(display_name: str) -> str:
    return DEMO_GUIDES[display_name]


def stream_demo(display_name: str):
    demo_name = DEMO_NAMES[display_name]
    events: queue.Queue = queue.Queue()
    started = time.monotonic()

    def publish(record: dict[str, Any], screenshot: bytes | None) -> None:
        events.put((record, screenshot))

    def worker() -> None:
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                context = browser.new_context(
                    ignore_https_errors=True, viewport={"width": 1440, "height": 900}
                )
                page = context.new_page()
                try:
                    artifact = DEMOS[demo_name](page, GlinerClient(GLINER_URL), publish)
                    events.put(
                        (
                            {"event": "run_finished", "artifact": str(artifact)},
                            page.screenshot(type="jpeg", quality=72),
                        )
                    )
                finally:
                    context.close()
                    browser.close()
        except Exception as exc:  # noqa: BLE001 - surface worker failures in the UI
            events.put(({"event": "run_failed", "error": str(exc)}, None))
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    rows = []
    latest_image = None
    latest_decision = {}
    while True:
        item = events.get()
        if item is None:
            break
        record, screenshot = item
        if screenshot:
            latest_image = Image.open(io.BytesIO(screenshot)).copy()
        if (
            record["event"]
            in {
                "field_classification",
                "business_decision",
                "browser_action",
            }
            or record["event"] == "run_failed"
        ):
            latest_decision = record
        rows.append(event_row(record))
        elapsed = time.monotonic() - started
        decisions = sum(
            row[0] in {"field classification", "business decision", "browser action"}
            for row in rows
        )
        status = (
            f"**RUNNING** · `{display_name}` · "
            f"<span class='metric'>{elapsed:.1f}s / {decisions} decisions</span>"
        )
        if record["event"] == "run_finished":
            status = f"**PASSED** · `{display_name}` · <span class='metric'>{elapsed:.1f}s</span>"
        elif record["event"] == "run_failed":
            status = f"**FAILED** · `{record['error']}`"
        yield status, latest_image, rows, latest_decision


with gr.Blocks(css=CSS, title="GLiNER Browser Lab") as app:
    gr.HTML(
        """
        <div class="hero">
          <div class="eyebrow">SystemOne Demos / local GLiNER decisions</div>
          <h1>GLiNER Browser Lab</h1>
          <p>Watch a text classifier map form fields and choose bounded actions while Playwright executes the workflow.</p>
        </div>
        """
    )
    with gr.Row(elem_classes="app-shell"):
        with gr.Column(scale=1, elem_classes="panel"):
            demo = gr.Radio(list(DEMO_NAMES), value="RPA Challenge", label="Workflow")
            guide = gr.HTML(
                value=demo_guide("RPA Challenge"), elem_classes="demo-guide"
            )
            run_button = gr.Button(
                "Run selected demo", variant="primary", elem_classes="run-button"
            )
            status = gr.Markdown("**IDLE** · GLiNER endpoint ready for a run")
            latest = gr.JSON(label="Latest decision")
        with gr.Column(scale=2, elem_classes="panel"):
            screenshot = gr.Image(label="Live browser", interactive=False, height=520)
    decisions = gr.Dataframe(
        headers=["Event", "Field / state", "GLiNER decision", "Confidence"],
        datatype=["str", "str", "str", "str"],
        interactive=False,
        wrap=True,
        elem_classes="decision-table panel",
    )
    demo.change(demo_guide, inputs=demo, outputs=guide)
    run_button.click(
        stream_demo,
        inputs=demo,
        outputs=[status, screenshot, decisions, latest],
    )


if __name__ == "__main__":
    if HOST not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("DEMO_UI_HOST must be a loopback address")
    app.queue(default_concurrency_limit=1).launch(server_name=HOST, server_port=PORT)
