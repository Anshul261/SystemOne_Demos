"""Serve GLiNER2.5 span extraction over real PDF pages, with boxes, for extract_client.py.

POST /extract {"model", "pdf": base64, "page": 1-based, "schema": {label: description}, "threshold"}
returns the rendered page, its spans, and one box per text line each span covers.
"""

import base64
import hashlib
import os
import threading
import time
from typing import Any
from collections import OrderedDict

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from gliner2 import AutoExtractor
from pydantic import BaseModel, Field

from document import Page, blocks, open_pdf, read_image, read_page, span_lines, word_boxes

MODELS = {"multi": "fastino/gliner2.5-multi-v1"}
DEVICE = os.getenv("EXTRACT_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
PRELOAD = [m for m in os.getenv("EXTRACT_MODELS", "multi").split(",") if m]
MAX_PDF_BYTES = 25 * 1024 * 1024
IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"RIFF")  # JPEG, PNG, WebP
LEGACY_SPAN_WIDTH = 8  # the old span-head architecture scored spans up to this many words

loaded: dict[str, AutoExtractor] = {}
locks = {name: threading.Lock() for name in [*MODELS, "ocr"]}
pages: OrderedDict[tuple[str, int], tuple[Page, int, float]] = OrderedDict()
pages_lock = threading.Lock()
app = FastAPI(title="SystemOne extraction service")


def load(name: str) -> AutoExtractor:
    with locks[name]:
        if name not in loaded:
            model = AutoExtractor.from_pretrained(MODELS[name], map_location=DEVICE)
            model.extract_entities("Warm up on a short sentence.", {"thing": "a thing"})
            loaded[name] = model
    return loaded[name]


def page_of(data: bytes, number: int) -> tuple[Page, int, float]:
    """A parsed page from a small LRU cache: rendering, or OCR for an image, is most of the parse time."""
    key = (hashlib.sha256(data).hexdigest(), number)
    with pages_lock:
        if key in pages:
            pages.move_to_end(key)
            return pages[key]  # with the original parse time, so a cached page still reports its OCR cost
    started = time.perf_counter()
    if data.startswith(IMAGE_MAGIC):
        if number != 1:
            raise HTTPException(422, "An image has one page")
        with locks["ocr"]:
            page, count = read_image(data), 1
    else:
        pdf = open_pdf(data)
        try:
            count = len(pdf)
            if not 1 <= number <= count:
                raise HTTPException(422, f"Page {number} does not exist; the document has {count} pages")
            page = read_page(pdf, number - 1)
        finally:
            pdf.close()
    parse_ms = (time.perf_counter() - started) * 1000
    with pages_lock:
        pages[key] = (page, count, parse_ms)
        while len(pages) > 96:
            pages.popitem(last=False)
    return page, count, parse_ms


class Request(BaseModel):
    model: str = "multi"
    pdf: str = ""
    image: str = ""  # a scanned or photographed page (JPEG, PNG or WebP), read with OCR
    page: int = Field(1, ge=1)
    labels: dict[str, str] = Field(alias="schema", min_length=1, max_length=16)
    threshold: float = Field(0.5, ge=0.05, le=0.99)
    segment: bool = True  # split the page at numbered items before extracting


@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE, "loaded": sorted(loaded), "available": MODELS}


@app.post("/extract")
def extract(request: Request):
    if request.model not in MODELS:
        raise HTTPException(422, f"Unknown model {request.model!r}; choose one of {sorted(MODELS)}")
    data = base64.b64decode(request.pdf or request.image, validate=True)
    if len(data) > MAX_PDF_BYTES or not (data.startswith(b"%PDF") or data.startswith(IMAGE_MAGIC)):
        raise HTTPException(422, "Send a PDF or a JPEG, PNG or WebP image under 25 MB")
    page, count, parse_ms = page_of(data, request.page)
    response = {"page": request.page, "page_count": count, "width": page.width, "height": page.height,
                "image": base64.b64encode(page.image).decode("ascii"), "image_type": "image/jpeg",
                "image_size": list(page.image_size),
                "chars": len(page.text), "words": len(page.text.split()), "parse_ms": round(parse_ms, 1),
                "spans": [], "model_ms": 0.0, "model": MODELS[request.model],
                "text_source": "ocr" if data.startswith(IMAGE_MAGIC) else "pdf"}
    if not page.text.strip():
        response["note"] = "This page has no text layer. Scanned pages need OCR first."
        return response
    model = load(request.model)
    with locks[request.model], torch.inference_mode():
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        # Split before extracting: scanned tables by visual row, PDFs at numbered items.
        ranges = ((page.rows or blocks(page.text)) if request.segment else [(0, len(page.text))])
        results = model.batch_extract_entities([page.text[a:b] for a, b in ranges], request.labels, batch_size=16,
                                               include_spans=True, include_confidence=True, threshold=request.threshold)
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        response["model_ms"] = round((time.perf_counter() - started) * 1000, 1)
    response["blocks"] = len(ranges)
    response["split"] = "none" if not request.segment else "rows" if page.rows else "items"
    # A mark where each numbered item starts, so the split is visible on the page (rows need none).
    response["block_marks"] = ([next((b for b in page.boxes[a:z] if b), None) for a, z in ranges[1:]]
                               if response["split"] == "items" else [])
    spans = []
    for (offset, _), result in zip(ranges, results):
        for label, entries in result.get("entities", {}).items():
            for entry in entries:
                start, end = entry["start"] + offset, entry["end"] + offset
                if page.text[start:end] != entry["text"]:
                    raise HTTPException(500, "A span's offsets do not match the page text")
                words = word_boxes(page, start, end)
                spans.append({
                    "label": label, "text": entry["text"], "start": start, "end": end,
                    "score": round(float(entry.get("confidence", 1.0)), 4),
                    "words": len(entry["text"].split()), "lines": span_lines(page, start, end),
                    # Where a fixed-width span head would have had to stop.
                    "legacy_cut": words[LEGACY_SPAN_WIDTH - 1] if len(words) > LEGACY_SPAN_WIDTH else None,
                })
    spans.sort(key=lambda s: (s["start"], -s["end"]))
    response["spans"] = spans
    return response


class ClassifyRequest(BaseModel):
    """The contract gliner_client.GlinerClient speaks: a text and a GLiNER classification schema."""
    text: str
    tasks: dict[str, Any] = Field(alias="schema")
    include_confidence: bool = True
    model: str = "multi"


@app.post("/classify")
def classify(request: ClassifyRequest):
    if request.model not in MODELS:
        raise HTTPException(422, f"Unknown model {request.model!r}; choose one of {sorted(MODELS)}")
    model = load(request.model)
    with locks[request.model], torch.inference_mode():
        result = model.classify_text(request.text, request.tasks, include_confidence=request.include_confidence)
    return {"result": result}


class OcrRequest(BaseModel):
    image: str


@app.post("/ocr")
def ocr(request: OcrRequest):
    """A scanned page as cells: each OCR line with its text, box (image fractions) and visual row."""
    data = base64.b64decode(request.image, validate=True)
    if len(data) > MAX_PDF_BYTES or not data.startswith(IMAGE_MAGIC):
        raise HTTPException(422, "Send a JPEG, PNG or WebP image under 25 MB")
    page, _, parse_ms = page_of(data, 1)
    return {"image": base64.b64encode(page.image).decode("ascii"), "image_size": list(page.image_size),
            "cells": page.cells, "ocr_ms": round(parse_ms, 1)}


if __name__ == "__main__":
    for name in PRELOAD:
        load(name)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("EXTRACT_PORT", "8030")))
