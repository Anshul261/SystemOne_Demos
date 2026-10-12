"""HTTP client for the local d1 service (services/d1/server.py)."""

from __future__ import annotations

import base64
import io
from typing import Any

import httpx
from PIL import Image

from gliner_client import DecisionError


class D1Client:
    def __init__(self, base_url: str = "http://127.0.0.1:8020", timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def system_one(
        self,
        model: str,
        questions: dict[str, dict[str, Any]],
        images: list[Image.Image],
        state: Any = None,
    ) -> tuple[dict[str, dict[str, Any]], float]:
        """Named questions over images (and an optional state): the answers and the forward-pass time in ms."""
        response = httpx.post(
            f"{self.base_url}/system_one",
            json={
                "model": model,
                "state": state,
                "questions": questions,
                "images": [self._encode(image) for image in images],
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        answers = body.get("answers", {})
        missing = set(questions) - set(answers)
        if missing:
            raise DecisionError(f"d1 returned no answer for {sorted(missing)}")
        return answers, float(body["model_ms"])

    def system_one_batch(
        self, model: str, requests: list[tuple[Any, dict[str, dict[str, Any]], list[Image.Image]]]
    ) -> tuple[list[dict[str, dict[str, Any]]], float]:
        """Many (state, questions, images) requests in one call: each request's answers, and the model time."""
        response = httpx.post(
            f"{self.base_url}/system_one_batch",
            json={"model": model, "requests": [
                {"state": state, "questions": questions, "images": [self._encode(i) for i in images]}
                for state, questions, images in requests]},
            timeout=self.timeout,
        )
        response.raise_for_status()
        body = response.json()
        answers = [result["answers"] for result in body["results"]]
        for (_, questions, _), answer in zip(requests, answers):
            if set(questions) - set(answer):
                raise DecisionError(f"d1 returned no answer for {sorted(set(questions) - set(answer))}")
        return answers, float(body["model_ms"])

    @staticmethod
    def _encode(image: Image.Image) -> str:
        # PNG keeps the pixels the model was calibrated on; a second JPEG pass would shift them.
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")
