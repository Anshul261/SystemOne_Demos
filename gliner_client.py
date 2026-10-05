from __future__ import annotations

from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class Decision:
    label: str
    confidence: float | None


class DecisionError(RuntimeError):
    pass


class GlinerClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8010", timeout: float = 60):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._field_cache: dict[tuple[str, tuple[str, ...]], Decision] = {}

    def classify(
        self,
        text: str,
        task: str,
        candidates: dict[str, str],
        prompt: str,
    ) -> Decision:
        response = httpx.post(
            f"{self.base_url}/classify",
            json={
                "text": text,
                "schema": {
                    task: {
                        "labels": candidates,
                        "prompt": prompt,
                    }
                },
                "include_confidence": True,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        value = response.json()["result"][task]
        if isinstance(value, str):
            decision = Decision(value, None)
        else:
            decision = Decision(value["label"], value.get("confidence"))
        if decision.label not in candidates:
            raise DecisionError(f"GLiNER returned an unknown {task}: {decision.label}")
        return decision

    def classify_field(self, label: str, candidates: dict[str, str]) -> Decision:
        cache_key = (label.casefold(), tuple(candidates))
        if cache_key not in self._field_cache:
            self._field_cache[cache_key] = self.classify(
                text=f"Form field label: {label}",
                task="source_field",
                candidates=candidates,
                prompt=(
                    f"Which source value should be entered into the form field "
                    f"labeled '{label}'?"
                ),
            )
        return self._field_cache[cache_key]
