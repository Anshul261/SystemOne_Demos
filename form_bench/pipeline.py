"""The flow, the same for any EMR, with Jev making every judgment:

1. read: a doctor's free text becomes the patient record (layer1.py): values, Jev's confidence, and flags.
2. plan: the screens the record needs. Each screen is mapped once by looking at it (mapping.py): what it is
   for and which record value each field holds. The mapping is saved and reused; new fields are mapped as met.
3. fill: per screen, the value each field takes, in the field's unit and, for a dropdown, as one of its
   options. A value is flagged when Jev was unsure of it, or of what the field holds.
4. enter (entry.py) types the reviewed values and reads them back. Nothing here is specific to an EMR: a
   screen's setup and save live with the EMR's target.
"""

import json
from pathlib import Path

from forms import label, open_screen, pin, read_form
from layer1 import FLAG_BELOW
from layer1 import record as read_note
from mapping import NONE, REVIEW_BELOW, map_screen
from models import system_one
from schema import ITEMS, RECORD_UNITS, VALUE

MAPS = Path(__file__).parent / "data" / "mappings"
CONVERT = {("lb", "kg"): lambda v: v / 2.20462, ("in", "cm"): lambda v: v * 2.54,
           ("F", "C"): lambda v: (v - 32) * 5 / 9}


def field_key(field: dict) -> str:
    """A field the same way each time the screen is read: its control, what describes it, and its position."""
    return f"{field['role']}|{field.get('context') or field.get('row') or field['name']}|{field['name']}|{field.get('nth', 0)}"


def mapping_for(page, fields: list[dict], name: str) -> dict:
    """The screen's saved mapping, extended with any field not seen before (only those are looked at)."""
    path = MAPS / f"{name}.json"
    saved = json.loads(path.read_text()) if path.exists() else {"section": None, "fields": {}}
    new = [f for f in fields if field_key(f) not in saved["fields"]]
    if new:
        mapped = map_screen(page, new, section=saved["section"])
        saved["section"] = saved["section"] or mapped["section"]
        saved["fields"] |= {field_key(f): m for f, m in zip(new, mapped["fields"])}
        MAPS.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(saved, indent=1))
    return {"section": saved["section"], "fields": [saved["fields"][field_key(f)] for f in fields]}


def record_key(mapped: dict) -> str | None:
    """The record key a mapped field takes its value from."""
    if not mapped.get("item"):
        return None
    return mapped["item"] if mapped["attribute"] == VALUE else f"{mapped['item']}.{mapped['attribute']}"


def needs(record: dict, flags: dict, section: str | None) -> bool:
    """Whether the record has anything, or anything to review, for a screen of this section."""
    items = ITEMS.get(section or "", {})
    return any(key.split(".")[0] in items for key in [*record, *flags])


def in_unit(value, item: str, unit: str | None):
    """A record value (kept in the record's units) in the unit a field expects."""
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
            value = in_unit(record[key], m["item"], m.get("unit"))
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
    return {"record": record, "confidence": confidence, "flags": flags}


def screen_for(context, target, k: int, reading: dict):
    """Open screen k ready for this record, and what Jev fills into it. Returns (page, fields, mapping, fill)."""
    screen = target.screens[k]
    page = open_screen(context, screen)
    if screen.setup:
        screen.setup(page, reading["record"])
    _, fields = read_form(page)
    pin(page, fields)
    mapping = mapping_for(page, fields, f"{target.name}-{k}")
    values, sure, why = fill_values(reading["record"], reading["confidence"], reading["flags"], fields,
                                    mapping["fields"])
    return page, fields, mapping, (values, sure, why)


def plan(context, target, reading: dict) -> list[dict]:
    """The target's screens this record needs, each opened and filled for review."""
    planned = []
    for k, screen in enumerate(target.screens):
        path = MAPS / f"{target.name}-{k}.json"
        if path.exists() and not needs(reading["record"], reading["flags"], json.loads(path.read_text())["section"]):
            continue
        page, fields, mapping, (values, sure, why) = screen_for(context, target, k, reading)
        if not needs(reading["record"], reading["flags"], mapping["section"]):
            page.close()
            continue
        planned.append({"screen": k, "title": screen.title or mapping["section"], "section": mapping["section"],
                        "page": page, "fields": fields, "mapping": mapping["fields"], "values": values,
                        "confidence": sure, "flags": why})
    return planned
