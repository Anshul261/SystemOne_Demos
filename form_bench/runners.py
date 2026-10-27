"""The agents under test. Each fills one screen from a note in a page it opens, and the Meter records what it spent.

- jev: jev-ultrafast unchanged, given the note as its goal. System One picks every action; a small LLM writes
  each typed value.
- mapfill: read the form once from Playwright's ARIA snapshot, then
    1. System One chooses every dropdown option from the note, while one LLM call writes every text value,
    2. System One checks each written value against the note, flagging any it cannot confirm,
    3. Playwright fills the form. No model runs while filling.

System One is TypeSafe's Jev when TYPESAFE_API_KEY is set, and otherwise d1 on the local service: the request
shape is the same, so jev-ultrafast's own calls are redirected.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp
from jev_ultrafast import Agent, agent, browser, model
from playwright.sync_api import BrowserContext, Page

TYPESAFE = "https://api.typesafe.ai/v1/systemone"
SYSTEM_ONE = "jev" if os.getenv("TYPESAFE_API_KEY") else "d1"
D1_URL = os.getenv("D1_URL", "http://127.0.0.1:8020")
D1_MODEL = os.getenv("D1_MODEL", "3b")
if SYSTEM_ONE == "d1":
    os.environ["TYPESAFE_API_KEY"] = "local-d1"  # jev-ultrafast reads it; the local service ignores it
LLM_URL = os.getenv("TEXT_MODEL_BASE_URL", "https://api.cerebras.ai/v1").rstrip("/")
LLM_MODEL = os.getenv("TEXT_MODEL", "qwen-3.8-27b")
os.environ.setdefault("TEXT_MODEL_BASE_URL", LLM_URL)
os.environ.setdefault("TEXT_MODEL", LLM_MODEL)
LLM_REASONING = os.getenv("LLM_REASONING", "none")
FLAG_BELOW = 0.5  # System One's confidence that the note states a written value
FIELD = re.compile(r'^\s*- (textbox|spinbutton|combobox|searchbox) "(.+?)"')
OPTION = re.compile(r'^\s*- option "(.+?)"')
GOAL = ("Enter this patient's details into the form exactly as the note states them. Leave any field the note "
        "does not mention empty. Do not press Save or submit the form. Stop when every stated detail is entered.\n"
        "Note:\n{note}")
EXTRACT = ("You fill a clinical form from a note. Return a JSON object with one key per field id. Each value is "
           "the text to enter, copied from the note, or null when the note does not state it. Never infer, "
           "estimate or invent a value. Dates are YYYY-MM-DD. Numbers have no units.")


@dataclass
class Meter:
    started: float = field(default_factory=time.perf_counter)
    system_one_calls: int = 0
    system_one_ms: float = 0
    system_one_tokens: int = 0
    llm_calls: int = 0
    llm_ms: float = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    actions: int = 0
    flagged: list[str] = field(default_factory=list)
    error: str | None = None

    def report(self) -> dict:
        return {k: v for k, v in vars(self).items() if k != "started"} | {
            "wall_ms": round((time.perf_counter() - self.started) * 1000)}


meter: Meter | None = None
_post_json = model.post_json


def post_json(url: str, key: str, body: dict) -> dict:
    """jev-ultrafast's HTTP call, used by both agents: routes System One to local d1 if needed, and meters it."""
    if url == TYPESAFE and SYSTEM_ONE == "d1":
        url, body = f"{D1_URL}/system_one", {**body, "model": D1_MODEL}
    if "api.cerebras.ai" in url:  # Cerebras rejects jev-ultrafast's reasoning field; both agents skip reasoning
        body = {k: v for k, v in body.items() if k != "reasoning"} | {"reasoning_effort": LLM_REASONING}
    started = time.perf_counter()
    result = _post_json(url, key, body)
    elapsed = (time.perf_counter() - started) * 1000
    if meter and url.endswith("/chat/completions"):
        usage = result.get("usage", {})
        meter.llm_calls += 1
        meter.llm_ms += elapsed
        meter.llm_input_tokens += usage.get("prompt_tokens", 0)
        meter.llm_output_tokens += usage.get("completion_tokens", 0)
    elif meter:
        meter.system_one_calls += 1
        meter.system_one_ms += elapsed
        meter.system_one_tokens += result.get("usage", {}).get("input_tokens", 0)
    return result


model.post_json = post_json


@contextmanager
def metered():
    global meter
    meter = Meter()
    try:
        yield meter
    finally:
        meter = None


def system_one(note: str, questions: dict) -> dict:
    url, key = (TYPESAFE, os.environ["TYPESAFE_API_KEY"])
    body = {"model": os.getenv("TYPESAFE_MODEL", "jev-latest"), "state": {"note": note}, "questions": questions}
    return post_json(url, key, body)["answers"]


def llm_json(system: str, user: dict) -> dict:
    body = {"model": LLM_MODEL, "max_tokens": 1024, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}]}
    result = post_json(f"{LLM_URL}/chat/completions", os.environ["TEXT_MODEL_API_KEY"], body)
    return json.loads(result["choices"][0]["message"]["content"])


def read_form(page: Page) -> tuple[str, list[dict]]:
    """The form's ARIA snapshot and its fields: [{role, name, options}], read once per screen."""
    snapshot = page.locator("body").aria_snapshot()
    fields: list[dict] = []
    for line in snapshot.splitlines():
        if match := FIELD.match(line):
            fields.append({"role": match[1], "name": match[2], "options": []})
        elif (match := OPTION.match(line)) and fields and fields[-1]["role"] == "combobox":
            fields[-1]["options"].append(match[1])
    return snapshot, fields


def choose_options(note: str, dropdowns: list[dict]) -> tuple[dict[str, str], list[str]]:
    """System One sets every dropdown in one request; the first option (the form's blank) means not stated.
    Returns the choices and the dropdowns it was unsure of."""
    if not dropdowns:
        return {}, []
    questions = {f"f{i}": {"type": "choice", "criteria": {option: option for option in d["options"]},
                           "instructions": f"Which option should the form field '{d['name']}' be set to, according "
                                           f"to the note? Choose '{d['options'][0]}' if the note does not say."}
                 for i, d in enumerate(dropdowns)}
    answers = system_one(note, questions)
    chosen = {d["name"]: answers[f"f{i}"]["choice"] for i, d in enumerate(dropdowns)
              if answers[f"f{i}"]["choice"] != d["options"][0]}
    unsure = [d["name"] for i, d in enumerate(dropdowns) if answers[f"f{i}"]["confidence"] < FLAG_BELOW]
    return chosen, unsure


def write_values(note: str, snapshot: str, inputs: list[dict]) -> dict[str, str]:
    """One LLM call writes every text value; nulls and unknown ids are dropped."""
    ids = {f"f{i}": f["name"] for i, f in enumerate(inputs)}
    output = llm_json(EXTRACT, {"form": snapshot, "fields": ids, "note": note})
    return {ids[k]: str(v).strip() for k, v in output.items() if k in ids and v not in (None, "")}


def confirm(note: str, values: dict[str, str]) -> list[str]:
    """System One checks each written value against the note; returns the fields it cannot confirm."""
    if not values:
        return []
    names = list(values)
    questions = {f"v{i}": {"type": "noul", "instructions": f"Does the note state that the patient's {name} "
                                                            f"is {values[name]}?"} for i, name in enumerate(names)}
    answers = system_one(note, questions)
    return [name for i, name in enumerate(names) if answers[f"v{i}"]["noul"] < FLAG_BELOW]


def run_mapfill(context: BrowserContext, url: str, note: str, spent: Meter) -> Page:
    page = context.new_page()
    page.goto(url)
    snapshot, fields = read_form(page)
    dropdowns = [f for f in fields if f["role"] == "combobox" and f["options"]]
    inputs = [f for f in fields if f not in dropdowns]
    with ThreadPoolExecutor(2) as pool:
        chosen = pool.submit(choose_options, note, dropdowns)
        written = pool.submit(write_values, note, snapshot, inputs)
        (chosen, unsure), written = chosen.result(), written.result()
    spent.flagged = unsure + confirm(note, written)
    roles = {f["name"]: f["role"] for f in fields}
    for name, value in {**written, **chosen}.items():
        control = page.get_by_role(roles[name], name=name, exact=True)
        control.select_option(label=value) if roles[name] == "combobox" else control.fill(value)
        spent.actions += 1
    return page


class PageBrowser(browser.Browser):
    """jev-ultrafast's browser, attached to a tab Playwright opened instead of a new one, so the scorer sees
    the same page. Playwright has already navigated it."""

    def __init__(self, target_id: str):
        ensure_daemon()
        self.target = None  # Playwright owns the tab and closes it
        self.session = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)


def run_jev(context: BrowserContext, url: str, note: str, spent: Meter) -> Page:
    """jev-ultrafast as shipped, with the note as its goal."""
    page = context.new_page()
    page.goto(url)
    target_id = context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    agent.Browser = lambda _url: PageBrowser(target_id)
    jev = Agent(url, GOAL.format(note=note))
    try:
        for _ in jev.run():
            pass
    except (ValueError, RuntimeError) as error:  # its step budget, or a model error: score what it entered
        spent.error = str(error)
    spent.actions = len(jev.state["history"])
    return page


AGENTS = {"mapfill": run_mapfill, "jev": run_jev}
