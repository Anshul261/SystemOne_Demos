"""Score an extractor on the messy note set, per field and per hard feature, without a browser.

    uv run --env-file .env python notes_v2/evaluate.py cache                     # read OpenEMR's screens once
    uv run --env-file .env python notes_v2/evaluate.py run --extractor llm --split dev
    uv run --env-file .env python notes_v2/evaluate.py run --extractor jev --split test
    uv run --env-file .env python notes_v2/evaluate.py run --extractor layer1 --set notes_v3 --split fresh

`cache` saves each OpenEMR screen's snapshot and fields to notes_v2/forms/. `run` replays them: for every note
and screen, the extractor returns values and flags for the screen's fields; those are mapped to the note's
canonical answer key and compared. Results go to results/notes-v2-<extractor>-<split>-<time>.json.

The test split is for measuring only. Tune on dev.
"""

import argparse
import json
import re
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from forms import read_form, same_control, shown  # noqa: E402
from models import metered  # noqa: E402
from targets import in_row, named  # noqa: E402

HERE = Path(__file__).parent
FORMS = HERE / "forms"
# The canonical key each OpenEMR control holds. A list means any of them may hold it.
KEYS = {
    "new-patient": {
        "given": [named("textbox", "First Name")], "middle": [named("textbox", "Middle Name")],
        "family": [named("textbox", "Last Name")], "birth_date": [named("textbox", "Date of Birth")],
        "sex": [named("combobox", "Birth Sex"), named("combobox", "Sex")],
        "street": [named("textbox", "Street and Number")], "unit": [named("textbox", "Address Line 2")],
        "city": [named("textbox", "City Name")], "state": [named("combobox", "State/Locality")],
        "postal_code": [named("textbox", "Postal Code")]},
    "vitals": {
        "weight": [in_row("textbox", "Weight (LOINC:29463-7) lbs *")],
        "height": [in_row("textbox", "Height/Length (LOINC:8302-2) in *")],
        "bp_systolic": [in_row("textbox", "BP Systolic (LOINC:8480-6) mmHg *")],
        "bp_diastolic": [in_row("textbox", "BP Diastolic (LOINC:8462-4) mmHg *")],
        "pulse": [in_row("textbox", "Pulse (LOINC:8867-4) per min *")],
        "respiration": [in_row("textbox", "Respiration (LOINC:9279-1) per min *")],
        "temperature": [in_row("textbox", "Temperature (LOINC:8310-5) F *")],
        "temp_site": [in_row("combobox", "Temp Location (LOINC:8327-9) *")],
        "oxygen_saturation": [in_row("textbox", "Oxygen Saturation (LOINC:59408-5) % *")]},
}
SCREENS = {"new-patient": 0, "vitals": 1}
TOLERANCE = {"weight": 0.3, "height": 0.3, "temperature": 0.2}
STREET_TYPES = {"st": "street", "ave": "avenue", "rd": "road", "blvd": "boulevard", "dr": "drive", "ln": "lane",
                "ct": "court", "pl": "place", "ter": "terrace", "pkwy": "parkway"}


def cache():
    """Read each OpenEMR screen once and save its snapshot and fields."""
    from playwright.sync_api import sync_playwright

    from forms import open_screen
    from targets import openemr
    target = openemr()
    FORMS.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1120, "height": 780})
        target.login(context)
        for name, index in SCREENS.items():
            page = open_screen(context, target.screens[index])
            snapshot, fields = read_form(page)
            (FORMS / f"{name}.json").write_text(json.dumps({"snapshot": snapshot, "fields": fields}))
            print(f"{name}: {len(fields)} fields")
        browser.close()


def street(text: str) -> str:
    words = re.sub(r"[.,]", "", text).casefold().split()
    return " ".join(STREET_TYPES.get(w, w) for w in words)


def same(key: str, expected, actual: str) -> bool:
    if key in TOLERANCE or key in ("bp_systolic", "bp_diastolic", "pulse", "respiration", "oxygen_saturation"):
        try:
            return abs(float(actual) - float(expected)) <= TOLERANCE.get(key, 0.5)
        except ValueError:
            return False
    if key == "street":
        return street(actual) == street(expected)
    if key == "middle" and re.fullmatch(r"[A-Z]\.?", str(expected)):  # an initial: "K." matches "K" or "Kay"
        return actual.strip()[:1].casefold() == str(expected)[0].casefold()
    if key == "unit":  # "Unit 18", "#18" and "unit 18" are the same unit
        return re.findall(r"\w+$", actual.strip()) == re.findall(r"\w+$", expected.strip())
    if key == "temp_site":  # "oral" against the dropdown's "Oral", "temporal" against "Temporal Artery"
        return bool(actual) and actual.casefold().startswith(expected.casefold()[:5])
    return actual.strip().casefold() == str(expected).strip().casefold()


def score(note: dict, screen: str, fields: list[dict], values: dict, flags: dict, confidence: dict) -> dict:
    """Per canonical key on this screen: expected, actual, verdict, flagged, confidence."""
    out = {}
    for key, controls in KEYS[screen].items():
        held = [(i, shown(fields[i], v)) for i, v in values.items() if any(same_control(fields[i], c) for c in controls)]
        flagged = [i for i in flags if any(same_control(fields[i], c) for c in controls)]
        expected = note["expected"].get(key)
        actual = next((a for _, a in held if expected is not None and same(key, expected, a)), held[0][1] if held else "")
        conf = [confidence[i] for i, _ in held if i in confidence]
        out[key] = judge(note, key, actual) | {"flagged": bool(flagged), "flags": [flags[i] for i in flagged],
                                               "confidence": min(conf) if conf else None}
    return out


def judge(note: dict, key: str, actual: str) -> dict:
    expected = note["expected"].get(key)
    allowed = note.get("acceptable", {}).get(key)
    if allowed and any((a is None and not actual) or (a is not None and actual and same(key, a, actual)) for a in allowed):
        verdict = "right"
    elif expected is None:
        verdict = "invented" if actual else "right"
    elif not actual:
        verdict = "missed"
    else:
        verdict = "right" if same(key, expected, actual) else "wrong"
    return {"expected": expected, "actual": actual, "verdict": verdict}


def score_record(note: dict, screen: str, record: dict, confidence: dict, flags: dict) -> dict:
    """The same scoring for an extractor that returns a record keyed like the answer key (layer1)."""
    out = {}
    for key in KEYS[screen]:
        actual = "" if record.get(key) is None else str(record[key])
        out[key] = judge(note, key, actual) | {"flagged": key in flags, "flags": [flags[key]] if key in flags else [],
                                               "confidence": confidence.get(key)}
    return out


def summarize(rows: list[dict]) -> dict:
    cells = [(row, key, cell) for row in rows for key, cell in row["fields"].items()]
    errors = [(r, k, c) for r, k, c in cells if c["verdict"] != "right"]
    by_key = {}
    for _, key, cell in cells:
        k = by_key.setdefault(key, {"n": 0, "right": 0, "wrong": 0, "missed": 0, "invented": 0, "unflagged": 0})
        k["n"] += 1
        k[cell["verdict"]] += 1
        k["unflagged"] += cell["verdict"] != "right" and not cell["flagged"]
    by_feature = {}
    for row in rows:
        for feature in row["features"]:
            f = by_feature.setdefault(feature, {"fields": 0, "errors": 0, "unflagged": 0})
            for cell in row["fields"].values():
                f["fields"] += 1
                f["errors"] += cell["verdict"] != "right"
                f["unflagged"] += cell["verdict"] != "right" and not cell["flagged"]
    flagged = [c for _, _, c in cells if c["flagged"]]
    return {
        "notes": len({r["id"] for r in rows}), "fields": len(cells),
        "field_accuracy": round(1 - len(errors) / max(len(cells), 1), 4),
        "wrong": sum(c["verdict"] == "wrong" for _, _, c in cells),
        "missed": sum(c["verdict"] == "missed" for _, _, c in cells),
        "invented": sum(c["verdict"] == "invented" for _, _, c in cells),
        "unflagged_errors": sum(not c["flagged"] for _, _, c in errors),
        "flags": len(flagged), "flags_that_were_errors": sum(c["verdict"] != "right" for c in flagged),
        "service_failures": sum(bool(r["error"]) for r in rows),
        "median_ms": round(statistics.median(r["ms"] for r in rows)) if rows else 0,
        "system_one_tokens_per_screen": round(statistics.mean(r["system_one_tokens"] for r in rows)) if rows else 0,
        "system_one_calls_per_screen": round(statistics.mean(r["system_one_calls"] for r in rows), 1) if rows else 0,
        "by_key": by_key, "by_feature": dict(sorted(by_feature.items(), key=lambda kv: -kv[1]["errors"])),
    }


def run(extractor_name: str, split: str, limit: int | None, workers: int, note_set: str = "notes_v2") -> Path:
    if extractor_name == "llm":
        from extraction import extract
    elif extractor_name == "jev":
        from jev_extraction import extract
    elif extractor_name == "layer1":
        from layer1 import record as extract
    else:
        raise SystemExit(f"unknown extractor {extractor_name}")
    forms = {name: json.loads((FORMS / f"{name}.json").read_text()) for name in SCREENS}
    notes = [n for n in map(json.loads, (ROOT / note_set / "notes.jsonl").read_text().splitlines()) if n["split"] == split]
    notes = notes[:limit] if limit else notes

    def one(job):
        note, screen = job
        form = forms[screen]
        started = time.perf_counter()
        with metered() as spent:
            try:
                if extractor_name == "layer1":
                    values, confidence, flags = extract(note["note"])
                else:
                    values, flags, confidence = extract(note["note"], form["snapshot"], form["fields"])
                error = None
            except Exception as e:  # noqa: BLE001 - recorded; the note scores as all missed
                values, flags, confidence, error = {}, {}, {}, f"{type(e).__name__}: {e}"
        return {"id": note["id"], "screen": screen, "features": note["features"], "error": error,
                "ms": round((time.perf_counter() - started) * 1000), "llm_calls": spent.llm_calls,
                "system_one_calls": spent.system_one_calls, "llm_tokens": spent.llm_input_tokens + spent.llm_output_tokens,
                "system_one_tokens": spent.system_one_tokens,
                "fields": (score_record(note, screen, values, confidence, flags) if extractor_name == "layer1"
                           else score(note, screen, form["fields"], values, flags, confidence))}

    jobs = [(note, screen) for note in notes for screen in SCREENS]
    with ThreadPoolExecutor(workers) as pool:
        rows = list(pool.map(one, jobs))
    result = {"extractor": extractor_name, "set": note_set, "split": split, "summary": summarize(rows), "rows": rows}
    path = ROOT / "results" / f"{note_set.replace('_', '-')}-{extractor_name}-{split}-{datetime.now():%Y-%m-%d-%H%M%S}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(result, indent=1))
    return path


def report(path: Path) -> None:
    s = json.loads(path.read_text())["summary"]
    print(f"{s['notes']} notes, {s['fields']} fields: {s['field_accuracy']:.1%} right, wrong {s['wrong']}, "
          f"missed {s['missed']}, invented {s['invented']}, unflagged errors {s['unflagged_errors']}, "
          f"flags {s['flags']} ({s['flags_that_were_errors']} real), median {s['median_ms']} ms per screen, "
          f"service failures {s.get('service_failures', 0)}, "
          f"{s.get('system_one_tokens_per_screen', '?')} Jev tokens in {s.get('system_one_calls_per_screen', '?')} calls per screen")
    print("by field (errors / unflagged):", {k: f"{v['n'] - v['right']}/{v['unflagged']}" for k, v in s["by_key"].items()
                                              if v["n"] - v["right"]})
    print("by feature (errors / unflagged of fields):", {k: f"{v['errors']}/{v['unflagged']} of {v['fields']}"
                                                         for k, v in s["by_feature"].items() if v["errors"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["cache", "run", "report"])
    parser.add_argument("--extractor", default="llm")
    parser.add_argument("--split", default="dev", choices=["dev", "test", "fresh"])
    parser.add_argument("--set", default="notes_v2", help="the note set's directory: notes_v2 or notes_v3")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("results", nargs="?")
    args = parser.parse_args()
    if args.command == "cache":
        return cache()
    if args.command == "report":
        return report(Path(args.results))
    path = run(args.extractor, args.split, args.limit, args.workers, args.set)
    print(path)
    report(path)


if __name__ == "__main__":
    main()
