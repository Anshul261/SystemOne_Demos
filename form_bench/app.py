"""Chart entry console: a doctor's free text in, reviewed entries in the EMR out.

    uv run --env-file .env uvicorn app:app --port 8050

1. Read: Jev turns the note into the patient record (layer1.py).
2. Plan: the EMR screens the record needs, each mapped by Jev from what it looks like (pipeline.py), with the
   value for every field it fills.
3. Review: every value with how sure Jev is; flagged ones must be kept or corrected; a screen can be left out.
4. Enter: the kept screens are typed in, one live frame per field, and read back. 5. Save.

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
import pipeline
from cases import load_cases
from forms import fill, label, shown
from models import SYSTEM_ONE, metered
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


def open_flags(screen: dict) -> list[int]:
    return [i for i in screen["flags"] if i not in screen["resolved"]]


def row(screen: dict, i: int) -> dict:
    field, value = screen["fields"][i], screen["values"].get(i)
    return {"screen": screen["k"], "index": i, "label": label(field),
            "kind": "choice" if field["role"] == "combobox" else "text", "options": field["options"],
            "value": "" if value is None else shown(field, value), "flag": screen["flags"].get(i),
            "resolved": i in screen["resolved"], "confidence": screen["confidence"].get(i)}


def view(screen: dict) -> dict:
    """A screen for the reviewer: every field that has a value or a flag, in the screen's order."""
    return {"screen": screen["k"], "title": screen["title"], "section": screen["section"], "skip": screen["skip"],
            "entered": screen["entered"], "saved": screen["saved"],
            "rows": [row(screen, i) for i in sorted(set(screen["values"]) | set(screen["flags"]))]}


def line(event: str, started: float, **details) -> str:
    return json.dumps({"step": event, "ms": round((time.perf_counter() - started) * 1000), **details}) + "\n"


def session_for(session_id: str) -> dict:
    if session_id not in sessions:
        raise HTTPException(404, "This review has ended. Read the note again.")
    return sessions[session_id]


def screen_of(session: dict, k: int) -> dict:
    found = next((s for s in session["screens"] if s["k"] == k), None)
    if not found:
        raise HTTPException(404, "That screen is not part of this review.")
    return found


def kept(session: dict) -> list[dict]:
    return [s for s in session["screens"] if not s["skip"]]


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
        except KeyError:  # an EMR without its settings in .env
            continue
        available.append({"id": name, "name": NAMES.get(name, name),
                          "screens": [{"index": k, "title": s.title or s.url} for k, s in enumerate(target.screens)]})
    return {"targets": available, "system_one": SYSTEM_ONE}


@app.get("/api/sample")
def sample():
    """A synthetic patient's note, to try the console without real data."""
    case = random.choice(load_cases(77))
    return {"id": case.id, "note": case.note}


class ReadRequest(BaseModel):
    target: str
    note: str


@app.post("/api/read")
def read(request: ReadRequest):
    """Read the note, then open, map and fill each screen it needs. Nothing is typed into the EMR yet."""
    if request.target not in TARGETS or not request.note.strip():
        raise HTTPException(422, "Choose an EMR and write or paste a note first.")
    target = TARGETS[request.target]()

    def events():
        started = time.perf_counter()
        try:
            for old in [s for s in sessions.values() if s["target"] == request.target]:
                for screen in old["screens"]:
                    worker.run(screen["page"].close)
                sessions.pop(old["id"], None)
            with metered() as spent:
                reading = pipeline.read(request.note)
                yield line("read", started, values=len(reading["record"]), flags=len(reading["flags"]))
                screens = []
                for k, screen in enumerate(target.screens):
                    yield line("looking", started, title=screen.title)
                    planned = worker.run(lambda: pipeline.plan_screen(worker.context(target), target, k, reading))
                    if planned:
                        screens.append(planned | {"resolved": set(), "skip": False, "entered": False, "saved": False})
                        yield line("screen", started, title=planned["title"], values=len(planned["values"]),
                                   flags=len(planned["flags"]))
            session = {"id": uuid.uuid4().hex, "target": request.target, "name": NAMES.get(request.target),
                       "screens": screens, "spent": spent.report()}
            sessions[session["id"]] = session
            yield line("done", started, session=session["id"], screens=[view(s) for s in screens],
                       checks=spent.system_one_calls, tokens=spent.system_one_tokens)
        except Exception as error:  # noqa: BLE001 - shown in the console instead of a broken stream
            yield line("error", started, message=f"{type(error).__name__}: {error}")

    return StreamingResponse(events(), media_type="application/x-ndjson")


class EditRequest(BaseModel):
    session: str
    screen: int
    index: int
    value: str


@app.post("/api/edit")
def edit(request: EditRequest):
    """The reviewer's own value. Before entry it only changes what will be entered; after entry it is typed in
    and read back straight away."""
    screen = screen_of(session_for(request.session), request.screen)
    field = screen["fields"][request.index]
    if field["role"] == "combobox":
        if request.value not in field["options"]:
            raise HTTPException(422, f"Choose one of the options for {label(field)}.")
        value = field["options"].index(request.value)
    else:
        value = request.value.strip()
    if screen["entered"]:
        if not worker.run(lambda: fill(screen["page"], field, value)):
            raise HTTPException(409, "The page did not keep that value. Check the live screen and try again.")
    if value in ("", 0):
        screen["values"].pop(request.index, None)
    else:
        screen["values"][request.index] = value
    screen["resolved"].add(request.index)
    return {"row": row(screen, request.index)}


class FieldRequest(BaseModel):
    session: str
    screen: int
    index: int


@app.post("/api/confirm")
def confirm(request: FieldRequest):
    """The reviewer checked a flagged value and is keeping it."""
    screen = screen_of(session_for(request.session), request.screen)
    screen["resolved"].add(request.index)
    return {"row": row(screen, request.index)}


class SkipRequest(BaseModel):
    session: str
    screen: int
    skip: bool


@app.post("/api/skip")
def skip(request: SkipRequest):
    """Leave a screen out of entry, or put it back."""
    screen = screen_of(session_for(request.session), request.screen)
    if screen["entered"]:
        raise HTTPException(409, "This screen is already entered.")
    screen["skip"] = request.skip
    return {"screen": view(screen)}


class SessionRequest(BaseModel):
    session: str


@app.post("/api/enter")
def start_entry(request: SessionRequest):
    """Types the approved values of every kept screen, one frame per field, then reads each screen back."""
    session = session_for(request.session)
    waiting = sum(len(open_flags(s)) for s in kept(session))
    if waiting:
        raise HTTPException(409, f"Review {waiting} more field{'s' * (waiting != 1)} before entering.")
    todo = [s for s in kept(session) if not s["entered"]]
    if not todo:
        raise HTTPException(409, "Nothing is left to enter.")
    steps: queue.Queue = queue.Queue()

    def run():
        try:
            for screen in todo:
                steps.put(("start", screen))
                steps.put(("problems", screen, entry.enter(screen["page"], screen["fields"], screen["values"],
                                                           lambda step, k=screen["k"]: steps.put(step | {"screen": k}))))
            steps.put(("finished",))
        except Exception as error:  # noqa: BLE001
            steps.put(("error", f"{type(error).__name__}: {error}"))

    def events():
        started = time.perf_counter()
        threading.Thread(target=lambda: worker.run(run), daemon=True).start()
        while True:
            step = steps.get()
            if isinstance(step, dict):
                yield line("field", started, **step)
            elif step[0] == "start":
                yield line("screen", started, screen=step[1]["k"], title=step[1]["title"])
            elif step[0] == "problems":
                screen, problems = step[1], step[2]
                screen["entered"] = True
                screen["flags"] |= problems
                screen["resolved"] -= set(problems)
                yield line("entered", started, screen=view(screen))
            elif step[0] == "error":
                yield line("error", started, message=step[1])
                return
            else:
                yield line("done", started, screens=[view(s) for s in session["screens"]])
                return

    return StreamingResponse(events(), media_type="application/x-ndjson")


@app.post("/api/save")
def save(request: SessionRequest):
    """Save every entered screen, in order. Stops at the first one the EMR does not confirm."""
    session = session_for(request.session)
    screens = kept(session)
    if not screens or not all(s["entered"] for s in screens):
        raise HTTPException(409, "Enter every kept screen before saving.")
    waiting = sum(len(open_flags(s)) for s in screens)
    if waiting:
        raise HTTPException(409, f"Review {waiting} more field{'s' * (waiting != 1)} before saving.")
    saved = []
    for screen in screens:
        if screen["saved"]:
            continue
        save_screen = screen["save"]
        if not save_screen:
            raise HTTPException(422, f"{screen['title']} cannot be saved from the console.")
        result = worker.run(lambda: save_screen(screen["page"]))
        if not result:
            raise HTTPException(502, f"The EMR did not confirm saving {screen['title']}. Check it before trying again.")
        screen["saved"] = True
        saved.append(result)
    for screen in session["screens"]:
        worker.run(screen["page"].close)
    sessions.pop(request.session)
    return {"saved": saved}
