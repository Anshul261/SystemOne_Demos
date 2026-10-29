"""Working in the EMR after review, screen by screen: reach each screen through the EMR's own menus, type the
reviewed values and read them back, check the screen shows the right patient, and save with the EMR's own
button. Nothing here is specific to an EMR; the goals that reach and save each screen are written in its
target (targets.py). ARCHITECTURE.md explains the choices.

Why screen by screen: later screens need earlier saves. Vitals belong to a visit, and a visit needs the
registered patient, so a screen can only be opened once the ones before it are saved. The reviewer therefore
reviews values against each screen's saved layout (layout), and on the live screen each reviewed field is
found again (match) before anything is typed.
"""

import json
from datetime import date
from pathlib import Path

import entry
from forms import pin, read_form
from pipeline import ITEMS, field_keys, mapping_for, record_key
from steps import Procedures, for_patient, form_frame

DATA = Path(__file__).parent / "data"
LAYOUTS, PROCEDURES = DATA / "layouts", DATA / "procedures"
PATIENT_SURE = 0.8  # provisional: a screen is saved only when Jev is this sure it shows the patient


# The patient the goals name ----------------------------------------------------------------------------------

NAMES = {"first name": "given", "last name": "family", "birth date": "birth_date"}  # goal value -> record item


def patient_of(record: dict, target) -> dict:
    """The values goals may name ({first name}, {last name}, {birth date}, {today}): the note's patient, or the
    EMR's test patient when the note does not name one fully."""
    found = {k: str(record[v]) for k, v in NAMES.items() if record.get(v)}
    return (found if len(found) == len(NAMES) else dict(target.fixture)) | {"today": date.today().isoformat()}


# Getting to a screen -----------------------------------------------------------------------------------------

_books: dict[str, Procedures] = {}


def procedures(target) -> Procedures:
    """The routes Jev has learned in this EMR (data/procedures/<target>.json)."""
    if target.name not in _books:
        _books[target.name] = Procedures(PROCEDURES / f"{target.name}.json")
    return _books[target.name]


def reach(page, target, k: int, record: dict, patient: dict, on_step=None):
    """Screen k, reached the way its target says: goals Jev reaches through the EMR's menus, and any setup
    between them (placing lab orders). Returns the frame holding the screen's form."""
    screen = target.screens[k]
    if not screen.open:  # a form opened by its address, such as the local intake form
        page.goto(screen.url)
        if screen.prepare:
            screen.prepare(page)
        return page.main_frame
    for part in screen.open:
        if callable(part):
            part(page, record)
        else:
            procedures(target).do(page, part, patient, start=target.home, on_step=on_step)
    page.wait_for_timeout(500)
    return form_frame(page)


def make_visit(page, target, patient: dict, on_step=None) -> None:
    """Today's visit for the patient, which screens inside a visit (vitals, lab results) need."""
    procedures(target).do(page, target.visit, patient, start=target.home, on_step=on_step)


# A screen's layout, for review -------------------------------------------------------------------------------

EVERY_TEST = {code: "" for code in ITEMS["lab_results"]}  # a lab screen is learned with every panel ordered


def layout(page, target, k: int, on_step=None) -> dict:
    """Screen k's fields and what each holds (mapping.py), looked at once with the EMR's test patient and kept
    in data/layouts. Review uses it, since the patient's own screen cannot be opened before its turn."""
    path = LAYOUTS / f"{target.name}-{k}.json"
    if path.exists():
        return json.loads(path.read_text())
    patient = dict(target.fixture) | {"today": date.today().isoformat()}
    if target.home:
        target.home(page)
    if target.screens[k].visit and target.visit:
        make_visit(page, target, patient, on_step)
    frame = reach(page, target, k, EVERY_TEST, patient, on_step)
    _, fields = read_form(frame)
    mapped = mapping_for(frame, fields, f"{target.name}-{k}")
    kept = {"section": mapped["section"], "fields": fields, "mapping": mapped["fields"]}
    LAYOUTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(kept, indent=1))
    return kept


# Entering the reviewed values --------------------------------------------------------------------------------

def match(reviewed: list[dict], reviewed_map: list[dict], live: list[dict], live_map: list[dict]) -> dict[int, int]:
    """Where each reviewed field is on the live screen: the same control if it is there and holds the same
    record value, else a field of the same kind that holds it (the n-th such for the n-th), so a moved or
    relabelled field is still found. A lab row's label carries its order date, for one."""
    holds = lambda m: (record_key(m), m.get("unit"))  # noqa: E731
    same_control = {k: j for j, k in enumerate(field_keys(live))}
    found, taken = {}, set()
    for i, k in enumerate(field_keys(reviewed)):
        j = same_control.get(k)
        if j is not None and holds(live_map[j]) == holds(reviewed_map[i]):
            found[i] = j
            taken.add(j)
    for i, field in enumerate(reviewed):
        if i in found or not record_key(reviewed_map[i]):
            continue
        j = next((j for j, f in enumerate(live) if j not in taken and f["role"] == field["role"]
                  and holds(live_map[j]) == holds(reviewed_map[i])), None)
        if j is not None:
            found[i] = j
            taken.add(j)
    return found


def place(screen: dict, live: list[dict], where: dict[int, int]) -> tuple[dict, dict, dict]:
    """The reviewed values as the live screen takes them: {live index: value}, {live index: review index},
    and {review index: why it cannot go in}. A dropdown value is the reviewed option's text, found again among
    the live options. A value with no field of its own is fine when another field enters the same record
    value: the layout has a report date for every lab panel, and a note orders only some."""
    values, back, problems = {}, {}, {}
    entered = {record_key(screen["mapping"][i]) for i in screen["values"] if i in where}
    for i, value in screen["values"].items():
        field, j = screen["fields"][i], where.get(i)
        if j is None:
            if record_key(screen["mapping"][i]) not in entered:
                problems[i] = "This field is not on the screen as it was reviewed"
            continue
        if field["role"] == "combobox":
            text = field["options"][value].strip().casefold()
            value = next((n for n, o in enumerate(live[j]["options"]) if n and o.strip().casefold() == text), None)
            if value is None:
                problems[i] = "The screen no longer offers this option"
                continue
        values[j], back[j] = value, i
    return values, back, problems


def enter(page, target, screen: dict, record: dict, patient: dict, on_step=None, on_field=None) -> dict[int, str]:
    """Reach the screen, find each reviewed field on it, type the reviewed values and read them back.
    Returns the fields that did not go in, by review index, with the reason. Keeps the frame and the live
    fields on the screen dict, for corrections and for save."""
    frame = reach(page, target, screen["k"], record, patient, on_step)
    _, live = read_form(frame)
    pin(frame, live)  # entry finds each control by this mark, and save sees the form leave by it
    live_map = mapping_for(frame, live, f"{target.name}-{screen['k']}")["fields"]
    values, back, problems = place(screen, live, match(screen["fields"], screen["mapping"], live, live_map))
    tell = (lambda event: on_field(event | {"index": back[event["index"]]})) if on_field else None
    problems |= {back[j]: why for j, why in entry.enter(frame, live, values, tell).items()}
    screen["frame"], screen["live"] = frame, {i: live[j] for j, i in back.items()}
    return problems


# Saving ------------------------------------------------------------------------------------------------------

class WrongPatient(RuntimeError):
    """The screen does not clearly show the patient the note is about, so it was not saved."""


def save(page, target, screen: dict, patient: dict, on_step=None) -> str:
    """Check the screen shows the patient, then save it with the EMR's own button."""
    spec = target.screens[screen["k"]]
    who = f"{patient['first name']} {patient['last name']}"
    if spec.patient:
        sure = for_patient(page, who, patient["birth date"])
        if sure < PATIENT_SURE:
            raise WrongPatient(f"Not saved: Jev is only {sure:.0%} sure {spec.title} is {who}'s")
    if not spec.save_goal:
        return spec.save(page)
    procedures(target).do(page, spec.save_goal, patient, on_step=on_step, done=lambda: submitted(screen))
    return f"Saved {spec.title}"


def submitted(screen: dict) -> bool:
    """Whether the filled form has left the screen: none of the controls marked at entry is there any more.
    A save's own sign it went through, for pages that confirm nothing in words (a review list that empties)."""
    try:
        return screen["frame"].is_detached() or not screen["frame"].locator("[data-entry-pin]").count()
    except Exception:  # noqa: BLE001 - a frame that is navigating counts as left
        return True


def shown_after(page, values: list[str]) -> int:
    """How many entered values the EMR shows once saved, read from every frame. A rough sign only: a page
    after saving need not show every value (a new patient's chart shows the name, not the address)."""
    text = ""
    for frame in page.frames:
        try:
            text += frame.evaluate("document.body ? document.body.innerText : ''").casefold() + "\n"
        except Exception:  # noqa: BLE001 - a frame that went away
            pass
    return sum(str(v).strip().casefold() in text for v in values if str(v).strip())
