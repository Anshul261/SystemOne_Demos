"""Fit the stamp checks on the calibration split, then score the whole flow once on the test split.

For each model and attribute: try every question variant on calib, keep the best by AUROC, and fit the
threshold on calib (maximising balanced accuracy). Writes the chosen questions and thresholds to
stamp_calibration.json for the demo, and test-split results to OUT.json.

Usage: python calibrate.py {omni|3b} CALIB_DIR TEST_DIR OUT.json
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

# Region that holds the stamp box on every capture, as fractions of the page (generous for photo skew).
REGION = (0.55, 0.68, 0.99, 0.99)
STAMPED = {"valid", "wrong_shape", "wrong_color", "wrong_issuer", "misspelled", "wrong_status", "date_mismatch",
           "photocopy", "pasted_digital"}


def choice(instructions: str, criteria: dict) -> dict:
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def noul(instructions: str) -> dict:
    return {"type": "noul", "instructions": instructions}


# attribute -> image ("region" or "page"), positive option, truth(label) (None = not scored), variants
ATTRS = {
    "anywhere": ("page", "stamp", lambda l: l != "missing", {
        "c1": choice("Does this document page carry a stamp?", {"stamp": "A document page with an ink stamp printed on it",
                                                               "none": "A document page with no stamp anywhere, only typed text"}),
        "c2": choice("Describe this document.", {"stamp": "A typed document with a round or square rubber stamp mark",
                                                 "none": "A typed document with a signature but no stamp mark"}),
        "n1": noul("Is there an ink stamp or seal anywhere on this page?"),
    }),
    "in_box": ("region", "stamp", lambda l: l in STAMPED, {
        "c1": choice("What is inside the box?", {"stamp": "An ink stamp impression inside a box",
                                                 "empty": "An empty box with only blank paper inside"}),
        "c2": choice("Describe the image.", {"stamp": "A rubber stamp mark with printed text",
                                             "empty": "A blank rectangle on white paper"}),
        "n1": noul("Is there an ink stamp inside the box labelled 'Official stamp'?"),
    }),
    "shape": ("region", "round", lambda l: (l != "wrong_shape") if l in STAMPED else None, {
        "c1": choice("What shape is the stamp?", {"round": "A round circular stamp with text curving around its edge",
                                                  "rect": "A rectangular stamp with straight edges and straight lines of text"}),
        "c2": choice("What does the stamp look like?", {"round": "A circle-shaped seal, like a ring",
                                                        "rect": "A box-shaped stamp, like a rectangle"}),
        "c3": choice("Describe the image.", {"rect": "A rectangular frame stamp", "round": "A ring-shaped round stamp"}),
        "n1": noul("Is the stamp round?"),
    }),
    "color": ("region", "blue", lambda l: (l not in ("wrong_color", "photocopy")) if l in STAMPED else None, {
        "c1": choice("What colour is the stamp ink?", {"blue": "A stamp in blue ink", "red": "A stamp in red ink",
                                                       "grey": "A stamp in black or grey, like a photocopy"}),
        "c2": choice("Describe the image.", {"blue": "Blue ink marks on paper", "red": "Red ink marks on paper",
                                             "grey": "Black and grey marks on a black-and-white copy"}),
        "n1": noul("Is the stamp printed in blue ink?"),
    }),
    "issuer": ("region", "springfield", lambda l: (l != "wrong_issuer") if l in STAMPED - {"misspelled"} else None, {
        "c1": choice("Which company name is on the stamp?", {"springfield": "A stamp reading SPRINGFIELD MUTUAL INSURANCE",
                                                             "shelbyville": "A stamp reading SHELBYVILLE MUTUAL INSURANCE"}),
        "n1": noul("Does the stamp read SPRINGFIELD MUTUAL INSURANCE?"),
    }),
    "spelling": ("region", "correct", lambda l: (l != "misspelled") if l in STAMPED - {"wrong_issuer"} else None, {
        "c1": choice("Read the first word around the top of the stamp letter by letter. Which spelling is printed?",
                     {"correct": "SPRINGFIELD", "fil": "SPRINGFILED", "nf": "SPRINFGIELD", "ri": "SPIRNGFIELD"}),
    }),
    "status": ("region", "approved", lambda l: (l != "wrong_status") if l in STAMPED else None, {
        "c1": choice("Which word is in the centre of the stamp?", {"approved": "A stamp with the word APPROVED in the centre",
                                                                   "void": "A stamp with the word VOID in the centre",
                                                                   "received": "A stamp with the word RECEIVED in the centre"}),
        "n1": noul("Does the centre of the stamp say APPROVED?"),
    }),
    "date": ("region", "match", lambda l: (l != "date_mismatch") if l in STAMPED else None, {
        "c1": choice("Which date is on the stamp?", {"match": "A stamp dated 14 SEP 2026", "other": "A stamp with some other date"}),
        "n1": noul("Does the date on the stamp read 14 SEP 2026?"),
    }),
    "genuine": ("region", "ink", lambda l: (l != "pasted_digital") if l in STAMPED - {"photocopy"} else None, {
        "c1": choice("How was this stamp made?", {"ink": "A real rubber-stamp impression with uneven, patchy ink",
                                                  "digital": "A clean digital stamp image with perfectly solid, uniform colour"}),
        "c2": choice("Describe the stamp texture.", {"ink": "Grainy, faded and blotchy ink", "digital": "Crisp, flat, computer-drawn lines"}),
        "n1": noul("Is this a real ink impression from a rubber stamp, rather than a digitally inserted image?"),
    }),
}


def region(page: Image.Image) -> Image.Image:
    w, h = page.size
    return page.crop((int(REGION[0] * w), int(REGION[1] * h), int(REGION[2] * w), int(REGION[3] * h)))


def positive_score(answer: dict, pos: str) -> float:
    return answer["noul"] if answer["type"] == "noul" else answer["probabilities"][pos]


def score_split(model, data: Path, attrs: dict) -> tuple[list[dict], list[float]]:
    """Every variant of every attribute on every document: page questions in one pass, region ones in another."""
    meta = json.loads((data / "labels.json").read_text())
    rows, ms = [], []
    page_qs = {f"{a}.{v}": q for a, (img, *_r, vs) in attrs.items() if img == "page" for v, q in vs.items()}
    region_qs = {f"{a}.{v}": q for a, (img, *_r, vs) in attrs.items() if img == "region" for v, q in vs.items()}
    for it in meta["items"]:
        page = Image.open(data / it["file"]).convert("RGB")
        answers = {}
        for qs, img in ((page_qs, page), (region_qs, region(page))):
            torch.cuda.synchronize(); t0 = time.perf_counter()
            answers.update(model.system_one(None, qs, images=[img])["answers"])
            torch.cuda.synchronize(); ms.append((time.perf_counter() - t0) * 1000)
        rows.append({"file": it["file"], "label": it["label"], "capture": it["capture"], "answers": answers})
    return rows, ms


def fit_threshold(scores: list[float], truth: list[bool]) -> float:
    def bacc(t):
        tp = sum(s >= t and y for s, y in zip(scores, truth)); tn = sum(s < t and not y for s, y in zip(scores, truth))
        return (tp / max(1, sum(truth)) + tn / max(1, len(truth) - sum(truth))) / 2
    cands = sorted(set(scores))
    mids = [(a + b) / 2 for a, b in zip(cands, cands[1:])] or cands
    return max(mids, key=lambda t: (bacc(t), -abs(t - 0.5)))


def verdict(get) -> tuple[str, list[str]]:
    """The stamp rules, in order. `get(attr)` -> (passes, answer)."""
    trace = []
    for attr, failure in (("anywhere", "missing"), ("in_box", "outside_box"), ("shape", "wrong_shape"),
                          ("color", None), ("issuer", "wrong_issuer"), ("spelling", "misspelled"),
                          ("status", "wrong_status"), ("date", "date_mismatch"), ("genuine", "pasted_digital")):
        passed, answer = get(attr)
        trace.append(f"{attr}:{'pass' if passed else 'FAIL'}")
        if passed:
            continue
        if attr == "anywhere":
            return "missing", trace
        if attr == "color":  # a non-blue stamp is either the wrong ink or a grey copy
            other = max((k for k in answer.get("probabilities", {}) if k != "blue"),
                        key=lambda k: answer["probabilities"][k], default="red")
            return ("photocopy" if other == "grey" else "wrong_color"), trace
        return failure, trace
    return "valid", trace


def main() -> None:
    which, calib_dir, test_dir, out_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])
    repo, dtype, rev = MODELS[which]
    model = AutoModel.from_pretrained(repo, trust_remote_code=True, dtype=dtype, revision=rev).to("cuda")

    calib, _ = score_split(model, calib_dir, ATTRS)
    chosen = {}
    print(f"\n{repo}: question choice on the calibration split ({len(calib)} documents)")
    for attr, (img, pos, truth_fn, variants) in ATTRS.items():
        best = None
        for v in variants:
            pairs = [(positive_score(r["answers"][f"{attr}.{v}"], pos), truth_fn(r["label"])) for r in calib]
            pairs = [(s, t) for s, t in pairs if t is not None]
            scores, truth = [s for s, _ in pairs], [t for _, t in pairs]
            auc = auroc(scores, truth)
            if best is None or auc > best[1]:
                best = (v, auc, fit_threshold(scores, truth))
        v, auc, thr = best
        chosen[attr] = {"image": img, "positive": pos, "variant": v, "question": variants[v],
                        "threshold": round(thr, 4), "calib_auroc": round(auc, 3)}
        print(f"  {attr:<9} best {v}  calib AUROC {auc:.3f}  threshold {thr:.3f}")

    test, ms = score_split(model, test_dir, {a: (img, pos, fn, {chosen[a]["variant"]: chosen[a]["question"]})
                                             for a, (img, pos, fn, _) in ATTRS.items()})
    results = {"model": repo, "revision": rev, "region": REGION, "chosen": chosen, "attrs": {}, "rows": []}
    print(f"\n{repo}: test split ({len(test)} documents), thresholds frozen")
    for attr, (img, pos, truth_fn, _) in ATTRS.items():
        c = chosen[attr]
        pairs = [(positive_score(r["answers"][f"{attr}.{c['variant']}"], pos), truth_fn(r["label"])) for r in test]
        pairs = [(s, t) for s, t in pairs if t is not None]
        acc = sum((s >= c["threshold"]) == t for s, t in pairs)
        auc = auroc([s for s, _ in pairs], [t for _, t in pairs])
        results["attrs"][attr] = {"accuracy": f"{acc}/{len(pairs)}", "auroc": round(auc, 3)}
        print(f"  {attr:<9} acc {acc:>3}/{len(pairs):<3} AUROC {auc:.3f}")
    for r in test:
        def get(attr, r=r):
            c = chosen[attr]; a = r["answers"][f"{attr}.{c['variant']}"]
            return positive_score(a, c["positive"]) >= c["threshold"], a
        pred, trace = verdict(get)
        scores = {a: round(positive_score(r["answers"][f"{a}.{chosen[a]['variant']}"], chosen[a]["positive"]), 5) for a in chosen}
        probs = {a: r["answers"][f"{a}.{chosen[a]['variant']}"].get("probabilities") for a in chosen}
        results["rows"].append({"file": r["file"], "label": r["label"], "capture": r["capture"], "pred": pred, "trace": trace,
                                "scores": scores, "probabilities": probs})
    rows = results["rows"]
    exact = sum(r["pred"] == r["label"] for r in rows)
    fraud_passed = [r for r in rows if r["pred"] == "valid" and r["label"] != "valid"]
    valid_flagged = [r for r in rows if r["pred"] != "valid" and r["label"] == "valid"]
    n_valid = sum(r["label"] == "valid" for r in rows)
    results["summary"] = {"exact": f"{exact}/{len(rows)}", "fraud_passed": f"{len(fraud_passed)}/{len(rows) - n_valid}",
                          "valid_flagged": f"{len(valid_flagged)}/{n_valid}", "median_ms_per_pass": round(statistics.median(ms), 1),
                          "per_class": {}, "per_capture": {}}
    for key, field in (("per_class", "label"), ("per_capture", "capture")):
        for k in sorted({r[field] for r in rows}):
            sub = [r for r in rows if r[field] == k]
            results["summary"][key][k] = f"{sum(r['pred'] == r['label'] for r in sub)}/{len(sub)}"
    s = results["summary"]
    print(f"\n  VERDICT exact {s['exact']}  fraud passed {s['fraud_passed']}  valid flagged {s['valid_flagged']}  "
          f"median {s['median_ms_per_pass']} ms per pass (2 passes per document)")
    print("  per class  ", s["per_class"])
    print("  per capture", s["per_capture"])
    print("  fraud that passed:", [r["label"] for r in fraud_passed])
    out_path.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
