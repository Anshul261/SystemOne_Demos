"""HTTP client for the local extraction service (services/extract/server.py)."""

from __future__ import annotations

import base64
from typing import Any

import httpx


class ExtractClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8030", timeout: float = 180):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def extract(self, model: str, pdf: bytes, page: int, schema: dict[str, str], threshold: float = 0.5,
                segment: bool = True) -> dict[str, Any]:
        """One page: its rendered image (base64 JPEG), the spans, and one box per text line of each span.
        `segment` splits the page at numbered items first, so no span runs into the next item."""
        response = httpx.post(
            f"{self.base_url}/extract",
            json={"model": model, "pdf": base64.b64encode(pdf).decode("ascii"), "page": page,
                  "schema": schema, "threshold": threshold, "segment": segment},
            timeout=self.timeout,
        )
        if response.status_code == 422:
            detail = response.json().get("detail")
            raise ValueError(detail if isinstance(detail, str) else "The extraction service rejected the request.")
        response.raise_for_status()
        return response.json()
