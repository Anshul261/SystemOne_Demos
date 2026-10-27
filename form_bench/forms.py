"""EHR screens as data: read a screen's fields from Playwright's ARIA snapshot, find each control again, and
fill or read it back. No models here."""

import re

from playwright.sync_api import BrowserContext, Page

FIELD = re.compile(r"""^(\s*)- '?(textbox|spinbutton|combobox|searchbox)(?: "(.*?)")?""")
OPTION = re.compile(r'^\s*- option(?: "(.*?)")?')
ROW = re.compile(r'^(\s*)- row "(.*?)"')


def open_screen(context: BrowserContext, screen) -> Page:
    page = context.new_page()
    page.goto(screen.url)
    if screen.prepare:
        screen.prepare(page)
    return page


def read_form(page: Page) -> tuple[str, list[dict]]:
    """The form's ARIA snapshot and its fields, read once per screen: [{role, name, row, nth, options}].
    A control inside a table row is found by its row, so unlabelled cells (OpenEMR's vitals) work; nth counts
    the same role within that row. Elsewhere nth tells apart controls sharing a name, like two Middle Names."""
    snapshot = page.locator("body").aria_snapshot()
    fields: list[dict] = []
    row, row_indent = None, -1
    for line in snapshot.splitlines():
        if match := ROW.match(line):
            row, row_indent = match[2], len(match[1])
        elif match := FIELD.match(line):
            if len(match[1]) <= row_indent:
                row, row_indent = None, -1
            role, name = match[2], match[3] or ""
            nth = sum(f["role"] == role and f["row"] == row and (row or f["name"] == name) for f in fields)
            fields.append({"role": role, "name": name, "row": row, "nth": nth, "options": []})
        elif (match := OPTION.match(line)) and fields and fields[-1]["role"] == "combobox":
            fields[-1]["options"].append(match[1] or "")
    return snapshot, [f for f in fields if f["name"] or f["row"]]


def label(field: dict) -> str:
    return " - ".join(part for part in (field.get("row"), field.get("name")) if part)


def field_id(field: dict) -> tuple:
    return field["role"], field.get("row") or "", "" if field.get("row") else field.get("name", ""), field.get("nth", 0)


def locate(page: Page, field: dict):
    if field.get("row"):  # a row's name includes its selected options, so match its words in order, gaps allowed
        # Anchored at the start only: text typed into a row's box becomes part of the row's name.
        words = re.compile("^" + ".*".join(re.escape(w).replace("/", r"\/") for w in field["row"].split()))
        return page.get_by_role("row", name=words).get_by_role(field["role"]).nth(field.get("nth", 0))
    return page.get_by_role(field["role"], name=field["name"], exact=True).nth(field.get("nth", 0))


def shown(field: dict, value: str | int) -> str:
    """What a value looks like on the page: dropdown values are option indexes."""
    return field["options"][value] if field["role"] == "combobox" else value


def option_index(field: dict, text: str | None) -> int | None:
    matches = [k for k, option in enumerate(field["options"]) if k and text and option.casefold() == text.casefold()]
    return matches[0] if matches else None


def read_value(page: Page, field: dict, timeout: float = 5000) -> str:
    """What the control shows: the selected option's text, an input's value, or a widget's text."""
    return locate(page, field).evaluate(
        "e => e.tagName === 'SELECT' ? (e.value ? e.selectedOptions[0].text.trim() : '') :"
        " 'value' in e ? e.value : e.innerText.trim()", timeout=timeout)


def fill(page: Page, field: dict, value: str | int) -> bool:
    """Enter a value, read it back, and retry once; False if the page still shows something else."""
    control = locate(page, field)
    for _ in range(2):
        if field["role"] == "combobox":
            control.select_option(index=value)
            if control.evaluate("e => e.selectedIndex") == value:
                return True
        else:
            control.fill(value)
            if control.input_value().strip().casefold() == value.strip().casefold():
                return True
    return False
