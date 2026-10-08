"""Score lab_report.read_report on the scanned haematology report against its answer key.

Usage: python eval_lab.py   (needs the extraction service on :8030 and the d1 service on :8020)
"""

from __future__ import annotations

import base64
import io
import json
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from d1_client import D1Client  # noqa: E402
from lab_report import read_report  # noqa: E402
from lab_report import score as lab_score  # noqa: E402

TRUTH = json.loads((ROOT / "fixtures/documents/evya-haematology-report.truth.json").read_text())
def score(result: dict, truth: dict) -> dict:
    return lab_score(result["records"], truth)


def documents() -> list[tuple[str, bytes, dict]]:
    docs = [("real haematology report", (ROOT / "fixtures/documents/evya-haematology-report.webp").read_bytes(), TRUTH),
            ("real liver report", (ROOT / "fixtures/documents/lab/ilbs-liver-report.webp").read_bytes(),
             json.loads((ROOT / "fixtures/documents/lab/ilbs-liver-report.truth.json").read_text()))]
    for batch in ("lab_reports", "lab_reports_fresh", "lab_reports_final"):  # batches 1-2 informed fixes; batch 3 was scored once
        held = ROOT / "experiments/d1/data" / batch
        if (held / "manifest.json").exists():
            for m in json.loads((held / "manifest.json").read_text()):
                docs.append((f"{batch} {m['file']} ({m['capture']})", (held / m["file"]).read_bytes(),
                             json.loads((held / m["truth"]).read_text())))
    return docs


def main() -> None:
    configs = [tuple(c.split("/")) for c in (sys.argv[1:] or ["3b/3b"])]
    d1 = D1Client()
    report = {}
    for name, data, truth in documents():
        ocr = httpx.post("http://127.0.0.1:8030/ocr", json={"image": base64.b64encode(data).decode()}, timeout=300).json()
        image = Image.open(io.BytesIO(data)).convert("RGB")
        for model, vision in configs:
            started = time.perf_counter()
            try:
                result = read_report([dict(c) for c in ocr["cells"]], image, d1.system_one_batch, model, vision)
            except ValueError as exc:
                print(f"{name} {model}/{vision}: FAILED {exc}")
                report[f"{name} {model}/{vision}"] = {"error": str(exc)}
                continue
            wall = (time.perf_counter() - started) * 1000
            sc = score(result, truth)
            report[f"{name} {model}/{vision}"] = {"score": sc, "headers": result["headers"], "model_ms": result["model_ms"],
                                                  "ocr_ms": ocr["ocr_ms"]}
            summary = " ".join(f"{k} {v[0]}/{v[1]}" for k, v in sc["totals"].items() if v[1])
            print(f"\n== {name} · {model}/{vision}: {summary} · extra {sc['extra_records']} · {wall:.0f} ms")
            for row in sc["rows"]:
                bad = {k: v for k, v in row.items() if k not in ("test", "got") and v != "ok"}
                if bad or not row["got"]:
                    print(f"   {row['test']:22} -> {row['got']!s:20} {bad}")
    (ROOT / "experiments/d1/results/lab_eval.json").write_text(json.dumps(report, indent=1))
    for batch in ("lab_reports ", "lab_reports_fresh ", "lab_reports_final "):
        totals, failed = {}, 0
        for key, r in report.items():
            if not key.startswith(batch):
                continue
            if "error" in r:
                failed += 1
                continue
            for k, (a, b) in r["score"]["totals"].items():
                totals[k] = [totals.get(k, [0, 0])[0] + a, totals.get(k, [0, 0])[1] + b]
        if totals:
            print(f"\nTOTAL {batch.strip()}: " + " ".join(f"{k} {a}/{b}" for k, (a, b) in totals.items() if b)
                  + f" · tables not found: {failed}")


if __name__ == "__main__":
    main()
