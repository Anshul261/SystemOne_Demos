"""The flow, the same for any EMR, with Jev making every judgment:

1. read: a doctor's free text becomes the patient record (layer1.py): values, Jev's confidence, and flags.
2. plan: the screens the record needs. Each screen is looked at once, with a test patient, and kept as its
   layout: its fields and which record value each holds (mapping.py). The values to review come from it.
3. fill: per screen, the value each field takes, in the field's unit and, for a dropdown, as one of its
   options. A value is flagged when Jev was unsure of it, or of what the field holds.
4. enter and save, screen by screen in order, after review: reach the screen through the EMR's own menus
   (steps.py: Jev's steps, kept and replayed), type the reviewed values and read them back (entry.py), check
   the screen is the patient's, then save with the EMR's own button. Later screens need earlier saves (a
   visit needs the registered patient), so nothing is opened before its turn.

Nothing here is specific to an EMR: the goals that reach and save each screen live with the EMR's target.
"""

import json
from datetime import date
from pathlib import Path

import entry
from forms import label, pin, read_form
from layer1 import FLAG_BELOW
from layer1 import record as read_note
from mapping import map_screen
from models import system_one
from schema import ITEMS, RECORD_UNITS, VALUE
from steps import Procedures, for_patient, form_frame

DATA = Path(__file__).parent / "data"
MAPS, LAYOUTS, PROCEDURES = DATA / "mappings", DATA / "layouts", DATA / "procedures"
CONVERT = {("lb", "kg"): lambda v: v / 2.20462, ("in", "cm"): lambda v: v * 2.54,
           ("F", "C"): lambda v: (v - 32) * 5 / 9}


def field_key(field: dict) -> str:
    """A field the same way each time the screen is read: its control, what describes it, and its position."""
    return f"{field['role']}|{field.get('context') or field.get('row') or field['name']}|{field['name']}|{field.get('nth', 0)}"


def field_keys(fields: list[dict]) -> list[str]:
    """Each field's key, told apart by order where several look the same (a lab screen's Code cells)."""
    seen: dict[str, int] = {}
    keys = []
    for field in fields:
        k = field_key(field)
        keys.append(f"{k}#{seen[k]}" if k in seen else k)
        seen[k] = seen.get(k, 0) + 1
    return keys


def mapping_for(page, fields: list[dict], name: str) -> dict:
    """The screen's saved mapping, extended with any field not seen before (only those are looked at)."""
    path = MAPS / f"{name}.json"
    saved = json.loads(path.read_text()) if path.exists() else {"section": None, "fields": {}}
    keys = field_keys(fields)
    new = [(k, f) for k, f in zip(keys, fields) if k not in saved["fields"]]
    if new:
        mapped = map_screen(page, [f for _, f in new], section=saved["section"])
        saved["section"] = saved["section"] or mapped["section"]
        saved["fields"] |= {k: m for (k, _), m in zip(new, mapped["fields"])}
        MAPS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=1))
    return {"section": saved["section"], "fields": [saved["fields"][k] for k in keys]}


def record_key(mapped: dict) -> str | None:
    """The record key a mapped field takes its value from."""
    if not mapped.get("item"):
        return None
    return mapped["item"] if mapped["attribute"] == VALUE else f"{mapped['item']}.{mapped['attribute']}"


def needs(record: dict, flags: dict, section: str | None) -> bool:
    """Whether the record has anything, or anything to review, for a screen of this section."""
    items = ITEMS.get(section or "", {})
    return any(key.split(".")[0] in items for key in [*record, *flags])


def in_unit(record: dict, item: str, unit: str | None):
    """A record value in the unit a field expects: as the note wrote it when the note used that unit, else
    converted from the record's unit."""
    value = record[item]
    if unit and f"{item}@{unit}" in record:
        return record[f"{item}@{unit}"]
    usual = RECORD_UNITS.get(item)
    if unit and usual and unit != usual and (usual, unit) in CONVERT:
        return round(CONVERT[(usual, unit)](float(value)), 1)
    return value


# A dropdown option is checked with a Noul per option ("does choosing it record the value?"): judged alone it is
# cautious on real matches (0.29 for "on the forehead" as "Temporal Artery") but near zero on a clear mismatch
# ("female" as "Other": 0.03). Provisional, from six cases: none below 0.15, review below 0.5.
NO_OPTION, CHECK_OPTION = 0.15, 0.5


def option_for(field: dict, value) -> tuple[int | None, float, str]:
    """The dropdown option that records a value: the same text, else the one Jev picks; with Jev's confidence,
    and why to review it ("" if no reason)."""
    text = str(value).strip().casefold()
    for k, option in enumerate(field["options"]):
        if k and option.strip().casefold() == text:
            return k, 1.0, ""
    options = {option.strip(): k for k, option in enumerate(field["options"]) if k and option.strip()}
    if not options:
        return None, 1.0, f"No option of this field records {value!r}"
    said = f"A field described as: {label(field)}\nThe value to record in it: {value}"
    names = list(options)
    answers = system_one(said, {
        "option": {"type": "choice", "criteria": {name: None for name in names},
                   "instructions": "Which of the field's options records this value, read as an answer to what the "
                                   "field asks?"}} | {
        f"check{k}": {"type": "noul", "instructions": {"task": "Does choosing this option in the field record the value?",
                                                        "option": name}} for k, name in enumerate(names)})
    choice = answers["option"]["choice"]
    p, check = answers["option"]["probabilities"][choice], answers[f"check{names.index(choice)}"]["noul"]
    if check < NO_OPTION:
        return None, 1 - check, f"No option of this field records {value!r}"
    return options[choice], p, f"Jev is not sure {choice!r} records {value!r}" if check < CHECK_OPTION else ""


def fill_values(record: dict, confidence: dict, flags: dict, fields: list[dict], mapped: list[dict]):
    """Per field index: the value to enter, Jev's confidence, and why to review it."""
    values, sure, why = {}, {}, {}
    for i, (field, m) in enumerate(zip(fields, mapped)):
        key = record_key(m)
        if not key or (key not in record and key not in flags):
            continue
        conf = min(c for c in (confidence.get(key), m["confidence"]) if c is not None) if key in confidence else m["confidence"]
        if key in flags:
            why[i] = flags[key]
        if key in record:
            value = in_unit(record, key, m.get("unit")) if key == m["item"] else record[key]
            if field["role"] == "combobox":
                option, p, doubt = option_for(field, value)
                if doubt:
                    why.setdefault(i, doubt)
                if option is None:
                    continue
                value, conf = option, min(conf, p) if conf is not None else p
            values[i] = value if field["role"] == "combobox" else str(value)
        if m.get("review"):
            why.setdefault(i, f"Jev is {m['confidence']:.0%} sure this field is {key}")
        elif conf is not None and conf < FLAG_BELOW and i in values:
            why.setdefault(i, f"Jev is {conf:.0%} sure of this")
        sure[i] = conf
    return values, sure, why


def read(note: str) -> dict:
    record, confidence, flags = read_note(note)
    if any(k in ITEMS["lab_results"] for k in record) and "collection_date" in record and "report_date" not in record:
        record["report_date"] = record["collection_date"]  # a report the note does not date is dated by its sample
    return {"record": record, "confidence": confidence, "flags": flags}


_books: dict[str, Procedures] = {}


def procedures(target) -> Procedures:
    """What Jev has learned about getting around this EMR."""
    if target.name not in _books:
        _books[target.name] = Procedures(PROCEDURES / f"{target.name}.json")
    return _books[target.name]


NAMES = {"first name": "given", "last name": "family", "birth date": "birth_date"}


def patient_of(record: dict, target) -> dict:
    """The patient details the goals name: the note's patient, else the EMR's test patient; and today."""
    found = {k: str(record[v]) for k, v in NAMES.items() if record.get(v)}
    return (found if len(found) == len(NAMES) else dict(target.fixture)) | {"today": date.today().isoformat()}


def reach(page, target, k: int, record: dict, patient: dict, on_step=None):
    """Screen k reached through the EMR's own screens; returns the frame holding its form."""
    screen = target.screens[k]
    if not screen.open:  # a form opened by its address, such as the local one
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
    procedures(target).do(page, target.visit, patient, start=target.home, on_step=on_step)


EVERY_TEST = {code: "" for code in ITEMS["lab_results"]}  # a screen is learned with every panel ordered


def layout(page, target, k: int, on_step=None) -> dict:
    """Screen k's fields and what each holds: looked at once, with the test patient, and kept."""
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


def review(target, k: int, kept: dict, reading: dict) -> dict | None:
    """Screen k filled from the record for review, or None if the record has nothing for it."""
    if not needs(reading["record"], reading["flags"], kept["section"]):
        return None
    values, sure, why = fill_values(reading["record"], reading["confidence"], reading["flags"], kept["fields"],
                                    kept["mapping"])
    return {"k": k, "title": target.screens[k].title or kept["section"], "section": kept["section"],
            "fields": kept["fields"], "mapping": kept["mapping"], "values": values, "confidence": sure, "flags": why}


def match(reviewed: list[dict], reviewed_map: list[dict], live: list[dict], live_map: list[dict]) -> dict[int, int]:
    """Each reviewed field's place on the live screen: the same control if it is there, else the field that
    holds the same record value (the n-th of its kind for the n-th), so a moved or relabelled field is found."""
    keys = {k: j for j, k in enumerate(field_keys(live))}
    holds = lambda m: (record_key(m), m.get("unit"))  # noqa: E731
    found, taken = {}, set()
    for i, k in enumerate(field_keys(reviewed)):
        j = keys.get(k)
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


def enter(page, target, screen: dict, record: dict, patient: dict, on_step=None, on_field=None) -> dict[int, str]:
    """Reach the screen, find each reviewed field on it, type the reviewed values and read them back.
    Returns the fields that did not go in, by their review index, with the reason."""
    frame = reach(page, target, screen["k"], record, patient, on_step)
    _, live = read_form(frame)
    pin(frame, live)
    live_map = mapping_for(frame, live, f"{target.name}-{screen['k']}")["fields"]
    place = match(screen["fields"], screen["mapping"], live, live_map)
    values, back, problems = {}, {}, {}
    covered = {record_key(screen["mapping"][i]) for i in screen["values"] if i in place}
    for i, value in screen["values"].items():
        field, j = screen["fields"][i], place.get(i)
        if j is None:  # fine when the value goes in elsewhere: a report date for a panel not ordered this time
            if record_key(screen["mapping"][i]) not in covered:
                problems[i] = "This field is not on the screen as it was reviewed"
            continue
        if field["role"] == "combobox":
            text = field["options"][value].strip().casefold()
            value = next((n for n, o in enumerate(live[j]["options"]) if n and o.strip().casefold() == text), None)
            if value is None:
                problems[i] = "The screen no longer offers this option"
                continue
        values[j], back[j] = value, i
    step = (lambda event: on_field(event | {"index": back[event["index"]]})) if on_field else None
    problems |= {back[j]: why for j, why in entry.enter(frame, live, values, step).items()}
    screen["frame"], screen["live"] = frame, {i: live[j] for j, i in back.items()}
    return problems


PATIENT_SURE = 0.8  # provisional: a screen is saved only when Jev is this sure it is the patient's


class WrongPatient(RuntimeError):
    pass


def save(page, target, screen: dict, patient: dict, on_step=None) -> str:
    """Check the screen is the patient's, then save it with the EMR's own button."""
    spec = target.screens[screen["k"]]
    if spec.patient:
        sure = for_patient(page, f"{patient['first name']} {patient['last name']}", patient["birth date"])
        if sure < PATIENT_SURE:
            raise WrongPatient(f"Not saved: Jev is only {sure:.0%} sure {spec.title} is "
                               f"{patient['first name']} {patient['last name']}'s")
    if not spec.save_goal:
        return spec.save(page)
    procedures(target).do(page, spec.save_goal, patient, on_step=on_step, done=lambda: submitted(screen))
    return f"Saved {spec.title}"


def submitted(screen: dict) -> bool:
    """Whether the filled form has left the screen: none of the controls marked at entry is there any more."""
    try:
        return screen["frame"].is_detached() or not screen["frame"].locator("[data-entry-pin]").count()
    except Exception:  # noqa: BLE001 - a frame that is navigating counts as left
        return True


def shown_after(page, values: list[str]) -> int:
    """How many entered values the EMR shows once saved: what it displays back, read from every frame."""
    text = ""
    for frame in page.frames:
        try:
            text += frame.evaluate("document.body ? document.body.innerText : ''").casefold() + "\n"
        except Exception:  # noqa: BLE001 - a frame that went away
            pass
    return sum(str(v).strip().casefold() in text for v in values if str(v).strip())
