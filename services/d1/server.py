"""Serve LiquidAI d1 decision models over HTTP for d1_client.py.

POST /system_one {"model": "omni" | "3b", "state": ..., "questions": {...}, "images": [base64 JPEG/PNG]}
returns the model's typed answers and the forward-pass time. Revisions are pinned because the models
load remote code.
"""

import base64
import io
import os
import threading
import time
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field
from transformers import AutoModel

MODELS = {
    "omni": ("LiquidAI/d1-omni-600M", torch.float16, "414f8d6438174f5b2133a9c21a478fc42625e308"),
    "3b": ("LiquidAI/d1-3B", torch.bfloat16, "da1fe36a861f24690f27f622dca1d8688503d113"),
}
DEVICE = os.getenv("D1_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
PRELOAD = [m for m in os.getenv("D1_MODELS", "omni,3b").split(",") if m]
MAX_IMAGE_BYTES = 12 * 1024 * 1024
loaded: dict[str, Any] = {}
locks = {name: threading.Lock() for name in MODELS}
app = FastAPI(title="SystemOne d1 service")


def load(name: str):
    repo, dtype, revision = MODELS[name]
    if DEVICE == "cpu":
        dtype = torch.float32
    with locks[name]:
        if name not in loaded:
            model = AutoModel.from_pretrained(repo, trust_remote_code=True, dtype=dtype, revision=revision)
            model = model.to(DEVICE).eval()
            # The first call with a new input shape is slow; warm the page and stamp-region shapes.
            for size in ((1240, 1754), (546, 544)):
                model.system_one(None, {"w": {"type": "noul", "instructions": "Is there a stamp?"}},
                                 images=[Image.new("RGB", size, "white")])
            loaded[name] = model
    return loaded[name]


class Request(BaseModel):
    model: str
    state: Any = None
    questions: dict[str, dict[str, Any]] = Field(min_length=1, max_length=32)
    images: list[str] = Field(default_factory=list, max_length=4)


@app.get("/health")
def health():
    return {"status": "ok", "device": DEVICE, "loaded": sorted(loaded), "available": sorted(MODELS)}


@app.post("/system_one")
def system_one(request: Request):
    if request.model not in MODELS:
        raise HTTPException(422, f"Unknown model {request.model!r}; choose one of {sorted(MODELS)}")
    images = []
    for item in request.images:
        raw = base64.b64decode(item, validate=True)
        if len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Each image must be under 12 MB")
        images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
    model = load(request.model)
    with locks[request.model], torch.inference_mode():
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        result = model.system_one(request.state, request.questions, images=images or None)
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        model_ms = (time.perf_counter() - started) * 1000
    return {**result, "model": MODELS[request.model][0], "model_ms": round(model_ms, 2)}


class BatchItem(BaseModel):
    state: Any = None
    questions: dict[str, dict[str, Any]] = Field(min_length=1, max_length=32)
    images: list[str] = Field(default_factory=list, max_length=4)


class BatchRequest(BaseModel):
    model: str
    requests: list[BatchItem] = Field(min_length=1, max_length=1024)


@app.post("/system_one_batch")
def system_one_batch(request: BatchRequest):
    """Many (state, questions, images) requests in one call; d1 packs single-question requests together."""
    if request.model not in MODELS:
        raise HTTPException(422, f"Unknown model {request.model!r}; choose one of {sorted(MODELS)}")
    items = []
    for item in request.requests:
        images = []
        for encoded in item.images:
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_IMAGE_BYTES:
                raise HTTPException(413, "Each image must be under 12 MB")
            images.append(Image.open(io.BytesIO(raw)).convert("RGB"))
        items.append((item.state, item.questions, images or None))
    model = load(request.model)
    with locks[request.model], torch.inference_mode():
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        results = model.system_one_batch(items)
        if DEVICE == "cuda":
            torch.cuda.synchronize()
        model_ms = (time.perf_counter() - started) * 1000
    return {"results": results, "model": MODELS[request.model][0], "model_ms": round(model_ms, 2)}


if __name__ == "__main__":
    for name in PRELOAD:
        load(name)
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("D1_PORT", "8020")))
