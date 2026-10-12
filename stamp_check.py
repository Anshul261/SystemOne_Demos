"""Verify a required stamp on a document image with d1 vision decisions and explicit rules.

The model answers narrow visual questions about the stamp region (is there a stamp, its shape, ink,
issuer, centre word, date, texture). Each answer is compared with a threshold fitted on a separate
calibration split; Python rules turn the checks into accept, reject or human review. A check whose
calibration AUROC is below RELIABLE_AUROC for the chosen model is reported as unverified instead of
being trusted, so a small model escalates rather than guesses.
"""

from __future__ import annotations

import base64
import io
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent
SAMPLES = ROOT / "fixtures" / "stamps"
CALIBRATION = json.loads((ROOT / "fixtures" / "stamp_calibration.json").read_text(encoding="utf-8"))
# Calibration AUROC a check needs before its answer is trusted. d1-omni-600M's shape check fell from
# 0.93 on calibration to 0.86 on test, so the bar sits above it.
RELIABLE_AUROC = 0.95
# Answers this close to their threshold (in log-odds) go to human review instead of deciding: a faint
# genuine stamp re-saved as JPEG moved from 0.18 to 0.15 across a 0.17 threshold.
REVIEW_MARGIN = 0.75
# Region holding the "Official stamp" box on every capture, as page fractions (generous for photo skew).
REGION = tuple(CALIBRATION["region"])
MODEL_NAMES = {"omni": "d1-omni-600M", "3b": "d1-3B"}

# Rule order: the first failing hard check decides a rejection. Spelling is advisory: it sends to review.
CHECKS = [
    {"id": "in_box", "title": "Stamp inside the Official stamp box", "failure": "missing"},
    {"id": "shape", "title": "Round stamp", "failure": "wrong_shape"},
    {"id": "color", "title": "Original blue ink", "failure": "wrong_color"},
    {"id": "issuer", "title": "Issued by Springfield Mutual Insurance", "failure": "wrong_issuer"},
    {"id": "status", "title": "Centre word reads APPROVED", "failure": "wrong_status"},
    {"id": "date", "title": "Dated 14 SEP 2026, the issue date", "failure": "date_mismatch"},
    {"id": "genuine", "title": "Real ink impression, not a pasted image", "failure": "pasted_digital"},
    {"id": "spelling", "title": "Company name spelled correctly", "failure": "misspelled", "advisory": True},
]
REASONS = {
    "valid": "The stamp meets every requirement.",
    "missing": "There is no stamp in the Official stamp box.",
    "outside_box": "A stamp is on the page but not inside the Official stamp box.",
    "wrong_shape": "The stamp is not round.",
    "wrong_color": "The stamp ink is not blue.",
    "photocopy": "This is a black-and-white copy, so the original blue ink cannot be confirmed.",
    "wrong_issuer": "The stamp names a different company.",
    "wrong_status": "The stamp does not say APPROVED.",
    "date_mismatch": "The stamp date does not match the issue date.",
    "pasted_digital": "The stamp looks digitally inserted rather than pressed in ink.",
    "misspelled": "The company name on the stamp may be misspelled.",
}
ADVISORY = {c["id"]: c.get("advisory", False) for c in CHECKS}
EXPECTED_VERDICT = {label: ("accept" if label == "valid" else "review" if label == "misspelled" else "reject")
                    for label in REASONS}


def find_page(image: Image.Image) -> tuple[float, float, float, float] | None:
    """The paper's bounding box as image fractions when a photo shows a desk around it, else None.

    Paper stays low in colour saturation even under shadow or warm light, while a wooden or coloured
    desk does not, so the mask is "unsaturated and not dark". A page that already fills the frame, or a
    desk the same colour as paper, returns None and the image passes through unchanged.
    """
    hsv = np.asarray(image.convert("RGB").resize((max(1, image.width // 8), max(1, image.height // 8))).convert("HSV"),
                     dtype=np.float64) / 255
    paper = (hsv[..., 1] < 0.22) & (hsv[..., 2] > 0.3)
    rows = np.flatnonzero(paper.mean(axis=1) > 0.5)
    cols = np.flatnonzero(paper.mean(axis=0) > 0.5)
    if not len(rows) or not len(cols):
        return None
    h, w = paper.shape
    box = (cols[0] / w, rows[0] / h, (cols[-1] + 1) / w, (rows[-1] + 1) / h)
    span_w, span_h = box[2] - box[0], box[3] - box[1]
    if span_w * span_h < 0.3 or (span_w > 0.92 and span_h > 0.92):
        return None
    return box


def page_area(image: Image.Image, box: tuple[float, float, float, float] | None) -> Image.Image:
    if box is None:
        return image
    w, h = image.size
    return image.crop((int(box[0] * w), int(box[1] * h), int(box[2] * w), int(box[3] * h)))


def region_in_image(box: tuple[float, float, float, float] | None) -> list[float]:
    """The stamp region as fractions of the original image, for drawing it over the upload."""
    x0, y0, x1, y1 = box or (0, 0, 1, 1)
    return [x0 + REGION[0] * (x1 - x0), y0 + REGION[1] * (y1 - y0), x0 + REGION[2] * (x1 - x0), y0 + REGION[3] * (y1 - y0)]


def region(page: Image.Image) -> Image.Image:
    w, h = page.size
    return page.crop((int(REGION[0] * w), int(REGION[1] * h), int(REGION[2] * w), int(REGION[3] * h)))


def reliable(model: str, check: str) -> bool:
    spec = CALIBRATION["models"][model]["checks"].get(check)
    return bool(spec) and spec["calib_auroc"] >= RELIABLE_AUROC


def score(answer: dict[str, Any], positive: str) -> float:
    return float(answer["noul"] if answer["type"] == "noul" else answer["probabilities"][positive])


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def assess(model: str, check: str, answer: dict[str, Any]) -> dict[str, Any]:
    """One check's result: its score against the model's calibrated threshold."""
    spec = CALIBRATION["models"][model]["checks"][check]
    value = score(answer, spec["positive"])
    passed = value >= spec["threshold"]
    margin = logit(value) - logit(spec["threshold"])
    result = {"check": check, "model": model, "score": round(value, 4), "threshold": spec["threshold"],
              "passed": passed, "uncertain": abs(margin) < REVIEW_MARGIN, "margin": round(margin, 3),
              "calib_auroc": spec["calib_auroc"]}
    if answer["type"] == "choice":
        result["probabilities"] = {k: round(v, 4) for k, v in answer["probabilities"].items()}
    return result


def decide(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Accept, reject or review from per-check results. Missing checks count as unverified."""
    statuses, rejection, review = {}, None, []
    for check in CHECKS:
        result = results.get(check["id"])
        if result is None and check.get("advisory"):
            statuses[check["id"]] = "skipped"  # a known blind spot of the model: reported, never blocking
        elif result is None:
            statuses[check["id"]] = "unverified"
            review.append(check["title"])
        elif result.get("uncertain"):
            statuses[check["id"]] = "uncertain"
            review.append(check["title"])
        elif result["passed"]:
            statuses[check["id"]] = "pass"
        elif check.get("advisory"):
            statuses[check["id"]] = "review"
            review.append(check["title"])
        else:
            statuses[check["id"]] = "fail"
            if rejection is None:
                rejection = check["failure"]
                if check["id"] == "color" and result.get("probabilities"):
                    others = {k: v for k, v in result["probabilities"].items() if k != "blue"}
                    if others and max(others, key=others.get) == "grey":
                        rejection = "photocopy"
                if check["id"] == "in_box" and results.get("anywhere", {}).get("passed"):
                    rejection = "outside_box"
    if rejection:
        return {"verdict": "reject", "label": rejection, "reason": REASONS[rejection], "statuses": statuses}
    if review:
        unsure = [c["title"] for c in CHECKS if statuses[c["id"]] in ("unverified", "uncertain")]
        if unsure:
            label, reason = "unverified", "A person should confirm: " + "; ".join(unsure) + "."
        else:
            label, reason = "misspelled", REASONS["misspelled"]
        return {"verdict": "review", "label": label, "reason": reason, "statuses": statuses}
    return {"verdict": "accept", "label": "valid", "reason": REASONS["valid"], "statuses": statuses}


def route(model: str, ask: Callable[[str, list[str], str], dict[str, dict]]) -> dict[str, dict]:
    """Which checks the model answers. `ask(model, checks, "region" | "page")` returns check results.

    The model answers every check it is reliable on in one pass over the stamp region. When the box is
    empty, one page-level pass tells "missing" from "outside_box" if the model is reliable at that.
    """
    results = ask(model, [c["id"] for c in CHECKS if reliable(model, c["id"])], "region")
    if "in_box" in results and not results["in_box"]["passed"] and reliable(model, "anywhere"):
        results.update(ask(model, ["anywhere"], "page"))
    return results


def is_correct(decision: dict[str, Any], expected: str) -> bool:
    """The right verdict, and for a rejection the right reason."""
    return decision["verdict"] == EXPECTED_VERDICT[expected] and (
        decision["verdict"] != "reject" or decision["label"] == expected)


def encode_image(image: Image.Image, max_side: int = 1000) -> bytes:
    """A display copy for the UI and audit stream; the model always gets the full-resolution image."""
    image = image.convert("RGB")
    image.thumbnail((max_side, max_side))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=82)
    return buffer.getvalue()


def load_samples() -> list[dict[str, Any]]:
    return json.loads((SAMPLES / "labels.json").read_text(encoding="utf-8"))["items"]


def decode_upload(data_url: str) -> Image.Image:
    header, _, payload = data_url.partition(",")
    if not header.startswith("data:image/") or not payload:
        raise ValueError("Upload a JPEG or PNG image of the document.")
    raw = base64.b64decode(payload, validate=True)
    if len(raw) > 12 * 1024 * 1024:
        raise ValueError("Use an image under 12 MB.")
    image = Image.open(io.BytesIO(raw))
    image.load()
    if min(image.size) < 400:
        raise ValueError("The image is too small to read a stamp. Use at least 400 pixels on each side.")
    return image.convert("RGB")


class StampCheck:
    """Run one document through the model(s) and the rules, emitting events for the UI and the audit log."""

    def __init__(self, client, model: str, on_event: Callable[[dict[str, Any], bytes | None], None] | None = None):
        if model not in MODEL_NAMES:
            raise ValueError(f"Unknown stamp model: {model}")
        from browser_forms import AuditLog

        self.client, self.model, self.on_event = client, model, on_event
        self.audit = AuditLog(f"stamp-check-{model}")

    def emit(self, event: str, image: bytes | None = None, **details: Any) -> None:
        self.audit.write(event, **details)
        if self.on_event:
            self.on_event({"event": event, **details}, image)

    def ask(self, model: str, checks: list[str], image: Image.Image, which: str, doc_id: str) -> dict[str, dict]:
        questions = {c: CALIBRATION["models"][model]["checks"][c]["question"] for c in checks}
        self.emit("model_call_started", doc_id=doc_id, model=model, model_name=MODEL_NAMES[model], image=which,
                  questions={c: q["instructions"] for c, q in questions.items()})
        answers, model_ms = self.client.system_one(model, questions, [image])
        self.timings.append(model_ms)
        results = {}
        for check in checks:
            result = assess(model, check, answers[check])
            results[check] = result
            self.emit("check_scored", doc_id=doc_id, model_ms=model_ms, image=which, **result)
        self.emit("model_call_finished", doc_id=doc_id, model=model, image=which, model_ms=model_ms,
                  questions=len(checks))
        return results

    def run(self, page: Image.Image, doc: dict[str, Any]) -> dict[str, Any]:
        doc_id = doc["id"]
        started = time.monotonic()
        self.emit("document_received", encode_image(page), doc_id=doc_id, source=doc.get("source", "sample"),
                  expected=doc.get("label"), capture=doc.get("capture"), size=list(page.size),
                  model=self.model, model_name=MODEL_NAMES[self.model])
        box = find_page(page)
        if box:
            self.emit("page_found", doc_id=doc_id, box=[round(v, 4) for v in box])
            page = page_area(page, box)
        crop = region(page)
        self.emit("region_selected", encode_image(crop), doc_id=doc_id, region=[round(v, 4) for v in region_in_image(box)],
                  crop_size=list(crop.size))

        self.timings: list[float] = []
        images = {"region": crop, "page": page}
        results = route(self.model, lambda model, checks, which: self.ask(model, checks, images[which], which, doc_id))
        decision = decide(results)
        expected = doc.get("label")
        outcome = {
            "doc_id": doc_id, "model": self.model, "model_name": MODEL_NAMES[self.model], **decision,
            "model_ms": round(sum(self.timings), 1), "model_calls": len(self.timings),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
            "expected": expected, "expected_verdict": EXPECTED_VERDICT.get(expected) if expected else None,
            "correct": is_correct(decision, expected) if expected else None,
        }
        self.emit("verdict", **outcome)
        return outcome


def run_stamp_check(client, model: str, documents: list[tuple[Image.Image, dict[str, Any]]],
                    on_event: Callable[[dict[str, Any], bytes | None], None] | None = None) -> Path:
    checker = StampCheck(client, model, on_event)
    checker.emit("check_started", total=len(documents), model=model, model_name=MODEL_NAMES[model])
    outcomes = [checker.run(page, doc) for page, doc in documents]
    labelled = [o for o in outcomes if o["correct"] is not None]
    checker.emit("completed", checked=len(outcomes), correct=sum(o["correct"] for o in labelled),
                 labelled=len(labelled), model_ms=round(sum(o["model_ms"] for o in outcomes), 1))
    return checker.audit.path
