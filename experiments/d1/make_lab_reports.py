"""Held-out lab reports for the lab reader: different labs, layouts, wording and panels from the real
haematology report the reader was built against, rendered in a browser and then scanned or
photographed with the stamp set's capture model. Answer keys come from construction.

Usage (repository .venv, which has Playwright): python make_lab_reports.py OUT_DIR [SEED_PREFIX]
"""

from __future__ import annotations

import html
import json
import random
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
import make_stamps  # noqa: E402  (capture: scan / photo / clean)

PANELS = {
    "LIVER FUNCTION TEST": [
        ("Total Bilirubin", "mg/dL", 0.2, 1.2, 1), ("Direct Bilirubin", "mg/dL", 0.0, 0.3, 2),
        ("SGOT (AST)", "U/L", 5, 40, 0), ("SGPT (ALT)", "U/L", 7, 56, 0), ("Alkaline Phosphatase", "U/L", 44, 147, 0),
        ("Total Protein", "g/dL", 6.0, 8.3, 1), ("Albumin", "g/dL", 3.5, 5.0, 1)],
    "KIDNEY FUNCTION TEST": [
        ("Blood Urea", "mg/dL", 15, 40, 0), ("Serum Creatinine", "mg/dL", 0.6, 1.3, 2), ("Uric Acid", "mg/dL", 3.5, 7.2, 1),
        ("Sodium", "mmol/L", 135, 145, 0), ("Potassium", "mmol/L", 3.5, 5.1, 1)],
    "LIPID PROFILE": [
        ("Total Cholesterol", "mg/dL", 125, 200, 0), ("Triglycerides", "mg/dL", 40, 150, 0),
        ("HDL Cholesterol", "mg/dL", 40, 60, 0), ("LDL Cholesterol", "mg/dL", 50, 130, 0)],
    "THYROID PROFILE": [("T3", "ng/dL", 80, 200, 0), ("T4", "µg/dL", 5.1, 14.1, 1), ("TSH", "µIU/mL", 0.27, 4.2, 2)],
}
LAYOUTS = [
    {"lab": "Riverbend Diagnostics", "headers": ["Investigation", "Observed Value", "Units", "Biological Ref. Interval"],
     "panels": ["LIVER FUNCTION TEST", "KIDNEY FUNCTION TEST"], "flag_style": "bold", "methods": True},
    {"lab": "Northgate Pathology Lab", "headers": ["TEST NAME", "RESULT", "UNIT", "NORMAL RANGE"],
     "panels": ["LIPID PROFILE", "THYROID PROFILE"], "flag_style": "star", "methods": False},
    {"lab": "Lakeside Clinical Laboratory", "headers": ["Parameter", "Value", "Unit", "Reference Range"],
     "panels": ["KIDNEY FUNCTION TEST", "LIPID PROFILE", "THYROID PROFILE"], "flag_style": "bold", "methods": False},
]
METHODS = {"mg/dL": "Method: Spectrophotometry", "U/L": "Method: IFCC Kinetic", "g/dL": "Method: Biuret",
           "mmol/L": "Method: ISE Direct"}


def value_for(low: float, high: float, digits: int, rng: random.Random) -> tuple[float, str]:
    kind = rng.choices(["normal", "low", "high"], weights=[6, 1, 2])[0]
    span = high - low
    v = {"normal": rng.uniform(low + 0.1 * span, high - 0.1 * span),
         "low": rng.uniform(max(0.0, low - 0.4 * span), low - 0.05 * span) if low > 0 else rng.uniform(low, high),
         "high": rng.uniform(high + 0.05 * span, high + 0.6 * span)}[kind]
    v = round(v, digits)
    flag = "low" if v < low else "high" if v > high else "normal"
    return v, flag


def fmt(x: float, digits: int) -> str:
    return f"{x:.{digits}f}"


def render(layout: dict, rng: random.Random) -> tuple[str, dict]:
    rows, tests = [], []
    for panel in layout["panels"]:
        rows.append(f'<tr><td colspan="4" class="sec">{panel}</td></tr>')
        for name, unit, low, high, d in PANELS[panel]:
            v, flag = value_for(low, high, d, rng)
            shown = fmt(v, d)
            if flag != "normal":
                shown = f"<b>{shown}</b>" if layout["flag_style"] == "bold" else f"{shown} *"
            method = (f'<div class="method">{METHODS[unit]}</div>' if layout["methods"] and unit in METHODS else "")
            rows.append(f"<tr><td>{html.escape(name)}{method}</td><td>{shown}</td><td>{html.escape(unit)}</td>"
                        f"<td>{fmt(low, d)} - {fmt(high, d)}</td></tr>")
            tests.append({"section": panel.title(), "test": name, "value": v, "unit": unit, "low": round(low, d),
                          "high": round(high, d), "flag": flag, "highlighted": False})
    h = layout["headers"]
    page = f"""<html><head><style>
      body {{ font-family: Arial, sans-serif; margin: 46px 56px; color: #1d1d1d; width: 820px; background: #fff; }}
      .top {{ display: flex; justify-content: space-between; border-bottom: 2px solid #234; padding-bottom: 10px; }}
      .lab {{ font-size: 26px; font-weight: bold; color: #234; }} .addr {{ font-size: 12px; color: #555; }}
      .pt {{ margin: 16px 0; font-size: 13px; line-height: 1.7; }}
      table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
      th {{ text-align: left; border-bottom: 1.5px solid #333; padding: 6px 4px; font-size: 13px; }}
      td {{ padding: 5px 4px; vertical-align: top; }} .sec {{ font-weight: bold; padding-top: 14px; text-decoration: underline; }}
      .method {{ font-size: 10px; color: #666; }} .sign {{ margin-top: 40px; text-align: right; font-size: 13px; }}
      .foot {{ margin-top: 30px; font-size: 11px; color: #666; border-top: 1px solid #aaa; padding-top: 6px; }}
    </style></head><body>
      <div class="top"><div><div class="lab">{layout['lab']}</div><div class="addr">12 Example Road, Springfield · Tel 555-0100</div></div>
      <div class="addr">Report ID: {rng.randrange(100000, 999999)}<br>Collected: 03/09/2026</div></div>
      <div class="pt">Patient: Sample Patient &nbsp; · &nbsp; Age/Sex: 45 Y / F &nbsp; · &nbsp; Ref. by: Dr. Example</div>
      <table><tr><th>{h[0]}</th><th>{h[1]}</th><th>{h[2]}</th><th>{h[3]}</th></tr>{''.join(rows)}</table>
      <div class="sign">Dr. A. Example, MD (Pathology)<br>Consultant Pathologist</div>
      <div class="foot">This is a synthetic report generated for testing. All values are invented.</div>
    </body></html>"""
    return page, {"facility": layout["lab"], "tests": tests, "findings": []}


def main() -> None:
    out = Path(sys.argv[1])
    prefix = sys.argv[2] if len(sys.argv) > 2 else "lab"
    out.mkdir(parents=True, exist_ok=True)
    manifest = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 940, "height": 1200}, device_scale_factor=1.6)
        for i, layout in enumerate(LAYOUTS):
            for j in range(2):
                rng = random.Random(f"{prefix}-{i}-{j}")
                markup, truth = render(layout, rng)
                page.set_content(markup)
                shot = out / f"report-{i}-{j}.raw.png"
                page.screenshot(path=str(shot), full_page=True)
                img = Image.open(shot).convert("RGB")
                # The capture model expects an A4-ish page: pad to A4 proportions, then scan or photograph.
                w = img.width
                h = int(w * 1754 / 1240)
                canvas = Image.new("RGB", (w, max(h, img.height)), "white")
                canvas.paste(img, (0, 0))
                canvas = canvas.resize(make_stamps.PAGE)
                captured, mode = make_stamps.capture(canvas, rng, photocopy=False)
                name = f"report-{i}-{j}.jpg"
                captured.save(out / name, quality=90)
                shot.unlink()
                truth["capture"] = mode
                (out / f"report-{i}-{j}.truth.json").write_text(json.dumps(truth, indent=1))
                manifest.append({"file": name, "truth": f"report-{i}-{j}.truth.json", "layout": i, "capture": mode})
        browser.close()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(f"wrote {len(manifest)} reports to {out}: {[m['capture'] for m in manifest]}")


if __name__ == "__main__":
    main()
