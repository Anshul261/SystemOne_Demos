"""Chart entry console: a doctor's free text in, reviewed entries saved in the EMR out.

    uv run --env-file .env uvicorn app:app --port 8050

1. Read: Jev turns the note into the patient record (layer1.py).
2. Plan: the EMR screens the record needs, from each screen's layout (looked at once by Jev and kept: emr.py),
   with the value for every field (pipeline.py).
3. Review: every value with how sure Jev is; flagged ones must be kept or corrected; a screen can be left out.
4. Enter and save, screen by screen (emr.py): Jev reaches each screen through the EMR's own menus (steps.py),
   the values are typed and read back, Jev checks the screen is the patient's, and the EMR's own button saves.
   A value the page refuses stops the run before that screen is saved, for the reviewer to fix.

The server enforces the order: no entry with an open flag. One browser serves the console, so it is meant for
one reviewer at a time.
"""

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
import emr
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
        """One browser context per EMR, made on first use. Each review signs in on its own tab."""
        if target.name not in self.contexts:
            self.contexts[target.name] = self.browser.new_context(ignore_https_errors=True,
                                                                  viewport={"width": 1280, "height": 860})
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
    """Read the note, then fill each screen it needs from that screen's layout. Nothing is opened in the EMR
    for this patient yet; a screen never looked at before is looked at once, with the EMR's test patient."""
    if request.target not in TARGETS or not request.note.strip():
        raise HTTPException(422, "Choose an EMR and write or paste a note first.")
    target = TARGETS[request.target]()

    def learn(k):
        page = worker.context(target).new_page()
        try:
            return emr.layout(page, target, k)
        finally:
            page.close()

    def events():
        started = time.perf_counter()
        try:
            for old in [s for s in sessions.values() if s["target"] == request.target]:
                end(old)
            with metered() as spent:
                reading = pipeline.read(request.note)
                yield line("read", started, values=len(reading["record"]), flags=len(reading["flags"]))
                screens = []
                for k, spec in enumerate(target.screens):
                    yield line("looking", started, title=spec.title)
                    planned = pipeline.review(target, k, worker.run(lambda: learn(k)), reading)
                    if planned:
                        screens.append(planned | {"resolved": set(), "skip": False, "entered": False, "saved": False})
                        yield line("screen", started, title=planned["title"], values=len(planned["values"]),
                                   flags=len(planned["flags"]))
            patient = emr.patient_of(reading["record"], target)
            session = {"id": uuid.uuid4().hex, "target": request.target, "name": NAMES.get(request.target),
                       "screens": screens, "record": reading["record"], "patient": patient, "page": None,
                       "visit": False, "spent": spent.report()}
            sessions[session["id"]] = session
            yield line("done", started, session=session["id"], screens=[view(s) for s in screens],
                       checks=spent.system_one_calls, tokens=spent.system_one_tokens,
                       patient=f"{patient.get('first name', '')} {patient.get('last name', '')}".strip())
        except Exception as error:  # noqa: BLE001 - shown in the console instead of a broken stream
            yield line("error", started, message=f"{type(error).__name__}: {error}")

    return StreamingResponse(events(), media_type="application/x-ndjson")


def end(session: dict) -> None:
    if session.get("page"):
        worker.run(session["page"].close)
    sessions.pop(session["id"], None)


class EditRequest(BaseModel):
    session: str
    screen: int
    index: int
    value: str


@app.post("/api/edit")
def edit(request: EditRequest):
    """The reviewer's own value. Before entry it only changes what will be entered; on a screen entered but
    not saved it is typed in and read back straight away."""
    session = session_for(request.session)
    screen = screen_of(session, request.screen)
    if screen["saved"]:
        raise HTTPException(409, "This screen is already saved.")
    field = screen["fields"][request.index]
    if field["role"] == "combobox":
        if request.value not in field["options"]:
            raise HTTPException(422, f"Choose one of the options for {label(field)}.")
        value = field["options"].index(request.value)
    else:
        value = request.value.strip()
    if screen["entered"] and request.index in screen.get("live", {}):
        live = screen["live"][request.index]
        typed = live["options"].index(request.value) if field["role"] == "combobox" else value
        if not worker.run(lambda: fill(screen["frame"], live, typed)):
            raise HTTPException(409, "The page did not keep that value. Check the live screen and try again.")
    if value in ("", 0):
        screen["values"].pop(request.index, None)
    else:
        screen["values"][request.index] = value
    screen["resolved"].add(request.index)
    names = {v: k for k, v in emr.NAMES.items()}  # a corrected name or birth date is the patient's too
    held = pipeline.record_key(screen["mapping"][request.index]) if screen.get("mapping") else None
    if held in names and isinstance(value, str) and value:
        session["patient"][names[held]] = value
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
    """Leave a screen out, or put it back."""
    screen = screen_of(session_for(request.session), request.screen)
    if screen["entered"]:
        raise HTTPException(409, "This screen is already entered.")
    screen["skip"] = request.skip
    return {"screen": view(screen)}


class SessionRequest(BaseModel):
    session: str


VERBS = {"click": "Clicked", "fill": "Typed in", "select": "Chose", "scroll": "Scrolled"}


def described(step: dict) -> str:
    """A step through the EMR in words: "Clicked New/Search"."""
    return "Waited for the page to update" if step["kind"] == "wait" else f"{VERBS[step['kind']]} {step['label']}"


def enter_and_save(session: dict, target, todo: list[dict], say) -> None:
    """The work behind /api/enter, on the browser thread. For each screen in order: today's visit first if
    the screen needs one, then reach the screen and enter its values, stop if the page refused any, else
    check the patient and save. say(event, **details) reports each step to the console."""
    patient = session["patient"]
    who = f"{patient['first name']} {patient['last name']}"
    page = session["page"]
    if page is None:
        page = session["page"] = worker.context(target).new_page()
        if target.home:
            say("moving", text=f"Signing in to {session['name']}")
            target.home(page)

    for screen in todo:
        k, spec = screen["k"], target.screens[screen["k"]]
        say("screen", screen=k, title=screen["title"])

        def moved(step, k=k):  # a step through the EMR's menus, and whether Jev chose it or a saved route did
            say("moving", screen=k, frame=entry.frame(page), text=described(step),
                by_jev=step["probability"] is not None)

        if not screen["entered"]:
            if spec.visit and target.visit and not session["visit"]:
                say("moving", screen=k, text=f"Creating today's visit for {who}")
                emr.make_visit(page, target, patient, moved)
                session["visit"] = True
            problems = emr.enter(page, target, screen, session["record"], patient, moved,
                                 lambda event, k=k: say("field", **event, screen=k))
            screen["entered"] = True
            screen["flags"] |= problems
            screen["resolved"] -= set(problems)
            say("entered", screen=view(screen))
        if open_flags(screen):  # the page refused a value: the reviewer decides before this screen is saved
            say("stopped", screen=view(screen))
            return

        say("moving", screen=k, frame=entry.frame(page), text=f"Checking {screen['title']} is {who}'s, then saving")
        result = emr.save(page, target, screen, patient, moved)
        values = [shown(f, screen["values"][i]) for i, f in screen.get("live", {}).items()]
        screen["saved"] = True
        say("saved", screen=view(screen), result=result, frame=entry.frame(page),
            shown=emr.shown_after(page, values), entered=len(values))
    say("finished")


@app.post("/api/enter")
def start_entry(request: SessionRequest):
    """Enter and save every kept screen in order, streaming each step. Stops before saving a screen whose
    page refused a value; calling again carries on from there once the reviewer has dealt with it."""
    session = session_for(request.session)
    waiting = sum(len(open_flags(s)) for s in kept(session))
    if waiting:
        raise HTTPException(409, f"Review {waiting} more field{'s' * (waiting != 1)} before entering.")
    todo = [s for s in kept(session) if not s["saved"]]
    if not todo:
        raise HTTPException(409, "Nothing is left to enter.")
    target = TARGETS[session["target"]]()
    steps: queue.Queue = queue.Queue()

    def run():
        try:
            enter_and_save(session, target, todo, lambda event, **details: steps.put((event, details)))
        except Exception as error:  # noqa: BLE001 - shown in the console instead of a broken stream
            steps.put(("error", {"message": f"{type(error).__name__}: {error}"}))

    def events():
        started = time.perf_counter()
        threading.Thread(target=lambda: worker.run(run), daemon=True).start()
        while True:
            event, details = steps.get()
            if event == "finished":
                yield line("done", started, screens=[view(s) for s in session["screens"]])
                end(session)
                return
            yield line(event, started, **details)
            if event in ("error", "stopped"):
                return

    return StreamingResponse(events(), media_type="application/x-ndjson")
