"""Workflow 2, entry: type approved values into an EHR screen, field by field, reading each one back.

Nothing is decided here: the values come from extraction.py, after review. on_step, when given, receives one
event per field with a screenshot of that field being entered, so a person can watch the entry happen.
"""

import base64

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page

from forms import fill, label, locate, read_value, same, shown

HIGHLIGHT = "e => { e.style.outline = '3px solid #e0a800'; e.style.outlineOffset = '2px' }"
DONE = {True: "e => { e.style.outline = '3px solid #13756B' }", False: "e => { e.style.outline = '3px solid #B0245C' }"}


def frame(page: Page) -> str:
    """A picture of the whole window, also when the form sits in one of its frames."""
    return base64.b64encode(getattr(page, "page", page).screenshot(type="jpeg", quality=70)).decode()


def enter(page: Page, fields: list[dict], values: dict[int, str | int], on_step=None) -> dict[int, str]:
    """Enter every value in the form's order, then read them all back: some pages change fields while their
    scripts run. Returns the fields the page would not keep, with the reason."""
    problems: dict[int, str] = {}
    for i in sorted(values):
        field, value = fields[i], values[i]
        control = locate(page, field)
        if on_step:
            control.scroll_into_view_if_needed()
            control.evaluate(HIGHLIGHT)
            on_step({"index": i, "label": label(field), "value": shown(field, value), "status": "typing",
                     "frame": frame(page)})
        try:
            kept = fill(page, field, value)
        except PlaywrightError:  # the control refused the value, such as text in a number field
            kept = False
        if not kept:
            problems[i] = "The page did not keep this value"
        if on_step:
            control.evaluate(DONE[kept])
            on_step({"index": i, "label": label(field), "value": shown(field, value),
                     "status": "kept" if kept else "refused", "frame": frame(page)})
    for i, value in values.items():
        if i not in problems and not same(read_value(page, fields[i]), shown(fields[i], value)):
            if not fill(page, fields[i], value):
                problems[i] = "The page changed this value after it was entered"
    return problems
