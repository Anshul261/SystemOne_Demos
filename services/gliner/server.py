"""Serve GLiNER using the HTTP contract consumed by gliner_client.py."""

import os
import threading
from typing import Any

import torch
import uvicorn
from fastapi import FastAPI
from gliner2 import AutoExtractor
from pydantic import BaseModel, Field

MODEL_ID = os.getenv("GLINER_MODEL", "fastino/GLiNER2.5-Decide")
DEVICE = os.getenv("GLINER_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")
model = AutoExtractor.from_pretrained(MODEL_ID, map_location=DEVICE).eval()
lock = threading.Lock()
app = FastAPI(title="SystemOne GLiNER service")


class ClassifyRequest(BaseModel):
    text: str
    tasks: dict[str, Any] = Field(alias="schema")
    include_confidence: bool = True


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "device": DEVICE}


@app.post("/classify")
def classify(request: ClassifyRequest):
    with lock, torch.inference_mode():
        result = model.classify_text(
            request.text,
            request.tasks,
            include_confidence=request.include_confidence,
        )
    return {"result": result}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("GLINER_PORT", "8010")))
