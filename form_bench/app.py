"""Chart entry console: the two workflows, with a person between them.

    uv run --env-file .env uvicorn app:app --port 8050

1. Extract: read the chosen EHR screen's fields and pull checked values for them out of the visit note.
2. Review: every value beside its evidence in the note; flagged values must be kept or corrected.
3. Enter: type the approved values into the EHR, streaming a frame per field so the person watches it happen.
4. Save: only after entry, once anything the page refused has been reviewed.

The server enforces the order: no entry with an open flag, no save before entry. One browser serves the
console, so it is meant for one reviewer at a time.
"""

import base64
import json
import queue
import random
import threading
import time
import uuid
from concurrent.futures import Future
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
from pydantic import BaseModel

import entry
from cases import load_cases
from extraction import extract
from forms import fill, label, open_screen, read_form, shown
from models import LLM_MODEL, SYSTEM_ONE, metered
from targets import TARGETS

STATIC = Path(__file__).parent / "static"
NAMES = {"local": "the local intake form", "openemr": "OpenEMR"}


class Worker:
    """Playwright's sync API stays on one thread: every browser job runs here, one at a time."""

    def __init__(self):
        self.jobs: queue.Queue = queue.Queue()
        self.contexts: dict = {}
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self):
        with sync_playwright() as playwright:
            self.browser = playwright.chromium.launch()
            while True:
                job, done = self.jobs.get()
                try:
                    done.set_result(job())
                except Exception as error:  # noqa: BLE001 - handed back to the request that asked
                    done.set_exception(error)

    def run(self, job):
        done = Future()
        self.jobs.put((job, done))
        return done.result()

    def context(self, target):
        """One logged-in browser context per EHR, made on first use."""
        if target.name not in self.contexts:
            context = self.browser.new_context(ignore_https_errors=True, viewport={"width": 1120, "height": 780})
            if target.login:
                target.login(context)
            self.contexts[target.name] = context
        return self.contexts[target.name]


worker = Worker()
sessions: dict[str, dict] = {}
app = FastAPI(title="Chart entry")


def open_flags(session: dict) -> list[int]:
    return [i for i in session["flags"] if i not in session["resolved"]]


def row(session: dict, i: int) -> dict:
    field, value = session["fields"][i], session["values"].get(i)
    return {"index": i, "label": label(field), "kind": "choice" if field["role"] == "combobox" else "text",
            "options": field["options"], "value": "" if value is None else shown(field, value),
            "flag": session["flags"].get(i), "resolved": i in session["resolved"]}


def rows(session: dict) -> list[dict]:
    """Every field that has a value or a flag, in the form's order."""
    return [row(session, i) for i in sorted(set(session["values"]) | set(session["flags"]))]


def line(event: str, started: float, **details) -> str:
    return json.dumps({"step": event, "ms": round((time.perf_counter() - started) * 1000), **details}) + "\n"


def session_for(session_id: str) -> dict:
    if session_id not in sessions:
        raise HTTPException(404, "This review has ended. Extract the note again.")
    return sessions[session_id]


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/api/targets")
def targets():
    available = []
    for name, make in TARGETS.items():
        try:
            target = make()
        except KeyError:  # an EHR without its settings in .env
            continue
        available.append({"id": name, "name": NAMES.get(name, name),
                          "screens": [{"index": k, "title": s.title or s.url} for k, s in enumerate(target.screens)]})
    return {"targets": available, "system_one": SYSTEM_ONE, "llm": LLM_MODEL}


@app.get("/api/sample")
def sample():
    """A synthetic patient's note, to try the console without real data."""
    case = random.choice(load_cases(77))
    return {"id": case.id, "note": case.note}


class ExtractRequest(BaseModel):
    target: str
    screen: int
    note: str


@app.post("/api/extract")
def start_extraction(request: ExtractRequest):
    """Workflow 1. The screen is opened only to read its fields; nothing is typed into it."""
    if request.target not in TARGETS or not request.note.strip():
        raise HTTPException(422, "Choose a screen and paste a note first.")
    target = TARGETS[request.target]()
    screen = target.screens[request.screen]

    def events():
        started = time.perf_counter()
        try:
            for old in [s for s in sessions.values() if s["target"] == request.target]:
                worker.run(old["page"].close)
                sessions.pop(old["id"], None)
            page = worker.run(lambda: open_screen(worker.context(target), screen))
            snapshot, fields = worker.run(lambda: read_form(page))
            yield line("read", started, fields=len(fields), title=screen.title)
            with metered() as spent:
                values, flags = extract(request.note, snapshot, fields)
            session = {"id": uuid.uuid4().hex, "target": request.target, "name": NAMES.get(request.target),
                       "screen": screen, "page": page, "fields": fields, "values": values, "flags": flags,
                       "resolved": set(), "entered": False}
            sessions[session["id"]] = session
            yield line("extract", started, values=len(values), flags=len(flags),
                       checks=spent.system_one_calls, llm_calls=spent.llm_calls)
            yield line("done", started, session=session["id"], rows=rows(session))
        except Exception as error:  # noqa: BLE001 - shown in the console instead of a broken stream
            yield line("error", started, message=f"{type(error).__name__}: {error}")

    return StreamingResponse(events(), media_type="application/x-ndjson")


class EditRequest(BaseModel):
    session: str
    index: int
    value: str


@app.post("/api/edit")
def edit(request: EditRequest):
    """The reviewer's own value. Before entry it only changes what will be entered; after entry it is typed in
    and read back straight away."""
    session = session_for(request.session)
    field = session["fields"][request.index]
    if field["role"] == "combobox":
        if request.value not in field["options"]:
            raise HTTPException(422, f"Choose one of the options for {label(field)}.")
        value = field["options"].index(request.value)
    else:
        value = request.value.strip()
    if session["entered"]:
        if not worker.run(lambda: fill(session["page"], field, value)):
            raise HTTPException(409, "The page did not keep that value. Check the live screen and try again.")
    if value in ("", 0):
        session["values"].pop(request.index, None)
    else:
        session["values"][request.index] = value
    session["resolved"].add(request.index)
    return {"row": row(session, request.index)}


class SessionRequest(BaseModel):
    session: str
    index: int | None = None


@app.post("/api/confirm")
def confirm(request: SessionRequest):
    """The reviewer checked a flagged value and is keeping it."""
    session = session_for(request.session)
    session["resolved"].add(request.index)
    return {"row": row(session, request.index)}


@app.post("/api/enter")
def start_entry(request: SessionRequest):
    """Workflow 2. Types the approved values, one frame per field, then reads every value back."""
    session = session_for(request.session)
    if waiting := open_flags(session):
        raise HTTPException(409, f"Review {len(waiting)} more field{'s' * (len(waiting) != 1)} before entering.")
    if session["entered"]:
        raise HTTPException(409, "These values are already entered. Save, or extract again.")
    steps: queue.Queue = queue.Queue()

    def run():
        try:
            steps.put(("problems", entry.enter(session["page"], session["fields"], session["values"], steps.put)))
        except Exception as error:  # noqa: BLE001
            steps.put(("error", f"{type(error).__name__}: {error}"))

    def events():
        started = time.perf_counter()
        threading.Thread(target=lambda: worker.run(run), daemon=True).start()
        while True:
            step = steps.get()
            if isinstance(step, dict):
                yield line("field", started, **step)
                continue
            kind, result = step
            if kind == "error":
                yield line("error", started, message=result)
                return
            session["entered"] = True
            session["flags"] |= result
            session["resolved"] -= set(result)
            final = worker.run(lambda: base64.b64encode(
                session["page"].screenshot(full_page=True, type="jpeg", quality=70)).decode())
            yield line("done", started, rows=rows(session), screenshot=final)
            return

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/save")
def save(request: SessionRequest):
    session = session_for(request.session)
    if not session["entered"]:
        raise HTTPException(409, "Enter the values before saving.")
    if waiting := open_flags(session):
        raise HTTPException(409, f"Review {len(waiting)} more field{'s' * (len(waiting) != 1)} before saving.")
    if not session["screen"].save:
        raise HTTPException(422, "This screen cannot be saved from the console.")
    result = worker.run(lambda: session["screen"].save(session["page"]))
    if not result:
        raise HTTPException(502, "The EHR did not confirm the save. Check it before trying again.")
    worker.run(session["page"].close)
    sessions.pop(request.session)
    return {"saved": result}
