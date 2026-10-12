"""Run the four live browser demos on each decision model and record pass/fail and time.

Usage: python live_browser.py ROUNDS [omni 3b multi]
"""

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import run  # noqa: E402
from decision_clients import make_client  # noqa: E402

rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 1
models = sys.argv[2:] or ["omni", "3b", "multi"]
demos = ["rpa", "parabank", "piko", "piko-excel"]
results = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for r in range(rounds):
        for model in models:
            for demo in demos:
                context = browser.new_context(ignore_https_errors=True, viewport={"width": 1440, "height": 900})
                page = context.new_page()
                started = time.perf_counter()
                try:
                    run.DEMOS[demo](page, make_client(model))
                    ok, error = True, None
                except Exception as exc:  # noqa: BLE001 - record and continue
                    ok, error = False, f"{type(exc).__name__}: {str(exc)[:160]}"
                seconds = round(time.perf_counter() - started, 2)
                context.close()
                results.append({"round": r + 1, "model": model, "demo": demo, "passed": ok, "seconds": seconds, "error": error})
                print(f"round {r + 1} {model:5} {demo:10} {'PASS' if ok else 'FAIL'} {seconds:6.2f}s {error or ''}", flush=True)
    browser.close()
(ROOT / "experiments/d1/results/live_browser.json").write_text(json.dumps(results, indent=1))
for model in models:
    mine = [x for x in results if x["model"] == model]
    print(f"{model}: {sum(x['passed'] for x in mine)}/{len(mine)} passed")
