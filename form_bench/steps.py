"""Getting around an EMR without scripts written for it: Jev picks each step, and the steps that worked are
kept and replayed. See ARCHITECTURE.md for why it is built this way.

The pieces, in the order a step happens:

1. See the page (observe). jev-ultrafast's page snapshot, run in every frame: the visible text and every
   control, each with the page's own element identity. A few patches make it see what a person sees.
2. Choose a step. A saved route's next step, if its control is on the page. Otherwise Jev, through
   jev-ultrafast's questions: which operation, and which control.
3. Act (act). On exactly the element that was observed, after checking it has not changed.
4. Stop. When Jev, given the page and the steps taken, says the goal is reached (or the caller's own check
   says so), when three steps change nothing, or when the step budget runs out.

Procedures keeps, per goal, the steps that reached it, as data (the way browser-harness keeps domain skills).

Two rules hold throughout. Jev never writes text: when it types, it picks which of the caller's values goes
in the field, and a goal is offered only the values it names. And no popup is answered blindly.
"""

import json
import re
import time
from pathlib import Path

import jev_ultrafast
from jev_ultrafast.model import choose
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from models import system_one

# Provisional thresholds, each chosen from a handful of runs on OpenEMR (ARCHITECTURE.md, "Thresholds").
REACHED = 0.8  # the goal counts as reached when Jev is this sure
POPUP_YES = 0.8  # a confirm popup is answered OK only when Jev is this sure that serves the goal
MOST = 150  # controls one Jev request can weigh; a lab screen has several hundred
BUDGET = 14  # steps per attempt at a goal
ATTEMPTS = 3  # attempts at a goal, each told which earlier paths failed


# 1. Seeing the page ------------------------------------------------------------------------------------------

# Pages make plain elements clickable too: OpenEMR's menus are divs with a Knockout click binding. Mark those
# before the snapshot runs, the way browser-use's own reader finds them: a click binding in the markup
# (onclick, Knockout, Angular, Vue), focusable, or a pointer cursor its parent does not share.
MARK_CLICKABLE = """(() => { if (!document.body) return;
  const native = 'a[href],button,input,select,textarea,summary,label,[role],[data-jev-clickable]';
  for (const e of document.body.querySelectorAll('*')) {
    if (e.matches(native) || e.closest('a[href],button,[data-jev-clickable]')) continue;
    if (!e.innerText?.trim()) continue;
    const bound = e.hasAttribute('onclick') || e.hasAttribute('ng-click') || e.hasAttribute('v-on:click') ||
      e.hasAttribute('@click') || /(^|[,{\\s])click\\s*:/.test(e.getAttribute('data-bind') || '') || e.tabIndex >= 0;
    if (!bound && (getComputedStyle(e).cursor !== 'pointer' ||
        (e.parentElement && getComputedStyle(e.parentElement).cursor === 'pointer'))) continue;
    e.setAttribute('data-jev-clickable', '');
  } })()"""

# Changes to jev-ultrafast's snapshot.js, each (why, its text, ours). Applied to its source when this module
# loads; an assertion stops everything if jev-ultrafast changes underneath.
SNAPSHOT_PATCHES = [
    ("read the marked elements",
     "roles.map(role=>'[role=\"'+role+'\"]').join(',');",
     "roles.map(role=>'[role=\"'+role+'\"]').join(',')+',[data-jev-clickable]';"),
    ("as menu items, so real buttons and links rank above them",
     "    return null;\n  };",
     "    if (e.hasAttribute('data-jev-clickable')) return 'menuitem';\n    return null;\n  };"),
    ("controls on the whole page, not only the viewport: a person filling a form scrolls to its Save button",
     "r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;",
     "r.height<=0) continue;"),
    ("text on the whole page too",
     "r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth",
     "r.width>0 && r.height>0"),
    ("room for a whole long page (fit() then cuts it to what one request can weigh)",
     "actions.length-250", "actions.length-800"),
    ("", "actions.splice(250)", "actions.splice(800)"),
    ("a <button> named by its text, as browsers name it: OpenEMR's confirm button has value=\"true\"",
     "(['button','submit','reset'].includes(e.type) ? e.value : '')",
     "(e.tagName==='INPUT' && ['button','submit','reset'].includes(e.type) ? e.value : '')"),
]


def _snapshot() -> str:
    source = Path(jev_ultrafast.__file__).with_name("snapshot.js").read_text()
    for _why, theirs, ours in SNAPSHOT_PATCHES:
        assert theirs in source, f"jev-ultrafast's snapshot changed: {theirs!r} is gone"
        source = source.replace(theirs, ours)
    return source


SNAPSHOT = _snapshot()
GUARD = "n => window.__jevFast.guard(window.__jevFast.nodes.get(n))"  # what the element looks like now
ELEMENT = "n => window.__jevFast.nodes.get(n)"


def observe(page: Page) -> dict:
    """The page as Jev sees it: every frame's text and controls in one list, each control tagged with its
    frame. Returns {url, title, text, actions, frames, guards, look}; look is what "the page changed" means."""
    texts, actions, scrolls, frames, guards = [], [], [], [], {}
    for frame in page.frames:
        try:
            frame.evaluate(MARK_CLICKABLE)
            state = frame.evaluate(SNAPSHOT)
        except PlaywrightError:  # a frame that is navigating or gone
            continue
        usable = [a for a in (state or {}).get("actions", []) if a["kind"] in ("click", "fill", "select")]
        if not usable:  # a hidden tab or an empty frame
            continue
        k = len(frames)
        frames.append(frame)
        texts.append(state["text"])
        actions += [{**action, "frame": k, "node": f"{k}:{action['node']}"} for action in usable]
        where = f" in {state['title']!r}" if frame.parent_frame and state["title"] else ""
        scrolls += [{**action, "frame": k, "id": f"{action['id']}_{k}", "label": action["label"] + where}
                    for action in state["actions"] if action["kind"] == "scroll"]
        guards |= {f"{k}:{node}": guard for node, guard in state["guards"].items()}
    actions = fit(actions)
    for n, action in enumerate(actions, 1):
        action["id"] = f"e{n}"
    actions += scrolls + [{"id": "wait", "kind": "wait", "label": "Wait for the page to update"}]
    text = "\n".join(texts)[:6000]
    return {"url": page.url, "title": page.title(), "text": text, "actions": actions, "frames": frames,
            "guards": guards, "look": (text, tuple((a["label"], a.get("value")) for a in actions))}


def rank(action: dict) -> int:
    """Which controls matter most for getting around: buttons and links, then empty fields, then the rest.
    Filled fields matter least, since entry types the values itself."""
    if action["kind"] == "click":
        return 0 if action.get("role") in ("button", "link") else 2
    if action["kind"] == "fill":
        return 1 if not action.get("value") else 4
    return 3


def fit(actions: list[dict]) -> list[dict]:
    """At most MOST controls, the highest ranked, kept in page order. Clicks that only focus a field Jev can
    type in go first. (A whole lab screen exceeded both Jev's 255 options per choice and its token limit.)"""
    if len(actions) <= MOST:
        return actions
    typed = {a["node"] for a in actions if a["kind"] == "fill"}
    useful = [a for a in actions if not (a["kind"] == "click" and a["node"] in typed)]
    keep = set(map(id, sorted(useful, key=rank)[:MOST]))
    return [a for a in useful if id(a) in keep]


FIELDS = ("() => [...document.querySelectorAll('input:not([type=hidden]):not([type=button]):not([type=submit]),"
          "select,textarea')].filter(e => e.checkVisibility()).length")


def form_frame(page: Page):
    """The frame holding the form on screen: the visible one with the most fields."""
    def fields(frame):
        try:
            shown = frame == page.main_frame or frame.frame_element().is_visible()
            return frame.evaluate(FIELDS) if shown else 0
        except PlaywrightError:
            return 0
    return max(page.frames, key=fields)


# 2. Acting ---------------------------------------------------------------------------------------------------

class StaleStep(RuntimeError):
    """The element changed between being chosen and being acted on."""


def act(seen: dict, action: dict, text: str | None = None) -> None:
    """Do one action on the element it was chosen for, if that element is still as it was seen."""
    if action["kind"] == "wait":
        time.sleep(0.5)
        return
    if action["kind"] == "scroll":
        seen["frames"][action["frame"]].evaluate("d => window.scrollBy(0, d)", action["delta"])
        return
    frame, node = seen["frames"][action["frame"]], int(action["node"].split(":")[1])
    if frame.evaluate(GUARD, node) != seen["guards"][action["node"]]:
        raise StaleStep("The page changed since this step was chosen")
    element = frame.evaluate_handle(ELEMENT, node).as_element()
    if action["kind"] == "click":
        element.click(timeout=5000)
    elif action["kind"] == "select":
        element.select_option(value=action["value"], timeout=5000)
    else:
        element.fill(text, timeout=5000)


# 3. Jev's judgments, besides choosing a step ----------------------------------------------------------------

def value_for(goal: str, action: dict, inputs: dict[str, str]) -> str | None:
    """Which of the caller's values goes in the field Jev chose to type in: its name, or None."""
    if not inputs:
        return None
    none = "None of these values belongs in this field."
    answer = system_one(f"Goal: {goal}\nThe field: {action['label']}", {"value": {
        "type": "choice", "criteria": {**{f"{k}: {v}": None for k, v in inputs.items()}, none: None},
        "instructions": "Which of these values should be typed in the field to move the goal forward?"}})["value"]
    return None if answer["choice"] == none else answer["choice"].split(":")[0]


def reached(seen: dict, goal: str, steps: list[dict]) -> bool:
    """Whether the goal is reached, judged from the page and the steps already taken. Asked separately
    because jev-ultrafast's step question leans toward acting (it pressed a blank form's Create button when
    only opening the form was asked), and with the steps because a page alone may not show it (an emptied
    review list after Sign Results)."""
    taken = "; ".join(f"{s['kind']} {s['label']!r}" for s in steps) or "none yet"
    answer = system_one(f"Steps already taken: {taken}\n\nThe page now:\n{seen['text']}", {"reached": {
        "type": "noul", "instructions": {
            "task": "Has this goal been fully reached, as the page shows it now, so that no further step is needed?",
            "goal": goal}}})
    return answer["reached"]["noul"] >= REACHED


def answer_popups(page: Page, goal: str, history: list[dict]):
    """Answer the page's popups while pursuing a goal, never blindly (browser-harness's lesson on dialogs).
    An alert only informs, and leaving a page is allowed. A confirm or prompt ("A visit already exists for
    this patient today. Continue?") is answered OK only when Jev is sure that serves the goal. Each popup goes
    into the history Jev sees. Returns the handler, to remove when the goal is done."""
    def handle(dialog):
        ok = dialog.type in ("alert", "beforeunload")
        if not ok:
            yes = system_one(f"Goal: {goal}\nThe page asks: {dialog.message}", {"yes": {
                "type": "noul", "instructions": "Does answering yes (OK) to what the page asks serve the goal, "
                                                "without creating a duplicate or changing anything the goal does not ask for?"}})
            ok = yes["yes"]["noul"] >= POPUP_YES
        history.append({"action": f"The page showed a {dialog.type} popup: {dialog.message[:300]}", "kind": "popup",
                        "text": "OK" if ok else "Cancel", "page_changed": None})
        dialog.accept() if ok else dialog.dismiss()
    page.on("dialog", handle)
    return handle


def for_patient(page: Page, name: str, born: str) -> float:
    """How sure Jev is that the page shows this patient as the one it is for. (Asked as "is the form being
    filled in for this patient" it gave 0.65 for the right patient; this wording gave 0.90, and 0.02 for a
    wrong name or birth date.)"""
    return system_one(observe(page)["text"], {"patient": {"type": "noul", "instructions": {
        "task": "Does the page show this patient (same name and date of birth) as the patient it is for?",
        "patient": f"{name}, born {born}"}}})["patient"]["noul"]


# 4. Pursuing a goal ------------------------------------------------------------------------------------------

class Blocked(RuntimeError):
    """The goal could not be reached; args[1] is the steps taken."""


def plain(label: str) -> str:
    return " ".join(label.split())


def planned(seen: dict, step: dict) -> dict | None:
    """The control a saved step acts on, found again by its kind and what it says, if it is on the page."""
    return next((a for a in seen["actions"] if a["kind"] == step["kind"] and plain(a["label"]) == step["label"]
                 and (step["kind"] != "select" or a["value"] == step["value"])), None)


def briefing(goal: str, inputs: dict[str, str], avoid: list[list[dict]]) -> str:
    """The goal as jev-ultrafast's step question is told it: with the values it may type and the paths
    that already failed."""
    told = goal
    if inputs:
        told += "\nValues you may type: " + "; ".join(f"{k}: {v}" for k, v in inputs.items())
    for path in avoid:
        told += "\nAn earlier attempt took these steps and did not reach the goal: " + " -> ".join(
            f"{s['kind']} {s['label']!r}" for s in path)
    return told


def pursue(page: Page, goal: str, inputs: dict[str, str] | None = None, plan: list[dict] = (),
           avoid: list[list[dict]] = (), budget: int = BUDGET, on_step=None, done=None) -> list[dict]:
    """Take steps until the goal is reached; returns the steps taken, raises Blocked.

    plan: the steps that reached this goal before, followed without asking Jev while their controls are on
    the page. A planned step that is not there (a different patient's row) is chosen afresh by Jev.
    avoid: earlier attempts that failed, so Jev can choose differently.
    on_step(step): told of each step once taken. done(): the caller's own sign the goal is reached."""
    inputs, plan = inputs or {}, list(plan)
    told = briefing(goal, inputs, avoid)
    history: list[dict] = []  # what jev-ultrafast's step question sees: steps, failed tries and popups
    steps: list[dict] = []  # what was done, as kept in a route
    handler = answer_popups(page, goal, history)
    try:
        last = None
        for _ in range(budget):
            seen = observe(page)
            moves = [h for h in history if h["kind"] != "popup"]
            if moves:
                moves[-1]["page_changed"] = seen["look"] != last
                if len(moves) >= 3 and not any(h["page_changed"] for h in moves[-3:]):
                    raise Blocked(f"Three steps changed nothing on the way to: {goal}", steps)
            last = seen["look"]

            action = planned(seen, plan[0]) if plan else None
            if action:  # the saved route's next step
                step = plan.pop(0)
                name = step["input"] if step["kind"] == "fill" else None
                probability = None
            else:  # Jev's step
                plan = plan[1:]
                if (done and steps and done()) or reached(seen, goal, steps):
                    return steps
                decision = choose(seen, told, history)
                if decision["choice"] == "DONE":
                    return steps
                if decision["choice"] == "BLOCKED":
                    raise Blocked(f"Jev found no way to: {goal}", steps)
                action = next(a for a in seen["actions"] if a["id"] == decision["choice"])
                probability = decision["probabilities"].get(decision["choice"])
                name = value_for(goal, action, inputs) if action["kind"] == "fill" else None
                if action["kind"] == "fill" and name not in inputs:  # nothing it may type belongs there
                    history.append({"action": action["label"], "kind": "fill", "text": None, "page_changed": False})
                    continue
            text = inputs.get(name) if name else None

            try:
                act(seen, action, text)
            except (StaleStep, PlaywrightError) as error:
                history.append({"action": action["label"], "kind": action["kind"], "text": str(error)[:80],
                                "page_changed": False})
                continue
            page.wait_for_timeout(400)
            steps.append({"kind": action["kind"], "label": plain(action["label"]),
                          "value": action.get("value") if action["kind"] == "select" else None,
                          "input": name, "probability": probability})
            history.append({"action": action["label"], "kind": action["kind"], "text": text, "page_changed": None})
            if on_step:
                on_step(steps[-1])
        raise Blocked(f"Not reached in {budget} steps: {goal}", steps)
    finally:
        page.remove_listener("dialog", handler)


# 5. Remembering routes ---------------------------------------------------------------------------------------

class Procedures:
    """What Jev learned about getting things done in one EMR, kept as data the way browser-harness keeps
    domain skills: per task, the steps that last reached it. A route is rewritten whenever a run takes
    different steps, so it follows the EMR as it changes."""

    def __init__(self, path: Path):
        self.path = path
        self.known = json.loads(path.read_text()) if path.exists() else {}

    def do(self, page: Page, task: str, inputs: dict[str, str] | None = None, start=None, attempts: int = ATTEMPTS,
           on_step=None, done=None) -> dict:
        """Reach a task's goal. A task names the values it needs ("Open {first name} {last name}'s chart") and
        its route is kept under the task, so one route serves every patient. Only the values a task names are
        offered to it: given every value, Jev typed the patient's name into a form it was only asked to open.

        start(page), when given, returns to where the task begins for another attempt; each attempt is told
        the paths that failed. Returns {"steps", "asked": whether Jev chose any step, "attempts"}."""
        named = set(re.findall(r"{([^{}]+)}", task))
        inputs = {k: v for k, v in (inputs or {}).items() if k in named}
        goal, failed = task.format_map(inputs), []
        route = self.known.get(task, {}).get("steps", [])
        for attempt in range(1, attempts + 1):
            try:
                taken = pursue(page, goal, inputs, plan=route, avoid=failed, on_step=on_step, done=done)
            except Blocked as error:
                failed.append(error.args[1])
                if not start or attempt == attempts:
                    raise
                start(page)
                continue
            self.keep(task, taken)
            return {"steps": taken, "asked": any(s["probability"] is not None for s in taken), "attempts": attempt}

    def keep(self, task: str, taken: list[dict]) -> None:
        route = [{k: v for k, v in s.items() if k != "probability"} for s in taken]
        if route != self.known.get(task, {}).get("steps"):
            self.known[task] = {"steps": route}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.known, indent=1))
