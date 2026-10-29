"""Getting around an EMR without scripts written for it: Jev picks each step, the way jev-ultrafast does.

observe() reads every frame of the page with jev-ultrafast's snapshot (the visible text and every control,
with the page's own element identity), and pursue() asks jev-ultrafast's Jev questions which operation and
which control move a goal forward, then acts on exactly that element, checking first that it is the one
observed. Text is never generated: when Jev chooses to type, it picks which of the values the caller gave
goes in the field.
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

# jev-ultrafast's snapshot lists links, buttons and form controls. Pages also make plain elements clickable (a
# menu built of divs, as in OpenEMR): those with a click binding in their markup (onclick, Knockout, Angular,
# Vue), focusable ones, and those shown with a pointer cursor, the way browser-use's own reader finds them,
# are marked first and read as menu items (ranked below real buttons). And Jev sees the whole page, not only what is scrolled into view.
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


def _snapshot() -> str:
    source = Path(jev_ultrafast.__file__).with_name("snapshot.js").read_text()
    for old, new in (("roles.map(role=>'[role=\"'+role+'\"]').join(',');",
                      "roles.map(role=>'[role=\"'+role+'\"]').join(',')+',[data-jev-clickable]';"),
                     ("    return null;\n  };", "    if (e.hasAttribute('data-jev-clickable')) return 'menuitem';\n    return null;\n  };"),
                     # the whole page, as a person filling a form sees it by scrolling; a click scrolls to its control
                     ("r.height<=0 || x<0 || y<0 || x>=innerWidth || y>=innerHeight) continue;", "r.height<=0) continue;"),
                     ("r.width>0 && r.height>0 && r.bottom>0 && r.top<innerHeight && r.right>0 && r.left<innerWidth", "r.width>0 && r.height>0"),
                     ("actions.length-250", "actions.length-800"), ("actions.splice(250)", "actions.splice(800)"),
                     # a <button> is named by its text, as browsers name it, not by its value ("true")
                     ("(['button','submit','reset'].includes(e.type) ? e.value : '')",
                      "(e.tagName==='INPUT' && ['button','submit','reset'].includes(e.type) ? e.value : '')")):
        assert old in source, "jev-ultrafast's snapshot changed"
        source = source.replace(old, new)
    return source


SNAPSHOT = _snapshot()
GUARD = "n => window.__jevFast.guard(window.__jevFast.nodes.get(n))"
ELEMENT = "n => window.__jevFast.nodes.get(n)"


def observe(page: Page) -> dict:
    """What a person sees: every frame's visible text and controls, in one list Jev chooses from."""
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


MOST = 150  # what one Jev request can weigh: a long form (a lab screen) has several times more controls


def rank(action: dict) -> int:
    """Which controls Jev sees first on a long page: buttons and links, then empty fields, then the rest.
    Entry types the values itself, so filled fields matter least to getting around."""
    if action["kind"] == "click":
        return 0 if action.get("role") in ("button", "link") else 2
    if action["kind"] == "fill":
        return 1 if not action.get("value") else 4
    return 3


def fit(actions: list[dict]) -> list[dict]:
    """At most MOST controls, the most useful for getting around first, kept in page order. Clicks that only
    focus a field Jev can type in go first."""
    if len(actions) <= MOST:
        return actions
    typed = {a["node"] for a in actions if a["kind"] == "fill"}
    useful = [a for a in actions if not (a["kind"] == "click" and a["node"] in typed)]
    keep = set(map(id, sorted(useful, key=rank)[:MOST]))
    return [a for a in useful if id(a) in keep]


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


class StaleStep(RuntimeError):
    pass


def value_for(goal: str, action: dict, inputs: dict[str, str]) -> str | None:
    """Which of the caller's values goes in the field Jev chose to type in (its name), or None."""
    if not inputs:
        return None
    none = "None of these values belongs in this field."
    answer = system_one(f"Goal: {goal}\nThe field: {action['label']}", {"value": {
        "type": "choice", "criteria": {**{f"{k}: {v}": None for k, v in inputs.items()}, none: None},
        "instructions": "Which of these values should be typed in the field to move the goal forward?"}})["value"]
    return None if answer["choice"] == none else answer["choice"].split(":")[0]


def plain(label: str) -> str:
    return " ".join(label.split())


def planned(seen: dict, step: dict) -> dict | None:
    """The control a saved step acts on, found again by what it is and says, if it is on the page."""
    return next((a for a in seen["actions"] if a["kind"] == step["kind"] and plain(a["label"]) == step["label"]
                 and (step["kind"] != "select" or a["value"] == step["value"])), None)


def answer_popups(page: Page, goal: str, history: list[dict]):
    """Never answer a browser popup blindly (browser-harness's dialogs lesson): an alert only informs and is
    closed; a confirm or prompt ("A visit already exists for this patient today. Continue?") is answered by
    Jev against the goal, and declined unless Jev is sure. Leaving a page is allowed. Each popup goes into the
    history Jev sees. Returns the handler, to remove when done."""
    def handle(dialog):
        accept = dialog.type in ("alert", "beforeunload")
        if not accept:
            yes = system_one(f"Goal: {goal}\nThe page asks: {dialog.message}", {"yes": {
                "type": "noul", "instructions": "Does answering yes (OK) to what the page asks serve the goal, "
                                                "without creating a duplicate or changing anything the goal does not ask for?"}})["yes"]["noul"]
            accept = yes >= POPUP_YES
        history.append({"action": f"The page showed a {dialog.type} popup: {dialog.message[:300]}", "kind": "popup",
                        "text": "OK" if accept else "Cancel", "page_changed": None})
        dialog.accept() if accept else dialog.dismiss()
    page.on("dialog", handle)
    return handle


POPUP_YES = 0.8  # provisional: a confirm is declined unless Jev is this sure answering yes serves the goal


REACHED = 0.8  # provisional: the goal counts as reached, with no further step, when Jev is this sure


def reached(seen: dict, goal: str, steps: list[dict]) -> bool:
    """Whether the goal is reached, from the page and the steps already taken. Asked on its own because the
    step question leans toward acting: it pressed a blank form's Create button when only opening the form
    was asked."""
    taken = "; ".join(f"{s['kind']} {s['label']!r}" for s in steps) or "none yet"
    return system_one(f"Steps already taken: {taken}\n\nThe page now:\n{seen['text']}", {"reached": {"type": "noul", "instructions": {
        "task": "Has this goal been fully reached, as the page shows it now, so that no further step is needed?",
        "goal": goal}}})["reached"]["noul"] >= REACHED


def pursue(page: Page, goal: str, inputs: dict[str, str] | None = None, plan: list[dict] = (),
           avoid: list[list[dict]] = (), budget: int = 14, on_step=None, done=None) -> list[dict]:
    """Take steps until Jev sees the goal reached; returns the steps taken, raises Blocked.

    A plan (the steps that reached this goal before) is followed without asking Jev while its controls are
    on the page; Jev picks any step the plan cannot, and confirms the goal is reached at the end. avoid lists
    earlier attempts that did not reach it, so Jev can choose differently. on_step(step), when given, hears
    each step once it is taken. done(), when given, says from the page itself that the goal is reached."""
    inputs, history, steps, plan = inputs or {}, [], [], list(plan)
    told = goal
    if inputs:
        told += "\nValues you may type: " + "; ".join(f"{k}: {v}" for k, v in inputs.items())
    for path in avoid:
        told += "\nAn earlier attempt took these steps and did not reach the goal: " + " -> ".join(
            f"{s['kind']} {s['label']!r}" for s in path)
    handler = answer_popups(page, goal, history)
    try:
        return _pursue(page, goal, told, inputs, history, steps, plan, budget, on_step, done)
    finally:
        page.remove_listener("dialog", handler)


def _pursue(page, goal, told, inputs, history, steps, plan, budget, on_step, done):
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
        if action:
            step = plan.pop(0)
            text, name = (inputs.get(step["input"]), step["input"]) if step["kind"] == "fill" else (None, None)
            p = None
        else:
            plan = plan[1:]  # a step that is not on the page is chosen afresh (a different patient's row)
            if (done and steps and done()) or reached(seen, goal, steps):
                return steps
            decision = choose(seen, told, history)
            if decision["choice"] == "DONE":
                return steps
            if decision["choice"] == "BLOCKED":
                raise Blocked(f"Jev found no way to: {goal}", steps)
            action = next(a for a in seen["actions"] if a["id"] == decision["choice"])
            p = decision["probabilities"].get(decision["choice"])
            name = value_for(goal, action, inputs) if action["kind"] == "fill" else None
            text = inputs.get(name)
            if action["kind"] == "fill" and text is None:
                history.append({"action": action["label"], "kind": "fill", "text": None, "page_changed": False})
                continue
        try:
            act(seen, action, text)
        except (StaleStep, PlaywrightError) as error:
            history.append({"action": action["label"], "kind": action["kind"], "text": str(error)[:80],
                            "page_changed": False})
            continue
        page.wait_for_timeout(400)
        steps.append({"kind": action["kind"], "label": plain(action["label"]), "value": action.get("value")
                      if action["kind"] == "select" else None, "input": name, "probability": p})
        history.append({"action": action["label"], "kind": action["kind"], "text": text, "page_changed": None})
        if on_step:
            on_step(steps[-1])
    raise Blocked(f"Not reached in {budget} steps: {goal}", steps)


class Blocked(RuntimeError):
    """The goal could not be reached; args[1] is the steps taken."""


class Procedures:
    """What Jev learned about getting things done in one EMR, kept as data the way browser-harness keeps
    domain skills: a goal and the steps that reached it. Followed without Jev while the screens still match;
    a step that no longer matches is chosen by Jev again, and the steps that worked are saved."""

    def __init__(self, path: Path):
        self.path = path
        self.known = json.loads(path.read_text()) if path.exists() else {}

    def do(self, page: Page, task: str, inputs: dict[str, str] | None = None, start=None, attempts: int = 3,
           on_step=None, done=None) -> dict:
        """Reach a goal: the task with the inputs in it ("Open {first name} {last name}'s chart"), kept under
        the task so the steps serve every patient. start(page), when given, returns to where the task begins,
        for another attempt. Returns {"steps", "asked": whether Jev chose any step, "attempts"}."""
        named = set(re.findall(r"{([^{}]+)}", task))
        inputs = {k: v for k, v in (inputs or {}).items() if k in named}  # a goal may type only what it names
        goal, failed = task.format_map(inputs), []
        for attempt in range(1, attempts + 1):
            try:
                taken = pursue(page, goal, inputs, plan=self.known.get(task, {}).get("steps", []), avoid=failed,
                                on_step=on_step, done=done)
            except Blocked as error:
                failed.append(error.args[1])
                if not start or attempt == attempts:
                    raise
                start(page)
                continue
            kept = [{k: v for k, v in s.items() if k != "probability"} for s in taken]
            if kept != self.known.get(task, {}).get("steps"):
                self.known[task] = {"steps": kept}
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.known, indent=1))
            return {"steps": taken, "asked": any(s["probability"] is not None for s in taken), "attempts": attempt}


FIELDS = "() => [...document.querySelectorAll('input:not([type=hidden]):not([type=button]):not([type=submit]),select,textarea')].filter(e => e.checkVisibility()).length"


def form_frame(page: Page):
    """The frame holding the form on screen: the visible one with the most fields."""
    def fields(frame):
        try:
            shown = frame == page.main_frame or frame.frame_element().is_visible()
            return frame.evaluate(FIELDS) if shown else 0
        except PlaywrightError:
            return 0
    return max(page.frames, key=fields)


def for_patient(page: Page, name: str, born: str) -> float:
    """How sure Jev is that the screen is filling in this patient's chart, from what the page shows."""
    return system_one(observe(page)["text"], {"patient": {"type": "noul", "instructions": {
        "task": "Does the page show this patient (same name and date of birth) as the patient it is for?",
        "patient": f"{name}, born {born}"}}})["patient"]["noul"]
