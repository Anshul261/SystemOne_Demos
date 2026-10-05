"""Use JPT's typed questions through the same decision methods as GLiNER."""

from __future__ import annotations

from typing import Any

import httpx

from gliner_client import Decision, DecisionError, GlinerClient


class JptClient(GlinerClient):
    def __init__(self, base_url: str = "http://127.0.0.1:8085", timeout: float = 60):
        super().__init__(base_url, timeout)

    def classify_many(
        self, text: str, schema: dict[str, dict[str, Any]]
    ) -> dict[str, Decision | list[Decision]]:
        questions: dict[str, dict[str, Any]] = {}
        for head, spec in schema.items():
            labels = spec["labels"]
            descriptions = (
                labels if isinstance(labels, dict)
                else {label: label.replace("_", " ") for label in labels}
            )
            instruction = spec.get("prompt", head.replace("_", " ") + " for this request")
            if spec.get("multi_label"):
                for label, description in descriptions.items():
                    questions[f"{head}:{label}"] = {
                        "type": "noul",
                        "instructions": f"Does this text mention {description}?",
                    }
            elif list(descriptions) == ["yes", "no"]:
                questions[head] = {"type": "noul", "instructions": instruction}
            elif head == "priority" and isinstance(labels, list):
                questions[head] = {
                    "type": "score",
                    "instructions": instruction,
                    "criteria": list(descriptions.values()),
                }
            else:
                questions[head] = {
                    "type": "choice",
                    "instructions": instruction,
                    "criteria": descriptions,
                }

        response = httpx.post(
            f"{self.base_url}/v1/systemone",
            json={"state": text, "questions": questions},
            timeout=self.timeout,
        )
        response.raise_for_status()
        answers = response.json()["answers"]
        decisions: dict[str, Decision | list[Decision]] = {}
        for head, spec in schema.items():
            labels = list(spec["labels"])
            if spec.get("multi_label"):
                selected = []
                for label in labels:
                    probability = self._probability(answers[f"{head}:{label}"]["noul"])
                    if probability >= spec.get("cls_threshold", 0.5):
                        selected.append(Decision(label, probability))
                decisions[head] = selected
            elif list(labels) == ["yes", "no"]:
                probability = self._probability(answers[head]["noul"])
                decisions[head] = Decision(
                    "yes" if probability >= 0.5 else "no",
                    max(probability, 1 - probability),
                )
            else:
                answer = answers[head]
                probabilities = answer["probabilities"]
                if answer["type"] == "score":
                    index = max(probabilities, key=probabilities.__getitem__)
                    if not index.isdigit() or int(index) >= len(labels):
                        raise DecisionError(f"JPT returned an unknown {head}: {index}")
                    label = labels[int(index)]
                    probability = probabilities[index]
                else:
                    label = answer["choice"]
                    if label not in labels:
                        raise DecisionError(f"JPT returned an unknown {head}: {label}")
                    probability = probabilities[label]
                decisions[head] = Decision(label, self._probability(probability))
        return decisions

    @staticmethod
    def _probability(value: float) -> float:
        if not isinstance(value, (float, int)) or not 0 <= value <= 1:
            raise DecisionError(f"JPT returned an invalid probability: {value}")
        return float(value)


def make_client(model: str, gliner_url: str, jpt_url: str) -> GlinerClient:
    if model == "gliner":
        return GlinerClient(gliner_url)
    if model == "jpt":
        return JptClient(jpt_url)
    raise ValueError(f"Unknown model: {model}")
