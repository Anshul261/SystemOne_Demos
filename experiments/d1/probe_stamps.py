"""Can d1 verify a required stamp and catch wrong ones? Four ways of asking, scored against labels.

  direct      one 9-way "what is wrong with the stamp" choice over the full page
  decomposed  attribute questions over the full page; Python rules compute the verdict
  crop        attribute questions over the known stamp box (more pixels per letter) plus one
              full-page "any stamp anywhere?" question to tell outside_box from missing
  reference   the official reference stamp and the box crop as two images: "does it match?"

Usage: python probe_stamps.py {3b|omni} DATA_DIR OUT.json
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import torch
from PIL import Image
from transformers import AutoModel

MODELS = {"3b": ("LiquidAI/d1-3B", torch.bfloat16, "da1fe36a861f24690f27f622dca1d8688503d113"),
          "omni": ("LiquidAI/d1-omni-600M", torch.float16, "414f8d6438174f5b2133a9c21a478fc42625e308")}

SPEC_TEXT = ("An insurance certificate. It is valid only with the official stamp: round, blue ink, reading "
             "SPRINGFIELD MUTUAL INSURANCE around the top and CLAIMS DEPARTMENT around the bottom, with APPROVED "
             "and the issue date 14 SEP 2026 in the centre, placed inside the box labelled 'Official stamp'.")

DIRECT = {"verdict": {"type": "choice", "instructions": "Check the stamp on this certificate against the requirement. What is its status?",
                      "criteria": {
                          "valid": "The correct official stamp is present in the box and meets every requirement",
                          "missing": "There is no stamp anywhere on the document",
                          "outside_box": "A stamp is present but not inside the Official stamp box",
                          "wrong_shape": "The stamp is rectangular instead of round",
                          "wrong_color": "The stamp ink is red instead of blue",
                          "wrong_issuer": "The stamp names a different insurance company",
                          "misspelled": "The company name on the stamp is misspelled",
                          "wrong_status": "The centre of the stamp says something other than APPROVED",
                          "date_mismatch": "The date on the stamp does not match 14 SEP 2026",
                      }}}

ATTRS = {
    "present": {"type": "noul", "instructions": "Is there an ink stamp or seal in this image?"},
    "shape": {"type": "choice", "instructions": "What shape is the stamp?",
              "criteria": {"round": "Round or circular", "rect": "Rectangular or square", "none": "There is no stamp"}},
    "color": {"type": "choice", "instructions": "What colour is the stamp ink?",
              "criteria": {"blue": "Blue ink", "red": "Red ink", "black": "Black ink", "none": "There is no stamp"}},
    "issuer": {"type": "choice", "instructions": "Which organisation name is printed around the stamp?",
               "criteria": {"springfield": "SPRINGFIELD MUTUAL INSURANCE", "shelbyville": "SHELBYVILLE MUTUAL INSURANCE",
                            "other": "Another organisation or unreadable", "none": "There is no stamp"}},
    "spelling": {"type": "noul", "instructions": "Is the organisation name on the stamp spelled exactly S-P-R-I-N-G-F-I-E-L-D, with every letter in the correct order?"},
    "status": {"type": "choice", "instructions": "Which word is stamped in the centre?",
               "criteria": {"approved": "APPROVED", "void": "VOID", "received": "RECEIVED", "paid": "PAID", "none": "There is no stamp"}},
    "date": {"type": "noul", "instructions": "Does the date on the stamp read exactly 14 SEP 2026?"},
}
IN_BOX = {"type": "noul", "instructions": "Is there an ink stamp inside the rectangular box labelled 'Official stamp' at the bottom right of the page?"}
REFERENCE = {"match": {"type": "noul", "instructions": "The first image is the official reference stamp. Does the second image contain a stamp that exactly matches it: same shape, same ink colour, same organisation name spelled the same, same centre word and same date?"}}


def rules(present_anywhere: bool, in_box: bool, a: dict) -> str:
    """The verdict from attribute answers, checked in a fixed order."""
    if not present_anywhere and not in_box:
        return "missing"
    if not in_box:
        return "outside_box"
    if a["shape"]["choice"] == "rect":
        return "wrong_shape"
    if a["color"]["choice"] not in ("blue", "none"):
        return "wrong_color"
    if a["issuer"]["choice"] == "shelbyville" or a["issuer"]["choice"] == "other":
        return "wrong_issuer"
    if a["spelling"]["noul"] < 0.5:
        return "misspelled"
    if a["status"]["choice"] not in ("approved", "none"):
        return "wrong_status"
    if a["date"]["noul"] < 0.5:
        return "date_mismatch"
    return "valid"


def main() -> None:
    which, data, out_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    repo, dtype, rev = MODELS[which]
    meta = json.loads((data / "labels.json").read_text())
    box = meta["box"]
    pad = 24
    model = AutoModel.from_pretrained(repo, trust_remote_code=True, dtype=dtype, revision=rev).to("cuda")
    ref = Image.open(data / "reference_stamp.png").convert("RGB")

    def ask(state, qs, images):
        torch.cuda.synchronize(); t0 = time.perf_counter()
        r = model.system_one(state, qs, images=images)
        torch.cuda.synchronize()
        return r["answers"], (time.perf_counter() - t0) * 1000

    # warm the image shapes once
    first = Image.open(data / meta["items"][0]["file"]).convert("RGB")
    ask(None, ATTRS, [first]); ask(None, ATTRS, [first.crop(box)])

    rows = []
    for item in meta["items"]:
        doc = Image.open(data / item["file"]).convert("RGB")
        crop = doc.crop((box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad))
        row = {"file": item["file"], "label": item["label"], "pred": {}, "ms": {}, "raw": {}}

        a, ms = ask(SPEC_TEXT, DIRECT, [doc])
        row["pred"]["direct"], row["ms"]["direct"] = a["verdict"]["choice"], ms

        a, ms = ask(None, {**ATTRS, "in_box": IN_BOX}, [doc])
        row["pred"]["decomposed"], row["ms"]["decomposed"] = rules(a["present"]["noul"] >= .5, a["in_box"]["noul"] >= .5, a), ms
        row["raw"]["decomposed"] = {k: v.get("choice", round(v.get("noul", 0), 3)) for k, v in a.items()}

        anywhere, ms_any = ask(None, {"present": ATTRS["present"]}, [doc])
        a, ms = ask(None, ATTRS, [crop])
        row["pred"]["crop"] = rules(anywhere["present"]["noul"] >= .5, a["present"]["noul"] >= .5, a)
        row["ms"]["crop"] = ms + ms_any
        row["raw"]["crop"] = {k: v.get("choice", round(v.get("noul", 0), 3)) for k, v in a.items()}

        a, ms = ask(None, REFERENCE, [ref, crop])
        p = a["match"]["noul"]
        row["pred"]["reference"], row["ms"]["reference"], row["raw"]["reference"] = ("valid" if p >= .5 else "flag"), ms, round(p, 3)
        rows.append(row)

    summary = {"model": repo, "revision": rev, "n": len(rows), "strategies": {}}
    print(f"\n{repo}  ({len(rows)} documents, {len(set(r['label'] for r in rows))} classes)")
    print(f"{'strategy':<11} {'9-way':>7} {'accept/flag':>12} {'fraud passed':>13} {'valid flagged':>14} {'median ms':>10}")
    for s in ("direct", "decomposed", "crop", "reference"):
        exact = sum(r["pred"][s] == r["label"] for r in rows) if s != "reference" else None
        accept = [r["pred"][s] == "valid" for r in rows]
        truth = [r["label"] == "valid" for r in rows]
        fraud_passed = sum(a and not t for a, t in zip(accept, truth))
        valid_flagged = sum(t and not a for a, t in zip(accept, truth))
        binary = sum(a == t for a, t in zip(accept, truth))
        per_class = defaultdict(Counter)
        for r in rows:
            per_class[r["label"]][r["pred"][s]] += 1
        med = statistics.median(r["ms"][s] for r in rows)
        summary["strategies"][s] = {"exact": exact, "binary": binary, "fraud_passed": fraud_passed,
                                    "valid_flagged": valid_flagged, "median_ms": round(med, 1),
                                    "per_class": {k: dict(v) for k, v in per_class.items()}}
        n_bad, n_good = sum(not t for t in truth), sum(truth)
        print(f"{s:<11} {('%d/%d' % (exact, len(rows))) if exact is not None else '—':>7} {binary:>8}/{len(rows)} "
              f"{fraud_passed:>8}/{n_bad} {valid_flagged:>10}/{n_good} {med:>10.0f}")
    out_path.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    main()
