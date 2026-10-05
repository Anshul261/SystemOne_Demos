from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
        return self.classify_many(
            text, {task: {"labels": candidates, "prompt": prompt}}
        )[task]

    def classify_many(
        self, text: str, schema: dict[str, dict[str, Any]]
    ) -> dict[str, Decision | list[Decision]]:
        response = httpx.post(
            f"{self.base_url}/classify",
            json={
                "text": text,
                "schema": schema,
                "include_confidence": True,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json()["result"]
        decisions = {}
        for task, spec in schema.items():
            labels = spec["labels"]
            value = result[task]
            if spec.get("multi_label"):
                if not isinstance(value, list):
                    raise DecisionError(f"GLiNER returned an invalid {task}: {value}")
                decisions[task] = [self._decision(item, labels, task) for item in value]
            else:
                if isinstance(value, list):
                    raise DecisionError(f"GLiNER returned an invalid {task}: {value}")
                decisions[task] = self._decision(value, labels, task)
        return decisions

    @staticmethod
    def _decision(value: Any, labels: dict[str, str] | list[str], task: str) -> Decision:
        if isinstance(value, str):
            decision = Decision(value, None)
        elif isinstance(value, dict) and isinstance(value.get("label"), str):
            decision = Decision(value["label"], value.get("confidence"))
        else:
            raise DecisionError(f"GLiNER returned an invalid {task}: {value}")
        if decision.label not in labels:
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
