"""Check the screen mapper against what each field is known to hold.

    uv run --env-file .env python mapcheck.py

Registration: the hand-written mapping (notes_v2/evaluate.py KEYS); every other field holds nothing the
record fills. Vitals and lab results: read by code from each row's text and column header (the vital or the
test's LOINC code, the column, the unit). A field is right when the mapper gives what it holds, including
nothing. Results go to results/mapcheck-<time>.json.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from forms import open_screen, read_form, same_control
from mapping import key, map_screen
from models import metered
from notes_v2.evaluate import KEYS
from schema import ITEMS
from targets import openemr, openemr_login

ROOT = Path(__file__).parent
VITALS = {"Weight": "weight", "Height/Length": "height", "BP Systolic": "bp_systolic", "BP Diastolic": "bp_diastolic",
          "Pulse": "pulse", "Respiration": "respiration", "Temperature": "temperature", "Temp Location": "temp_site",
          "Oxygen Saturation": "oxygen_saturation"}
UNITS = {"lbs": "lb", "kg": "kg", "in": "in", "cm": "cm", "F": "F", "C": "C"}
COLUMNS = {"Value": "value", "Abn": "abnormal", "Units": "unit", "Range": "range", "Date": "date",
           "End Date": "end_date", "Code": "code", "Name": "name"}
REPORT = {"Reported": "report_date", "Ext Time Collected": "collection_date", "Status": "report_status"}


def column(field: dict) -> str | None:
    found = re.search(r"\(([^()]*)\)$", field.get("context", ""))
    return found[1] if found else None


def known_patient(field: dict) -> str | None:
    item = next((k for k, controls in KEYS["new-patient"].items() if any(same_control(field, c) for c in controls)), None)
    return key({"item": item, "attribute": "value", "unit": None})


def known_vital(field: dict) -> str | None:
    row = field.get("row") or ""
    item = next((v for k, v in VITALS.items() if row.startswith(k + " ")), None)
    attribute = COLUMNS.get(column(field)) if item else None
    if not item or attribute not in ("value", "abnormal"):
        return None
    unit = next((u for word, u in UNITS.items() if re.search(rf"\) {re.escape(word)}( |$)", row)), None)
    return key({"item": item, "attribute": attribute, "unit": unit if attribute == "value" and item in ("weight", "height", "temperature") else None})


def known_lab(field: dict) -> str | None:
    if column(field) in REPORT:
        return key({"item": REPORT[column(field)], "attribute": "value", "unit": None})
    code = next((c for c in ITEMS["lab_results"] if re.search(rf"(^|\s){re.escape(c)}\s", field.get("row") or "")), None)
    attribute = COLUMNS.get(column(field))
    return key({"item": code, "attribute": attribute, "unit": None}) if code and attribute else None


def main():
    base = os.environ["OPENEMR_URL"].rstrip("/")
    target = openemr()
    screens = [("new-patient", target.screens[0], known_patient), ("vitals", target.screens[1], known_vital),
               ("lab-results", type(target.screens[0])(f"{base}/interface/orders/orders_results.php?review=1", {}),
                known_lab)]
    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 900})
        openemr_login(context)
        for name, screen, known in screens:
            page = open_screen(context, screen)
            _, fields = read_form(page)
            with metered() as spent:
                mapped = map_screen(page, fields)
            rows = [{"field": f, "known": known(f), "key": key(m), **m} for f, m in zip(fields, mapped["fields"])]
            right = [r for r in rows if r["key"] == r["known"]]
            wrong = [r for r in rows if r["key"] != r["known"]]
            report[name] = {"section": mapped["section"], "section_confidence": mapped["section_confidence"],
                            "fields": len(rows), "right": len(right), "tokens": spent.system_one_tokens,
                            "seconds": round(spent.report()["wall_ms"] / 1000, 1), "rows": rows}
            print(f"{name}: section {mapped['section']} ({mapped['section_confidence']:.2f}); "
                  f"{len(right)}/{len(rows)} fields right; {spent.system_one_tokens} tokens; "
                  f"{report[name]['seconds']} s")
            for r in wrong:
                print(f"   {r['field']['role']} {(r['field'].get('context') or r['field']['name'])[:60]!r}: "
                      f"known {r['known']}, mapped {r['key']} ({r['confidence']})")
            page.close()
        browser.close()
    path = ROOT / "results" / f"mapcheck-{datetime.now():%Y-%m-%d-%H%M%S}.json"
    path.write_text(json.dumps(report, indent=1, default=str))
    print(path)


if __name__ == "__main__":
    main()
