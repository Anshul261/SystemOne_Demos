"""From the note to the value each screen field takes, the same for any EMR, with Jev making every judgment.

1. read: a doctor's free text becomes the patient record (layer1.py): values, Jev's confidence, and flags.
2. plan: the screens the record needs, from each screen's layout (its fields and which record value each
   holds, mapped by Jev from screenshots: mapping.py, cached here).
3. fill: per screen, the value each field takes, in the field's unit and, for a dropdown, as one of its
   options. A value is flagged when Jev was unsure of it, or of what the field holds.

What happens in the EMR (reaching each screen, entering, checking the patient, saving) is emr.py.
"""

import json
from pathlib import Path

from forms import label
from layer1 import FLAG_BELOW
from layer1 import record as read_note
from mapping import map_screen
from models import system_one
from schema import ITEMS, RECORD_UNITS, VALUE

MAPS = Path(__file__).parent / "data" / "mappings"
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


def review(target, k: int, kept: dict, reading: dict) -> dict | None:
    """Screen k filled from the record for review, using its saved layout (emr.layout), or None if the
    record has nothing for it."""
    if not needs(reading["record"], reading["flags"], kept["section"]):
        return None
    values, sure, why = fill_values(reading["record"], reading["confidence"], reading["flags"], kept["fields"],
                                    kept["mapping"])
    return {"k": k, "title": target.screens[k].title or kept["section"], "section": kept["section"],
            "fields": kept["fields"], "mapping": kept["mapping"], "values": values, "confidence": sure, "flags": why}
