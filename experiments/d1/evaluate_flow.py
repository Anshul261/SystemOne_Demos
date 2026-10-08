"""Export the frozen stamp calibration for the demo, then score the demo's own flow on the test split.

Reads results/calib_{omni,3b}.json (questions and thresholds chosen on the calibration split, plus each
test document's answers), writes ../../fixtures/stamp_calibration.json, and replays the test answers
through stamp_check.route / assess / decide — the same code the UI runs — for omni, 3b and cascade.

Usage: python evaluate_flow.py OUT.json
"""

from __future__ import annotations

import importlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def export() -> dict[str, dict]:
    runs = {m: json.loads((HERE / f"results/calib_{m}.json").read_text()) for m in ("omni", "3b")}
    calibration = {
        "region": runs["omni"]["region"],
        "note": "Questions and thresholds chosen on the calibration split only (experiments/d1/calibrate.py).",
        "models": {},
    }
    for m, run in runs.items():
        calibration["models"][m] = {"repo": run["model"], "revision": run["revision"], "checks": {
            attr: {"question": c["question"], "positive": c["positive"], "threshold": c["threshold"],
                   "calib_auroc": c["calib_auroc"], "test_auroc": run["attrs"][attr]["auroc"]}
            for attr, c in run["chosen"].items()}}
    (ROOT / "fixtures/stamp_calibration.json").write_text(json.dumps(calibration, indent=1) + "\n")
    return runs


def main() -> None:
    out_path = Path(sys.argv[1])
    runs = export()
    sys.path.insert(0, str(ROOT))
    stamp_check = importlib.import_module("stamp_check")
    rows = {m: {r["file"]: r for r in run["rows"]} for m, run in runs.items()}
    report = {}
    for mode in ("omni", "3b"):
        outcomes = []
        for file, ref in rows["omni"].items():
            calls = []

            def ask(model, checks, which, file=file, calls=calls):
                calls.append((model, which, len(checks)))
                stored = rows[model][file]
                results = {}
                for c in checks:
                    spec = stamp_check.CALIBRATION["models"][model]["checks"][c]
                    probs = stored["probabilities"][c]
                    answer = ({"type": "choice", "probabilities": probs} if probs is not None
                              else {"type": "noul", "noul": stored["scores"][c]})
                    assert abs(stamp_check.score(answer, spec["positive"]) - stored["scores"][c]) < 1e-4
                    results[c] = stamp_check.assess(model, c, answer)
                return results

            decision = stamp_check.decide(stamp_check.route(mode, ask))
            outcomes.append({"file": file, "expected": ref["label"], "capture": ref["capture"],
                             "verdict": decision["verdict"], "predicted": decision["label"],
                             "correct": stamp_check.is_correct(decision, ref["label"]),
                             "calls": " + ".join(f"{m}:{w}" for m, w, _ in calls)})
        n = len(outcomes)
        frauds = [o for o in outcomes if o["expected"] != "valid"]
        valids = [o for o in outcomes if o["expected"] == "valid"]
        classes = sorted({o["expected"] for o in outcomes})

        def tally(key, value):
            group = [o for o in outcomes if o[key] == value]
            return f"{sum(o['correct'] for o in group)}/{len(group)}"

        summary = {
            "correct": f"{sum(o['correct'] for o in outcomes)}/{n}",
            "fraud_accepted": f"{sum(o['verdict'] == 'accept' for o in frauds)}/{len(frauds)}",
            "valid_accepted": f"{sum(o['verdict'] == 'accept' for o in valids)}/{len(valids)}",
            "sent_to_review": f"{sum(o['verdict'] == 'review' for o in outcomes)}/{n}",
            "calls": dict(Counter(o["calls"] for o in outcomes)),
            "per_class": {k: tally("expected", k) for k in classes},
            "per_capture": {k: tally("capture", k) for k in sorted({o["capture"] for o in outcomes})},
            "outcomes_by_class": {k: dict(Counter(o["verdict"] + (f"/{o['predicted']}" if o["verdict"] == "reject" else "")
                                                  for o in outcomes if o["expected"] == k)) for k in classes},
        }
        report[mode] = {"summary": summary, "outcomes": outcomes}
        print(f"\n== {mode}: correct {summary['correct']}  fraud accepted {summary['fraud_accepted']}  "
              f"valid accepted {summary['valid_accepted']}  review {summary['sent_to_review']}")
        for k, v in summary["outcomes_by_class"].items():
            print(f"   {k:<15} {summary['per_class'][k]:>6}  {v}")
        print("   per capture", summary["per_capture"])
        print("   model calls", summary["calls"])
    out_path.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
