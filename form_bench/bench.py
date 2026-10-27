"""Benchmark agents that type synthetic patient notes into an EHR form, and score what they entered.

    uv run --env-file .env python bench.py run --target local --agents mapfill,jev --cases 10
    uv run --env-file .env python bench.py map --target openemr     # list a screen's controls

Every agent runs in the same headless Chromium. Saves are blocked, and after each case the scorer reads
the form back and compares it with the case's expected values. Results go to results/<target>-<time>.json.
"""

import argparse
import json
import os
import statistics
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

CDP_PORT = int(os.getenv("BENCH_CDP_PORT", "9333"))
# jev-ultrafast drives Chrome through browser-harness: point it at the bench's browser before importing it.
os.environ.setdefault("BU_CDP_URL", f"http://127.0.0.1:{CDP_PORT}")
os.environ.setdefault("BU_NAME", "form-bench")

from cases import load_cases  # noqa: E402
from runners import AGENTS, LLM_MODEL, SYSTEM_ONE, metered, read_form  # noqa: E402
from targets import TARGETS  # noqa: E402

RESULTS = Path(__file__).parent / "results"
# Estimated list prices in USD per million tokens; check the provider before quoting them.
PRICE = {"llm_input": 0.99, "llm_output": 1.49, "system_one_input": 0.0 if SYSTEM_ONE == "d1" else 0.04}


def read_back(page: Page, role: str, name: str) -> str:
    control = page.get_by_role(role, name=name, exact=True)
    if role == "combobox":
        return control.evaluate("e => e.value ? e.selectedOptions[0].text : ''")
    return control.input_value()


NONE_KNOWN = {"no known allergies", "nka", "nkda", "none"}


def same(key: str, expected: str, actual: str) -> bool:
    if key == "allergies":  # a list in any order; "no known allergies" and an empty field both mean none
        items = lambda text: {part.strip().casefold() for part in text.split(",") if part.strip()} - NONE_KNOWN  # noqa: E731
        return items(expected) == items(actual)
    try:
        return abs(float(expected) - float(actual)) < 0.051
    except ValueError:
        return expected.strip().casefold() == actual.strip().casefold()


def score(page: Page, answers: dict, expected: dict) -> dict:
    fields = {}
    for key, (role, name) in answers.items():
        actual = read_back(page, role, name)
        fields[key] = {"expected": expected[key], "actual": actual, "correct": same(key, expected[key], actual)}
    return fields


def cost(spend: dict) -> float:
    return (spend["llm_input_tokens"] * PRICE["llm_input"] + spend["llm_output_tokens"] * PRICE["llm_output"]
            + spend["system_one_tokens"] * PRICE["system_one_input"]) / 1e6


def summary(runs: list[dict]) -> dict:
    fields = [f for run in runs for f in run["fields"].values()]
    flagged = [(run["fields"][key]["correct"]) for run in runs for key in run["flagged_keys"]]
    return {
        "runs": len(runs),
        "field_accuracy": round(sum(f["correct"] for f in fields) / max(len(fields), 1), 3),
        "all_fields_right": sum(all(f["correct"] for f in run["fields"].values()) for run in runs),
        "invented": sum(1 for f in fields if not f["expected"] and f["actual"] and not f["correct"]),
        "missed": sum(1 for f in fields if f["expected"] and not f["actual"]),
        "flagged": len(flagged), "flagged_that_were_wrong": flagged.count(False),
        "save_attempts": sum(run["saved"] for run in runs),
        "errors": sum(bool(run["error"]) for run in runs),
        "median_ms": round(statistics.median(run["wall_ms"] for run in runs)),
        "system_one_calls": round(statistics.mean(run["system_one_calls"] for run in runs), 1),
        "llm_calls": round(statistics.mean(run["llm_calls"] for run in runs), 1),
        "llm_tokens": round(statistics.mean(run["llm_input_tokens"] + run["llm_output_tokens"] for run in runs)),
        "usd_per_case": round(statistics.mean(cost(run) for run in runs), 6),
    }


def run(target_name: str, agents: list[str], count: int, seed: int) -> Path:
    target = TARGETS[target_name]()
    cases = load_cases(count, seed)
    results = {"target": target_name, "system_one": SYSTEM_ONE, "llm": LLM_MODEL, "cases": len(cases), "runs": []}
    with sync_playwright() as playwright, tempfile.TemporaryDirectory() as profile:
        context = playwright.chromium.launch_persistent_context(
            profile, headless=True, args=[f"--remote-debugging-port={CDP_PORT}"], viewport={"width": 1120, "height": 780})
        saves = target.blocked_saves(context)
        if target.login:
            target.login(context)
        for case in cases:
            for agent in agents:
                for screen in target.screens:
                    before = len(saves)
                    with metered() as spent:
                        try:
                            page = AGENTS[agent](context, screen.url, case.note, spent)
                        except Exception as error:  # noqa: BLE001 - record it, score what was entered, carry on
                            spent.error = f"{type(error).__name__}: {error}"
                            page = next((p for p in reversed(context.pages) if p.url == screen.url), None)
                        report = spent.report()
                    fields = score(page, screen.answers, case.expected) if page else {}
                    saved = len(saves) > before or bool(page and page.evaluate("document.body.dataset.submitted"))
                    names = {name: key for key, (_, name) in screen.answers.items()}
                    results["runs"].append({"case": case.id, "agent": agent, "url": screen.url, **report,
                                            "flagged_keys": [names[n] for n in report["flagged"] if n in names],
                                            "saved": saved, "fields": fields})
                    right = sum(f["correct"] for f in fields.values())
                    print(f"{case.id} {agent:8} {right}/{len(fields)} fields  {report['wall_ms']:>6} ms  "
                          f"S1 {report['system_one_calls']:>2}  LLM {report['llm_calls']:>2}"
                          + (f"  ERROR {report['error']}" if report["error"] else ""))
                    if page:
                        page.close()
        context.close()
    results["summary"] = {agent: summary([r for r in results["runs"] if r["agent"] == agent]) for agent in agents}
    RESULTS.mkdir(exist_ok=True)
    path = RESULTS / f"{target_name}-{datetime.now():%Y-%m-%d-%H%M}.json"
    path.write_text(json.dumps(results, indent=2))
    return path


def show_map(target_name: str) -> None:
    target = TARGETS[target_name]()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context()
        if target.login:
            target.login(context)
        for screen in target.screens:
            page = context.new_page()
            page.goto(screen.url)
            print(screen.url)
            for field in read_form(page)[1]:
                print(f"  {field['role']:11} {field['name']!r}" + (f"  {field['options']}" if field["options"] else ""))
        browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["run", "map"])
    parser.add_argument("--target", default="local", choices=sorted(TARGETS))
    parser.add_argument("--agents", default="mapfill,jev")
    parser.add_argument("--cases", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    if args.command == "map":
        return show_map(args.target)
    agents = args.agents.split(",")
    path = run(args.target, agents, args.cases, args.seed)
    print(f"\nSystem One: {SYSTEM_ONE}   LLM: {LLM_MODEL}   results: {path}")
    for agent, totals in json.loads(path.read_text())["summary"].items():
        print(f"  {agent:8} " + "  ".join(f"{k}={v}" for k, v in totals.items()))


if __name__ == "__main__":
    main()
