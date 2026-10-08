"""Read a scanned lab report into test records: OCR cells -> table columns -> tests, results, units, ranges -> flags.

The models make the judgement calls; plain code does what has one right answer.
  d1 decides: which column a header names, whether a name-column cell is a test, a method note or a
              section heading, which standard unit an OCR'd unit means, and (vision) whether a printed
              result sits inside the lab's shaded "abnormal" box.
  code does:  column bands from header positions, numbers and ranges by pattern, pairing tests with
              their results, units and ranges in order within each section, and the low/high flags.

Pairing by order rather than by row matters: many reports print units and ranges a line below their
test, so the nearest row is often the next test's.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

from PIL import Image

# Paraphrased examples only; none is taken from a particular report.
COLUMNS = {
    "test": "the column that names each test, such as 'Investigation' or 'Test name'",
    "result": "the column with each measured value, such as 'Observed value' or 'Value'",
    "unit": "the column with units of measurement, such as 'Unit'",
    "range": "the column with normal reference ranges, such as 'Biological reference interval' or 'Normal values'",
    "none": "not a column title: a hospital name, patient detail, date or other text",
}
NAME_ROLES = {
    "test_name": "the name of one lab test, such as 'Serum sodium', 'TSH' or 'Absolute neutrophil count'",
    "method": "a note on how a test was done, usually in brackets, such as '(Method: ion selective electrode)'",
    "section": "a heading that groups several tests, printed bold and underlined in capitals, such as 'LIVER FUNCTION TEST'",
    "other": "a signature, comment, address or other text that is not part of the results table",
}
# d1 sees option descriptions, not their keys, so each description starts with the symbol itself.
UNITS = {
    "µg/dL": "µg/dL: micrograms per decilitre, also written ug/dl or mcg/dL",
    "ng/dL": "ng/dL: nanograms per decilitre",
    "ng/mL": "ng/mL: nanograms per millilitre",
    "pg/mL": "pg/mL: picograms per millilitre",
    "µIU/mL": "µIU/mL: micro international units per millilitre, also written uIU/ml or mIU/L, the unit for TSH",
    "mEq/L": "mEq/L: milliequivalents per litre",
    "µmol/L": "µmol/L: micromoles per litre",
    "g/dL": "g/dL: grams per decilitre, also written gm/dl, gms% or g%",
    "%": "%: percent",
    "million/cu mm": "million/cu mm: millions of cells per cubic millimetre, also written mil/cumm or 10^6/µL",
    "cells/cu mm": "cells/cu mm: cells per cubic millimetre, also written /cumm or cells/µL",
    "lakhs/cu mm": "lakhs/cu mm: lakhs per cubic millimetre, also written lakhs/cumm",
    "fL": "fL: femtolitres, the unit for cell volume such as MCV",
    "pg": "pg: picograms, the unit for haemoglobin per cell such as MCH",
    "mm/hr": "mm/hr: millimetres in an hour, the unit for sedimentation rate, sometimes written mm",
    "mg/dL": "mg/dL: milligrams per decilitre",
    "mmol/L": "mmol/L: millimoles per litre",
    "U/L": "U/L: units per litre, also IU/L",
    "other": "a different unit, or no unit",
}
HIGHLIGHT = {
    "boxed": "a number printed inside a shaded grey box",
    "plain": "a number printed on plain paper, with no box around it",
}
# A result, optionally followed by the lab's own flag marker ("19.3 *", "7.2 H").
NUMBER = re.compile(r"^[<>]?\s*(\d+(?:[.,]\d+)?)\s*(\*|H|L|\(H\)|\(L\)|High|Low)?$")
RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–]\s*(\d+(?:[.,]\d+)?)")
NUMBERS = re.compile(r"\d+(?:[.,]\d+)?")

Decide = Callable[[str, list[tuple[Any, dict, list]]], tuple[list[dict], float]]


def number(text: str) -> float | None:
    """'27,0' is 27.0 but '10,300' is 10300: a comma before exactly three digits groups thousands."""
    text = text.strip()
    if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
        return float(text.replace(",", ""))
    return float(text.replace(",", "."))


def parse_range(text: str) -> tuple[float, float, int, int] | None:
    """(low, high, start, end) of the reference range in `text`. OCR often drops the dash ("3.5 5.1") or
    reads it as a 1 ("0.60 -1 1.30", "40-1 150"); a number right after a dash match is the real high."""
    matches = list(RANGE.finditer(text))
    if matches:
        m = matches[-1]
        after = re.match(r"\s+(\d+(?:[.,]\d+)?)", text[m.end():])
        if after:  # "40-1 150": the "1" was the dash
            low, high, end = number(m.group(1)), number(after.group(1)), m.end() + after.end()
        else:
            low, high, end = number(m.group(1)), number(m.group(2)), m.end()
        if low < high:
            return low, high, m.start(), end
    nums = list(NUMBERS.finditer(text))
    if len(nums) >= 2:  # no usable dash: the last two numbers, if they rise
        a, b = nums[-2], nums[-1]
        if number(a.group()) < number(b.group()):
            return number(a.group()), number(b.group()), a.start(), b.end()
    return None


def align(tests: list[float], slots: list[float]) -> dict[int, int]:
    """Match tests to unit/range lines, both top to bottom, keeping order and allowing skips.

    A report prints each range at a consistent offset from its test: on the same line, or a line below.
    The offset is the median distance to the nearest line at or just below each test; the alignment then
    keeps as many pairs as it can within that offset's reach, preferring the closest. Pairing in order
    like this needs no section boundaries, which tilted photos make unreliable.
    """
    if not tests or not slots:
        return {}
    gaps = []
    for t in tests:
        below = [s - t for s in slots if -0.012 <= s - t <= 0.045]
        if below:
            gaps.append(min(below, key=abs))
    offset = sorted(gaps)[len(gaps) // 2] if gaps else 0.0
    n, m = len(tests), len(slots)
    best = [[(0, 0.0)] * (m + 1) for _ in range(n + 1)]  # (pairs, -cost), maximised
    move = [[None] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            options = [(best[i - 1][j], "skip_test"), (best[i][j - 1], "skip_slot")]
            miss = abs(slots[j - 1] - tests[i - 1] - offset)
            if miss < 0.022:
                pairs, score = best[i - 1][j - 1]
                options.append(((pairs + 1, score - miss), "pair"))
            best[i][j], move[i][j] = max(options, key=lambda o: o[0])
    pairs, i, j = {}, n, m
    while i and j:
        if move[i][j] == "pair":
            pairs[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif move[i][j] == "skip_test":
            i -= 1
        else:
            j -= 1
    return pairs


def center(box) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def group_rows(cells: list[dict]) -> list[list[dict]]:
    """Cells on one printed line, top to bottom, left to right within a line. A cell joins the line when
    its centre is within half a line height of the line's centre; comparing extents instead would chain
    tall, tightly spaced lines together."""
    groups: list[list[dict]] = []
    for c in sorted(cells, key=lambda c: center(c["box"])[1]):
        if groups:
            g = groups[-1]
            mid = sum(center(x["box"])[1] for x in g) / len(g)
            height = max(sum(x["box"][3] - x["box"][1] for x in g) / len(g), c["box"][3] - c["box"][1])
            if abs(center(c["box"])[1] - mid) < 0.5 * height:
                g.append(c)
                continue
        groups.append([c])
    return [sorted(g, key=lambda c: c["box"][0]) for g in groups]


def read_report(cells: list[dict], image: Image.Image, decide: Decide, model: str, vision_model: str | None = None,
                on_step: Callable[[str, dict], None] | None = None) -> dict[str, Any]:
    """Structured results for one scanned report page."""
    step = on_step or (lambda name, details: None)
    timings: dict[str, float] = {}

    def ask(name: str, requests: list[tuple[Any, dict, list]], which: str = model) -> list[dict]:
        if not requests:
            return []
        answers, ms = decide(which, requests)
        timings[name] = round(timings.get(name, 0) + ms, 1)
        return answers

    # 1. Column titles: every digit-free cell in the upper part of the page is a candidate.
    candidates = [c for c in cells if c["box"][1] < 0.6 and not re.search(r"\d", c["text"]) and len(c["text"]) > 2]
    question = {"column": {"type": "choice", "instructions": "Which column of a lab results table does this title name?",
                           "criteria": COLUMNS}}
    answers = ask("columns", [(f'Text near the top of a lab report: "{c["text"]}"', question, []) for c in candidates])
    titled = [(c, a["column"]) for c, a in zip(candidates, answers) if a["column"]["choice"] != "none"]
    best, band = None, []
    for group in group_rows([c for c, _ in titled]):
        kinds = {}
        for c in group:
            a = next(a for x, a in titled if x is c)
            if a["choice"] not in kinds or a["confidence"] > kinds[a["choice"]][1]["confidence"]:
                kinds[a["choice"]] = (c, a)
        if best is None or len(kinds) > len(best):
            best, band = kinds, group
    if not best or len(best) < 2:
        raise ValueError("Could not find the results table: no row of column titles was recognised.")
    # A tilted photo can push one title just off the others' line: borrow a confident title of a missing
    # kind from close above or below.
    mid = sum(center(c["box"])[1] for c, _ in best.values()) / len(best)
    for c, a in sorted(titled, key=lambda ca: -ca[1]["confidence"]):
        if a["choice"] not in best and a["confidence"] >= 0.6 and abs(center(c["box"])[1] - mid) < 0.04:
            best[a["choice"]] = (c, a)
    if not {"test", "result"} <= set(best):
        # Second pass: show the whole title row and ask which title names each kind of column.
        top_, bottom_ = min(c["box"][1] for c in band) - 0.03, max(c["box"][3] for c in band) + 0.03  # tilted photos
        row = [c for c in candidates if top_ <= c["box"][1] and c["box"][3] <= bottom_]
        options = {f"t{i}": c["text"] for i, c in enumerate(row)} | {"none": "none of these"}
        state = f'Text along the top of a lab results table: {" | ".join(c["text"] for c in row)}'
        kinds_ = [k for k in COLUMNS if k != "none"]
        picks = ask("columns", [(state, {"q": {"type": "choice", "instructions": f"Which of these is the title of {COLUMNS[k]}?",
                                               "criteria": options}}, []) for k in kinds_])
        best = {k: (row[int(a["q"]["choice"][1:])], a["q"]) for k, a in zip(kinds_, picks) if a["q"]["choice"] != "none"}
        if not {"test", "result"} <= set(best):
            raise ValueError("Could not find the results table: no test and result column titles were recognised.")
    headers = {kind: {"text": c["text"], "box": c["box"], "confidence": round(a["confidence"], 3)} for kind, (c, a) in best.items()}
    step("columns_found", {"headers": headers})

    # 2. Column bands. ("page_box" keeps each cell's position on the image for drawing.)
    for c in cells:
        c["page_box"] = c["box"]
    # Column bands: each cell below the titles joins the column whose title centre is nearest.
    top = max(h["box"][3] for h in headers.values())
    order = sorted(headers, key=lambda k: center(headers[k]["box"])[0])
    centres = [center(headers[k]["box"])[0] for k in order]
    body = [c for c in cells if c["box"][1] > top - 0.005]
    for c in body:
        x = center(c["box"])[0]
        c["column"] = order[min(range(len(centres)), key=lambda i: abs(centres[i] - x))]

    # 3. Name column: test, method note, section heading, or the footer below the table.
    names = [c for c in body if c["column"] == "test"]
    question = {"role": {"type": "choice", "instructions": "What is this text from the test-name column of a lab report?",
                         "criteria": NAME_ROLES}}
    w, h = image.size

    def crop(box, pad=0.004):  # the cell as printed, so bold and underlined headings are visible
        return image.crop((int(max(0, box[0] - pad) * w), int(max(0, box[1] - pad) * h),
                           int(min(1, box[2] + pad) * w), int(min(1, box[3] + pad * 1.5) * h)))

    for c, a in zip(names, ask("name_roles", [(f'"{c["text"]}"', question, [crop(c["page_box"])]) for c in names])):
        c["role"] = a["role"]["choice"]
        c["role_confidence"] = round(a["role"]["confidence"], 3)
    step("names_read", {"roles": {role: sum(c["role"] == role for c in names) for role in NAME_ROLES}})
    tests = [c for c in names if c["role"] == "test_name"]
    if not tests:
        raise ValueError("No test names were recognised in the results table.")
    bottom = max(c["box"][3] for c in names if c["role"] in ("test_name", "method")) + 0.005
    step("table_found", {"top": round(top, 4), "bottom": round(bottom, 4), "tests": len(tests)})

    # 4. Sections: headings start a new section (consecutive heading lines merge); tests join the current one.
    #    Values and ranges belong to a section until the next section's first test, because a report may
    #    print a range on the same line as the next heading.
    sections: list[dict] = []
    pending: list[str] = []
    for c in sorted((c for c in names if c["role"] in ("section", "test_name")), key=lambda c: c["box"][1]):
        if c["role"] == "section":
            pending.append(c["text"])
            continue
        if pending or not sections:
            sections.append({"title": " ".join(pending), "tests": []})
            pending = []
        sections[-1]["tests"].append(c)
    for i, sec in enumerate(sections):
        sec["start"] = sec["tests"][0]["box"][1] - 0.008
        sec["end"] = sections[i + 1]["tests"][0]["box"][1] - 0.008 if i + 1 < len(sections) else bottom

    # 5. Pairing. Numbers are printed on their test's line, so each pairs with the nearest test name; a
    #    heading mistaken for a test simply gets none. Unit/range slots (the unit and range cells on one
    #    line) and written findings are often printed a line low, so they pair in order.
    records = []
    for sec in sections:
        inside = [c for c in body if sec["start"] <= c["box"][1] < sec["end"]]
        result_cells = [c for c in inside if c["column"] == "result"]
        numeric = [c for c in result_cells if NUMBER.match(c["text"])]
        found: dict[int, list[dict]] = {}
        pairs = sorted(((abs(center(n["box"])[1] - center(t["box"])[1]), k, j)
                        for k, t in enumerate(sec["tests"]) for j, n in enumerate(numeric)))
        used = set()
        for dist, k, j in pairs:
            if k not in found and j not in used and dist < 0.02:
                found[k], used = [numeric[j]], used | {j}
        # Written findings go, in order, to the tests that got no number (a heading read as a test may
        # be left over at the end, without one).
        words = group_rows([c for c in result_cells if not NUMBER.match(c["text"])])
        for k, group in zip([k for k in range(len(sec["tests"])) if k not in found], words):
            found[k] = group
        valued = [k for k in range(len(sec["tests"])) if k in found and NUMBER.match(found[k][0]["text"])]
        for k, test in enumerate(sec["tests"]):
            if k not in found:
                continue  # a heading or a label without a result
            record: dict[str, Any] = {"section": sec["title"], "test": test["text"], "test_box": test["page_box"]}
            text = " ".join(c["text"] for c in found[k])
            record["result_text"], record["result_boxes"] = text, [c["page_box"] for c in found[k]]
            is_number = k in valued
            if is_number:
                match = NUMBER.match(text)
                record["value"] = number(match.group(1))
                if match.group(2):
                    record["printed_flag"] = match.group(2)
            else:
                record["finding"] = text.rstrip(".")
            records.append(record)

    # 6. Units and ranges: one alignment over the whole table, so a range printed above a section's
    #    first test (a tilted photo) still finds its test.
    # The last test's range may sit a line below the table's last name-column line, so units and ranges
    # may run one and a half lines further; the alignment's offset check keeps footer text out.
    heights = sorted(c["box"][3] - c["box"][1] for c in names)
    reach = bottom + 1.5 * heights[len(heights) // 2]
    slots = group_rows([c for c in body if c["column"] in ("unit", "range") and c["box"][1] < reach])
    numbered = [r for r in records if "value" in r]
    test_y = [center(r["test_box"])[1] for r in numbered]
    for i, j in align(test_y, [sum(center(c["box"])[1] for c in g) / len(g) for g in slots]).items():
        record, slot = numbered[i], slots[j]
        unit_text = " ".join(c["text"] for c in slot if c["column"] == "unit")
        range_text = " ".join(c["text"] for c in slot if c["column"] == "range")
        joined = f"{unit_text} {range_text}".strip()
        parsed = parse_range(joined)
        if parsed:
            record["low"], record["high"], range_start, _ = parsed
            unit_part, qualifier = joined[:range_start], ""
            if range_text and unit_text and range_start >= len(unit_text):
                unit_part, qualifier = unit_text, joined[len(unit_text):range_start]
            record["qualifier"] = re.sub(r"[\s:;]+", " ", re.sub(r"(?<![A-Za-z>])\d+\b", "", qualifier)).strip() or None
            record["unit_text"] = unit_part.strip() or None
        else:
            record["unit_text"] = joined or None
        record["slot_boxes"] = [c["page_box"] for c in slot]

    step("records_paired", {"records": len(records), "with_range": sum("low" in r for r in records)})

    # 7. Units: d1 maps each printed unit, OCR damage included, to a standard one, using the test as context.
    # A unit already printed exactly as a known symbol (any case) needs no model; the rest, OCR damage
    # included, are mapped by d1. "other" keeps the printed text.
    known = {k.lower(): k for k in UNITS if k != "other"}
    question = {"unit": {"type": "choice", "instructions": "Which unit of measurement is written here?", "criteria": UNITS}}
    with_unit = []
    for r in records:
        if r.get("unit_text"):
            if r["unit_text"].replace(" ", "").lower() in known:
                r["unit"], r["unit_confidence"] = known[r["unit_text"].replace(" ", "").lower()], 1.0
            else:
                with_unit.append(r)
    for r, a in zip(with_unit, ask("units", [(f'Unit printed on a lab report: "{r["unit_text"]}"\nTest: {r["test"]}', question, [])
                                             for r in with_unit])):
        r["unit"] = a["unit"]["choice"] if a["unit"]["choice"] != "other" else r["unit_text"]
        r["unit_confidence"] = round(a["unit"]["confidence"], 3)
    for sec_title in {r["section"] for r in records}:  # a unit the scan lost: the section's common unit
        units = [r["unit"] for r in records if r["section"] == sec_title and r.get("unit")]
        for r in records:
            if r["section"] == sec_title and "value" in r and not r.get("unit") and units and len(set(units)) == 1:
                r["unit"], r["unit_inferred"] = units[0], True

    step("units_read", {"units": sorted({r["unit"] for r in records if r.get("unit")})})

    # 8. Flags from the numbers; then d1 vision checks the lab's own shaded box around each result.
    for r in records:
        if "value" in r and "low" in r:
            r["flag"] = "low" if r["value"] < r["low"] else "high" if r["value"] > r["high"] else "normal"
    crops, owners = [], []
    for r in records:
        if "value" in r and r.get("result_boxes"):
            x0, y0, x1, y1 = r["result_boxes"][0]
            pad_x, pad_y = (y1 - y0) * 2.2, (y1 - y0) * 0.8
            crops.append(image.crop((int(max(0, x0 - pad_x) * w), int(max(0, y0 - pad_y) * h),
                                     int(min(1, x1 + pad_x * 2.5) * w), int(min(1, y1 + pad_y) * h))))
            owners.append(r)
    question = {"box": {"type": "choice", "instructions": "How is this number printed?", "criteria": HIGHLIGHT}}
    for r, a in zip(owners, ask("highlight", [(None, question, [crop]) for crop in crops], vision_model or model)):
        r["printed_box"] = a["box"]["choice"] == "boxed"
        r["printed_box_confidence"] = round(a["box"]["confidence"], 3)
    # The lab's own marks: a shaded box (d1 vision sees these reliably) or a printed marker such as "*" or
    # "H". d1 cannot tell bold from regular type, so a lab that marks only in bold shows no marks here.
    for r in records:
        if "value" in r:
            r["lab_marked"] = bool(r.get("printed_box") or r.get("printed_flag"))
    if any(r.get("lab_marked") for r in records):
        for r in records:
            if "flag" in r and "lab_marked" in r:
                r["agrees_with_lab"] = (r["flag"] != "normal") == r["lab_marked"]
    table = [{"text": c["text"], "box": c["page_box"], "column": c["column"], "role": c.get("role")}
             for c in body if c["box"][1] < bottom]
    return {"headers": headers, "sections": [s["title"] for s in sections], "records": records, "model_ms": timings,
            "cells": table, "table": {"top": round(top, 4), "bottom": round(bottom, 4)}}


def run_lab_report(extract, d1, document: dict[str, Any], data: bytes, model: str = "3b", vision_model: str | None = None,
                   on_event: Callable[[dict[str, Any], bytes | None], None] | None = None):
    """OCR one report page with the extraction service, read it with d1, and emit every stage."""
    import base64
    import io

    from browser_forms import AuditLog

    audit = AuditLog(f"lab-report-{document['id']}")

    def emit(event: str, image: bytes | None = None, **details: Any) -> None:
        audit.write(event, **details)
        if on_event:
            on_event({"event": event, **details}, image)

    started = time.monotonic()
    emit("report_received", doc_id=document["id"], title=document.get("title", "Your report"), model=model,
         vision_model=vision_model or model)
    ocr = extract.ocr(data)
    page = base64.b64decode(ocr["image"])
    emit("ocr_done", page, doc_id=document["id"], image_size=ocr["image_size"], ocr_ms=ocr["ocr_ms"],
         cells=[{"text": c["text"], "box": c["box"]} for c in ocr["cells"]])
    image = Image.open(io.BytesIO(page)).convert("RGB")
    result = read_report([dict(c) for c in ocr["cells"]], image, d1.system_one_batch, model, vision_model,
                         on_step=lambda name, details: emit(name, doc_id=document["id"], **details))
    records = result["records"]
    numeric = [r for r in records if "value" in r]
    flagged = [r for r in numeric if r.get("flag") in ("low", "high")]
    boxed = [r for r in numeric if r.get("lab_marked")]
    summary = {
        "tests": len(numeric), "findings": sum("finding" in r for r in records), "out_of_range": len(flagged),
        "lab_boxed": len(boxed), "agree": sum(1 for r in numeric if r.get("agrees_with_lab")),
        "compared": sum(1 for r in numeric if "agrees_with_lab" in r),
        "model_ms": round(sum(result["model_ms"].values()), 1), "ocr_ms": ocr["ocr_ms"],
        "elapsed_ms": round((time.monotonic() - started) * 1000, 1), "stage_ms": result["model_ms"],
    }
    emit("report_read", doc_id=document["id"], headers=result["headers"], sections=result["sections"],
         records=records, cells=result["cells"], table=result["table"], summary=summary)
    return audit.path


# The answer keys write units as printed; these are their standard forms.
CANONICAL_UNITS = {"gms%": "g/dL", "gms %": "g/dL", "mil/cu mm": "million/cu mm", "cells/cu mm": "cells/cu mm",
                   "lakhs/cumm": "lakhs/cu mm", "mm": "mm/hr"}


def assign(records: list[dict], names: list[str]) -> list[dict | None]:
    """One record per answer-key test at most: best name similarity first, reading order breaking ties
    (OCR can print two tests the same way, as 'ESR I Hr' for 'ESR II Hr')."""
    import difflib

    def key(text):
        return "".join(ch for ch in text.lower() if ch.isalnum())
    pairs = sorted(((-difflib.SequenceMatcher(None, key(n), key(r["test"])).ratio(), abs(i - j), i, j)
                    for i, n in enumerate(names) for j, r in enumerate(records)))
    out: list[dict | None] = [None] * len(names)
    used = set()
    for neg, _, i, j in pairs:
        if -neg >= 0.75 and out[i] is None and j not in used:
            out[i], used = records[j], used | {j}
    return out


def score(records: list[dict], truth: dict) -> dict:
    """Field-by-field comparison with an answer key. Marks each matched record with `check`."""
    totals = {k: 0 for k in ("found", "value", "unit", "range", "flag", "printed_box")}
    numeric = [r for r in records if "value" in r]
    rows = []
    for t, r in zip(truth["tests"], assign(numeric, [t["test"] for t in truth["tests"]])):
        row = {"test": t["test"], "got": r["test"] if r else None}
        if r:
            totals["found"] += 1
            unit = CANONICAL_UNITS.get(t["unit"], t["unit"])
            checks = {
                "value": r.get("value") == t["value"],
                "unit": (r.get("unit") or "").replace(" ", "").lower() == unit.replace(" ", "").lower(),
                "range": (r.get("low"), r.get("high")) == (t["low"], t["high"]),
                "flag": r.get("flag") == t["flag"],
                "printed_box": r.get("printed_box") == bool(t.get("highlighted")),
            }
            for k, ok in checks.items():
                totals[k] += ok
            r["check"] = {k: ok for k, ok in checks.items()} | {"expected": {
                "value": t["value"], "unit": unit, "low": t["low"], "high": t["high"], "flag": t["flag"]}}
            row.update({k: "ok" if ok else f"got {r.get(k) if k != 'range' else (r.get('low'), r.get('high'))}"
                        for k, ok in checks.items()})
        rows.append(row)
    written = [r for r in records if "finding" in r]
    findings = 0
    for f, r in zip(truth.get("findings", []), assign(written, [f["test"] for f in truth.get("findings", [])])):
        if r:
            ok = r["finding"].upper().replace(".", "") == f["finding"].replace(".", "")
            r["check"] = {"finding": ok, "expected": {"finding": f["finding"]}}
            findings += ok
    n = len(truth["tests"])
    return {"totals": {k: [v, n] for k, v in totals.items()} | {"findings": [findings, len(truth.get("findings", []))]},
            "rows": rows, "extra_records": len(numeric) - totals["found"] + len(written) - findings}
