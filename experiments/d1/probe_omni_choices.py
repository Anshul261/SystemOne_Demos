"""d1-omni-600M on stamp attributes, asked the way its image head works: choices between whole-image
descriptions, no text state. Several phrasings per attribute; each scored on the documents where the
attribute is defined. Also runs d1-3B with the same questions for a like-for-like comparison.

Usage: python probe_omni_choices.py {omni|3b} DATA_DIR OUT.json
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

from probe_real import auroc
from probe_stamps import MODELS

STAMPED = {"valid", "wrong_shape", "wrong_color", "wrong_issuer", "misspelled", "wrong_status", "date_mismatch"}

# attribute -> (positive option, truth(label) -> bool | None for "not scored", {variant: question})
ATTRS = {
    "in_box": ("stamp", lambda l: l in STAMPED, {
        "a": {"type": "choice", "instructions": "What is inside the box?",
              "criteria": {"stamp": "An ink stamp impression inside a box", "empty": "An empty box with only blank paper inside"}},
        "b": {"type": "choice", "instructions": "Describe the image.",
              "criteria": {"stamp": "A rubber stamp mark with printed text", "empty": "A blank rectangle on white paper"}},
    }),
    "anywhere": ("stamp", lambda l: l != "missing", {
        "a": {"type": "choice", "instructions": "Does this document page carry a stamp?",
              "criteria": {"stamp": "A document page with an ink stamp printed on it", "none": "A document page with no stamp anywhere, only typed text"}},
    }),
    "shape": ("round", lambda l: l != "wrong_shape" if l in STAMPED else None, {
        "a": {"type": "choice", "instructions": "What shape is the stamp?",
              "criteria": {"round": "A round circular stamp with text curving around its edge",
                           "rect": "A rectangular stamp with straight edges and straight lines of text"}},
    }),
    "color": ("blue", lambda l: l != "wrong_color" if l in STAMPED else None, {
        "a": {"type": "choice", "instructions": "What colour is the stamp ink?",
              "criteria": {"blue": "A stamp in blue ink", "red": "A stamp in red ink", "black": "A stamp in black ink"}},
    }),
    "issuer": ("springfield", lambda l: (l != "wrong_issuer") if l in STAMPED - {"misspelled"} else None, {
        "a": {"type": "choice", "instructions": "Which company name is on the stamp?",
              "criteria": {"springfield": "A stamp reading SPRINGFIELD MUTUAL INSURANCE",
                           "shelbyville": "A stamp reading SHELBYVILLE MUTUAL INSURANCE"}},
    }),
    "status": ("approved", lambda l: l != "wrong_status" if l in STAMPED else None, {
        "a": {"type": "choice", "instructions": "Which word is in the centre of the stamp?",
              "criteria": {"approved": "A stamp with the word APPROVED in the centre", "void": "A stamp with the word VOID in the centre",
                           "received": "A stamp with the word RECEIVED in the centre"}},
    }),
    "date": ("match", lambda l: l != "date_mismatch" if l in STAMPED else None, {
        "a": {"type": "choice", "instructions": "Which date is on the stamp?",
              "criteria": {"match": "A stamp dated 14 SEP 2026", "other": "A stamp with some other date"}},
        "b": {"type": "choice", "instructions": "Which year is on the stamp?",
              "criteria": {"match": "A stamp dated in the year 2026", "y2025": "A stamp dated in the year 2025", "y2024": "A stamp dated in the year 2024"}},
    }),
}


def main() -> None:
    which, data, out_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    repo, dtype, rev = MODELS[which]
    model = AutoModel.from_pretrained(repo, trust_remote_code=True, dtype=dtype, revision=rev).to("cuda")
    meta = json.loads((data / "labels.json").read_text())
    b, pad = meta["box"], 24
    report, timings = {"model": repo, "revision": rev, "attrs": {}}, []
    docs = []
    for it in meta["items"]:
        page = Image.open(data / it["file"]).convert("RGB")
        docs.append((it["label"], page, page.crop((b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad))))
    for attr, (pos, truth_fn, variants) in ATTRS.items():
        for vname, q in variants.items():
            scores, truth, picks = [], [], []
            for label, page, crop in docs:
                t = truth_fn(label)
                if t is None:
                    continue
                img = page if attr == "anywhere" else crop
                torch.cuda.synchronize(); t0 = time.perf_counter()
                a = model.system_one(None, {"q": q}, images=[img])["answers"]["q"]
                torch.cuda.synchronize(); timings.append((time.perf_counter() - t0) * 1000)
                scores.append(a["probabilities"][pos]); truth.append(t); picks.append(a["choice"] == pos)
            acc = sum(p == t for p, t in zip(picks, truth))
            both = len(set(truth)) == 2
            res = {"n": len(truth), "accuracy": f"{acc}/{len(truth)}", "auroc": round(auroc(scores, truth), 3) if both else None}
            report["attrs"][f"{attr}.{vname}"] = res
            print(f"{repo[-12:]} {attr + '.' + vname:<12} acc {res['accuracy']:>6}   auroc {res['auroc']}")
    report["median_ms"] = round(statistics.median(timings), 1)
    print("median ms per question", report["median_ms"])
    out_path.write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
