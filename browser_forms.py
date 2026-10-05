from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page

from gliner_client import DecisionError, GlinerClient


@dataclass(frozen=True)
class VisibleField:
    index: int
    label: str
    tag: str
    input_type: str
    options: tuple[str, ...]


class AuditLog:
    def __init__(
        self,
        demo: str,
        output_dir: str = "artifacts",
        page: Page | None = None,
        on_event: Callable[[dict[str, Any], bytes | None], None] | None = None,
        screenshot_events: set[str] | None = None,
    ):
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        self.path = Path(output_dir) / f"{demo}-{stamp}.jsonl"
        self.page = page
        self.on_event = on_event
        self.screenshot_events = screenshot_events

    def write(self, event: str, **details: Any) -> None:
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **details,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")
        if self.on_event:
            screenshot = (
                self.page.screenshot(type="jpeg", quality=72)
                if self.page
                and (self.screenshot_events is None or event in self.screenshot_events)
                else None
            )
            self.on_event(record, screenshot)


def form_controls(root: Locator) -> Locator:
    return root.locator(
        "input:visible:not([type=hidden]):not([type=submit]):not([type=button]), "
        "select:visible, textarea:visible"
    )


def visible_fields(root: Locator) -> list[VisibleField]:
    controls = form_controls(root)
    fields = []
    for index in range(controls.count()):
        control = controls.nth(index)
        data = control.evaluate(
            """element => ({
                label: element.labels?.[0]?.innerText?.trim() ||
                    element.previousElementSibling?.innerText?.trim() ||
                    element.parentElement?.querySelector('label')?.innerText?.trim() ||
                    element.closest('tr')?.querySelector('td:first-child')?.innerText?.trim() ||
                    element.getAttribute('aria-label') ||
                    element.getAttribute('placeholder') || '',
                tag: element.tagName.toLowerCase(),
                inputType: element.getAttribute('type') || '',
                options: element.tagName === 'SELECT'
                    ? Array.from(element.options).map(option => option.text.trim()).filter(Boolean)
                    : []
            })"""
        )
        if data["label"]:
            fields.append(
                VisibleField(
                    index=index,
                    label=data["label"].rstrip(":"),
                    tag=data["tag"],
                    input_type=data["inputType"],
                    options=tuple(data["options"]),
                )
            )
    return fields


def fill_classified_form(
    root: Locator,
    values: dict[str, str],
    descriptions: dict[str, str],
    gliner: GlinerClient,
    audit: AuditLog,
    *,
    minimum_confidence: float = 0.15,
    ignored_labels: set[str] | None = None,
) -> None:
    candidates = {key: descriptions[key] for key in values}
    ignored = {label.casefold() for label in (ignored_labels or set())}

    for field in visible_fields(root):
        if field.label.casefold() in ignored:
            audit.write("field_ignored", field=asdict(field))
            continue
        decision = gliner.classify_field(field.label, candidates)
        if decision.confidence is not None and decision.confidence < minimum_confidence:
            audit.write(
                "field_classification",
                field=asdict(field),
                decision=decision.label,
                confidence=decision.confidence,
                candidates=list(candidates),
                executed=False,
            )
            raise DecisionError(
                f"Low-confidence mapping for '{field.label}': "
                f"{decision.label} at {decision.confidence:.3f}"
            )

        value = str(values[decision.label])
        control = form_controls(root).nth(field.index)
        if field.tag == "select":
            options = control.locator("option").evaluate_all(
                "options => options.map(option => ({label: option.text.trim(), value: option.value}))"
            )
            if not any(
                option["label"] == value or option["value"] == value.casefold()
                for option in options
            ):
                audit.write(
                    "field_classification",
                    field=asdict(field),
                    decision=decision.label,
                    confidence=decision.confidence,
                    candidates=list(candidates),
                    executed=False,
                )
                raise DecisionError(
                    f"{field.label}: selected {decision.label} produces {value!r}, "
                    "which is not an available option"
                )
            try:
                control.select_option(label=value)
            except PlaywrightError:
                control.select_option(value=value.casefold())
        else:
            control.fill(value)
        audit.write(
            "field_classification",
            field=asdict(field),
            decision=decision.label,
            confidence=decision.confidence,
            candidates=list(candidates),
            executed=True,
        )


def page_state(page: Page, limit: int = 4000) -> str:
    text = page.locator("body").inner_text()
    return " ".join(text.split())[:limit]
