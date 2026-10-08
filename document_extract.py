"""Extract labelled spans of any length from real PDF pages and stream them with their page boxes.

The extraction service renders each page, reads its text layer, runs a GLiNER2.5 boundary model over
the page text, and maps every span back to one box per text line. This module validates the request,
walks the pages, and emits events for the UI and the audit log.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DOCUMENTS = ROOT / "fixtures" / "documents"
MODEL_NAMES = {"multi": "GLiNER2.5 Multi", "base": "GLiNER2.5 Base"}
MODEL_SIZES = {"multi": "287M · multilingual", "base": "194M · English"}
LABEL = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
MAX_LABELS = 12
MAX_PAGES = 40


def load_samples() -> list[dict[str, Any]]:
    return json.loads((DOCUMENTS / "samples.json").read_text(encoding="utf-8"))["documents"]


def validate_schema(schema: dict[str, str]) -> dict[str, str]:
    if not isinstance(schema, dict) or not 1 <= len(schema) <= MAX_LABELS:
        raise ValueError(f"Use between 1 and {MAX_LABELS} labels.")
    clean = {}
    for label, description in schema.items():
        label = str(label).strip()
        if not LABEL.match(label):
            raise ValueError(f"Label {label!r} must be lowercase letters, digits and underscores, starting with a letter.")
        description = str(description).strip()
        if not description or len(description) > 400:
            raise ValueError(f"Give {label} a description of at most 400 characters.")
        clean[label] = description
    return clean


def decode_pdf(data_url: str) -> bytes:
    header, _, payload = data_url.partition(",")
    if header != "data:application/pdf;base64" or not payload:
        raise ValueError("Upload a PDF file.")
    data = base64.b64decode(payload, validate=True)
    if len(data) > 25 * 1024 * 1024 or not data.startswith(b"%PDF"):
        raise ValueError("Upload a PDF under 25 MB.")
    return data


def run_extraction(
    client,
    model: str,
    document: dict[str, Any],
    pdf: bytes,
    pages: list[int] | str,
    schema: dict[str, str],
    threshold: float = 0.5,
    on_event: Callable[[dict[str, Any], bytes | None], None] | None = None,
    segment: bool = True,
) -> Path:
    """Extract `pages` ("all" or page numbers) and emit one event per page, plus a summary."""
    from browser_forms import AuditLog

    if model not in MODEL_NAMES:
        raise ValueError(f"Unknown extraction model: {model}")
    schema = validate_schema(schema)
    audit = AuditLog(f"extract-{document['id']}")
    digest = hashlib.sha256(pdf).hexdigest()[:16]

    def emit(event: str, image: bytes | None = None, **details: Any) -> None:
        audit.write(event, **details)
        if on_event:
            on_event({"event": event, **details}, image)

    emit("extraction_started", doc_id=document["id"], title=document.get("title", "Your PDF"),
         source=document.get("source", "upload"), model=model, model_name=MODEL_NAMES[model],
         labels=schema, threshold=threshold, segment=segment)
    queue = [pages[0]] if isinstance(pages, list) else [1]
    done, totals = [], {"spans": 0, "model_ms": 0.0, "longest": None, "beyond_cap": 0}
    while queue:
        number = queue.pop(0)
        started = time.monotonic()
        result = client.extract(model, pdf, number, schema, threshold, segment)
        image = base64.b64decode(result.pop("image"))
        key = f"{digest}-{number}"
        emit("page_ready", image, doc_id=document["id"], page=number, page_count=result["page_count"],
             image_key=key, image_size=result["image_size"], words=result["words"], chars=result["chars"],
             parse_ms=result["parse_ms"], note=result.get("note"))
        spans = result["spans"]
        longest = max(spans, key=lambda s: s["words"], default=None)
        beyond = sum(1 for s in spans if s["legacy_cut"])
        emit("page_extracted", doc_id=document["id"], page=number, spans=spans, model_ms=result["model_ms"],
             blocks=result.get("blocks", 1), block_marks=result.get("block_marks", []),
             elapsed_ms=round((time.monotonic() - started) * 1000, 1), beyond_cap=beyond,
             longest_words=longest["words"] if longest else 0)
        done.append(number)
        totals["spans"] += len(spans)
        totals["model_ms"] += result["model_ms"]
        totals["beyond_cap"] += beyond
        if longest and (totals["longest"] is None or longest["words"] > totals["longest"]["words"]):
            totals["longest"] = {"page": number, "label": longest["label"], "words": longest["words"],
                                 "lines": len(longest["lines"]), "text": longest["text"][:240]}
        if pages == "all" and number == done[0]:
            queue = list(range(1, min(result["page_count"], MAX_PAGES) + 1))
            queue.remove(number)
        elif isinstance(pages, list) and len(done) < len(pages):
            queue = [pages[len(done)]]
    emit("completed", doc_id=document["id"], pages=done, spans=totals["spans"],
         model_ms=round(totals["model_ms"], 1), beyond_cap=totals["beyond_cap"], longest=totals["longest"])
    return audit.path
