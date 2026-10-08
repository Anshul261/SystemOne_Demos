"""Can d1 decide the role of each OCR cell on a lab report? Scored against the hand-labelled answer key.

Usage: python lab_roles.py CELLS.json   (OCR cells from services/extract/document.read_image)
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
TRUTH = json.loads((ROOT / "fixtures/documents/evya-haematology-report.truth.json").read_text())

# Paraphrased examples only; none comes from the report being read.
ROLES = {
    "test_name": "The name of a blood test or measurement, such as 'Serum sodium' or 'TSH'",
    "result": "A single measured value for a test, such as '7.2' or '145'",
    "unit_or_range": "A unit of measurement and/or a normal reference range, such as 'mmol/L', '3.5 - 5.1' or 'mg/dL 70-110'",
    "method": "How a test was done, such as '(Method: ion selective electrode)'",
    "section": "A heading that names a group of tests, such as 'LIVER FUNCTION TEST'",
    "header": "A table column title, such as 'RESULT' or 'UNITS'",
    "finding": "A written observation instead of a number, such as 'mild anisocytosis' or 'not detected'",
    "other": "Hospital, patient, doctor, address, signature or unreadable OCR noise",
}
QUESTION = {"role": {"type": "choice", "instructions": "What is this cell of a lab report?", "criteria": ROLES}}


def states(cells: list[dict], style: str) -> list[str]:
    rows: dict[int, list[str]] = {}
    for c in cells:
        rows.setdefault(c["row"], []).append(c["text"])
    out = []
    for c in cells:
        lines = [f'A cell from a lab report: "{c["text"]}"']
        if style in ("row", "row+pos"):
            others = [t for t in rows[c["row"]] if t != c["text"]]
            lines.append(f'Other cells in the same row: {" | ".join(others) if others else "none"}')
        if style == "row+pos":
            lines.append(f'It starts {round(c["box"][0] * 100)}% of the way across the page and sits '
                         f'{round(c["box"][1] * 100)}% of the way down.')
        out.append("\n".join(lines))
    return out


def main() -> None:
    cells = json.loads(Path(sys.argv[1]).read_text())
    truth = [c["role"] for c in TRUTH["cell_roles"]["cells"]]
    assert [c["text"] for c in cells] == [c["text"] for c in TRUTH["cell_roles"]["cells"]], "OCR output changed"
    for model in ("omni", "3b"):
        for style in ("text", "row", "row+pos"):
            body = {"model": model, "requests": [{"state": s, "questions": QUESTION} for s in states(cells, style)]}
            t0 = time.perf_counter()
            r = httpx.post("http://127.0.0.1:8020/system_one_batch", json=body, timeout=300).json()
            wall = (time.perf_counter() - t0) * 1000
            pred = [x["answers"]["role"]["choice"] for x in r["results"]]
            hits = sum(p == t for p, t in zip(pred, truth))
            per = {role: f"{sum(p == t == role for p, t in zip(pred, truth))}/{truth.count(role)}" for role in ROLES}
            confusion = Counter(f"{t}->{p}" for p, t in zip(pred, truth) if p != t).most_common(5)
            print(f"{model:4} {style:8} {hits:3d}/{len(truth)}  model {r['model_ms']:.0f} ms  wall {wall:.0f} ms | {per}")
            print(f"            top confusions: {confusion}")


if __name__ == "__main__":
    main()
