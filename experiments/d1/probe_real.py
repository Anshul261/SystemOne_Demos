"""d1 on real documents: StaVer stamp presence (with a blue logo as a distractor) and SIFTA real vs forged.

Reports accuracy at 0.5 and threshold-free AUROC from the yes-probability.
Usage: python probe_real.py {3b|omni} OUT.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModel

from probe_stamps import MODELS

TASKS = {
    "staver": ("stamp", {
        "stamp": {"type": "noul", "instructions": "Is there an ink stamp (a rubber-stamp impression, not a printed company logo) on this document?"},
    }),
    "sifta": ("forged", {
        "forged": {"type": "noul", "instructions": "Does this document look forged or tampered with, for example altered text, pasted elements, a fake stamp or seal, or inconsistent fonts?"},
        "stamp": {"type": "noul", "instructions": "Is there an ink stamp or official seal on this document?"},
    }),
}


def auroc(scores: list[float], positive: list[bool]) -> float:
    pos = [s for s, p in zip(scores, positive) if p]
    neg = [s for s, p in zip(scores, positive) if not p]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def main() -> None:
    which, out_path = sys.argv[1], Path(sys.argv[2])
    repo, dtype, rev = MODELS[which]
    model = AutoModel.from_pretrained(repo, trust_remote_code=True, dtype=dtype, revision=rev).to("cuda")
    report = {"model": repo, "revision": rev, "tasks": {}}
    for task, (positive_label, questions) in TASKS.items():
        meta = json.loads(Path(f"data/{task}/labels.json").read_text())
        rows = []
        for it in meta["items"]:
            img = Image.open(f"data/{task}/{it['file']}").convert("RGB")
            torch.cuda.synchronize(); t0 = time.perf_counter()
            a = model.system_one(None, questions, images=[img])["answers"]
            torch.cuda.synchronize()
            rows.append({"file": it["file"], "label": it["label"], "ms": (time.perf_counter() - t0) * 1000,
                         **{k: v["noul"] for k, v in a.items()}})
        truth = [r["label"] == positive_label for r in rows]
        key = next(iter(questions))
        scores = [r[key] for r in rows]
        acc = sum((s >= .5) == t for s, t in zip(scores, truth))
        res = {"n": len(rows), "positives": sum(truth), "accuracy@0.5": f"{acc}/{len(rows)}",
               "auroc": round(auroc(scores, truth), 3), "median_ms": round(statistics.median(r["ms"] for r in rows[1:]), 1)}
        if task == "sifta":
            res["stamp_flag_rate"] = round(sum(r["stamp"] >= .5 for r in rows) / len(rows), 2)
        report["tasks"][task] = {**res, "rows": rows}
        print(f"{repo} {task}: {res}")
    out_path.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
