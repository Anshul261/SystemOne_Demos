"""Chart entry console: paste a visit note, let mapfill fill an EHR screen, review every value, then save.

    uv run --env-file .env uvicorn app:app --port 8050

Nothing is saved until every flagged field has been confirmed or corrected; the server enforces that.
One browser serves the console, so it is meant for one reviewer at a time.
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

import runners
from cases import load_cases
from targets import TARGETS

STATIC = Path(__file__).parent / "static"
NAMES = {"local": "Local intake form", "openemr": "OpenEMR"}
OUTLINE = {"flag": "#B0245C", "ok": "#13756B"}


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


def row(session: dict, i: int) -> dict:
    field, value = session["fields"][i], session["values"].get(i)
    return {"index": i, "label": runners.label(field), "kind": "choice" if field["role"] == "combobox" else "text",
            "options": field["options"], "value": "" if value is None else runners.shown(field, value),
            "flag": session["flags"].get(i), "resolved": i in session["resolved"]}


def rows(session: dict) -> list[dict]:
    """Every field that was filled or flagged, in the form's order."""
    return [row(session, i) for i in sorted(set(session["values"]) | set(session["flags"]))]


def screenshot(session: dict) -> str:
    """The EHR screen as it stands, with filled fields outlined in green and fields to review in magenta."""
    page = session["page"]
    for i in set(session["values"]) | set(session["flags"]):
        colour = OUTLINE["flag"] if i in session["flags"] and i not in session["resolved"] else OUTLINE["ok"]
        try:
            runners.locate(page, session["fields"][i]).evaluate(
                f"e => {{ e.style.outline = '3px solid {colour}'; e.style.outlineOffset = '1px' }}", timeout=1000)
        except Exception:  # noqa: BLE001 - a control the page has since removed
            pass
    return base64.b64encode(page.screenshot(full_page=True, type="jpeg", quality=70)).decode()


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
    return {"targets": available, "system_one": runners.SYSTEM_ONE, "llm": runners.LLM_MODEL}


@app.get("/api/sample")
def sample():
    """A synthetic patient's note, to try the console without real data."""
    case = random.choice(load_cases(77))
    return {"id": case.id, "note": case.note}


class FillRequest(BaseModel):
    target: str
    screen: int
    note: str


@app.post("/api/fill")
def fill(request: FillRequest):
    if request.target not in TARGETS or not request.note.strip():
        raise HTTPException(422, "Choose a screen and paste a note first.")
    target = TARGETS[request.target]()
    screen = target.screens[request.screen]

    def events():
        started = time.perf_counter()

        def step(name: str, **details):
            return json.dumps({"step": name, "ms": round((time.perf_counter() - started) * 1000), **details}) + "\n"

        try:
            for old in [s for s in sessions.values() if s["target"] == request.target]:
                worker.run(old["page"].close)
                sessions.pop(old["id"], None)
            page = worker.run(lambda: runners.open_screen(worker.context(target), screen))
            yield step("open", title=screen.title)
            with runners.metered() as spent:
                fields, values, flags = worker.run(lambda: runners.propose(page, request.note))
            yield step("propose", fields=len(fields), values=len(values), flags=len(flags),
                       system_one_calls=spent.system_one_calls, llm_calls=spent.llm_calls)
            worker.run(lambda: runners.enter(page, fields, values, flags))
            session = {"id": uuid.uuid4().hex, "target": request.target, "screen": screen, "page": page,
                       "fields": fields, "values": values, "flags": flags, "resolved": set()}
            sessions[session["id"]] = session
            yield step("enter", entered=len(values), flags=len(flags))
            yield step("done", session=session["id"], rows=rows(session), screenshot=worker.run(lambda: screenshot(session)))
        except Exception as error:  # noqa: BLE001 - shown in the console instead of a broken stream
            yield step("error", message=f"{type(error).__name__}: {error}")

    return StreamingResponse(events(), media_type="application/x-ndjson")


def session_for(session_id: str) -> dict:
    if session_id not in sessions:
        raise HTTPException(404, "This review has ended. Fill the form again.")
    return sessions[session_id]


class EditRequest(BaseModel):
    session: str
    index: int
    value: str


@app.post("/api/edit")
def edit(request: EditRequest):
    """Enter the reviewer's value, read it back, and count the field as reviewed."""
    session = session_for(request.session)
    field = session["fields"][request.index]
    if field["role"] == "combobox":
        if request.value not in field["options"]:
            raise HTTPException(422, f"Choose one of the options for {runners.label(field)}.")
        value = field["options"].index(request.value)
    else:
        value = request.value.strip()
    if not worker.run(lambda: runners.fill(session["page"], field, value)):
        raise HTTPException(409, "The page did not keep that value. Check the screenshot and try again.")
    session["values"][request.index] = value
    session["resolved"].add(request.index)
    return {"row": row(session, request.index), "screenshot": worker.run(lambda: screenshot(session))}


class ConfirmRequest(BaseModel):
    session: str
    index: int


@app.post("/api/confirm")
def confirm(request: ConfirmRequest):
    """The reviewer checked a flagged value and is keeping it."""
    session = session_for(request.session)
    session["resolved"].add(request.index)
    return {"row": row(session, request.index), "screenshot": worker.run(lambda: screenshot(session))}


class SaveRequest(BaseModel):
    session: str


@app.post("/api/save")
def save(request: SaveRequest):
    session = session_for(request.session)
    open_flags = [i for i in session["flags"] if i not in session["resolved"]]
    if open_flags:
        raise HTTPException(409, f"Review {len(open_flags)} more field{'s' * (len(open_flags) != 1)} before saving.")
    if not session["screen"].save:
        raise HTTPException(422, "This screen cannot be saved from the console.")
    result = worker.run(lambda: session["screen"].save(session["page"]))
    if not result:
        raise HTTPException(502, "The EHR did not confirm the save. Check it before trying again.")
    worker.run(session["page"].close)
    sessions.pop(request.session)
    return {"saved": result}
