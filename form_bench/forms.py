"""EHR screens as data: read a screen's fields from Playwright's ARIA snapshot, find each control again, and
fill or read it back. No models here."""

import re

from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

FIELD = re.compile(r"""^(\s*)- '?(textbox|spinbutton|combobox|searchbox)(?: "(.*?)")?""")
OPTION = re.compile(r'^\s*- option(?: "(.*?)")?')
ROW = re.compile(r'^(\s*)- row "(.*?)"')
CELL = re.compile(r'^(\s*)- (?:cell|gridcell|columnheader|rowheader)(?: "(.*?)")?')


def open_screen(context: BrowserContext, screen) -> Page:
    page = context.new_page()
    page.goto(screen.url)
    if screen.prepare:
        screen.prepare(page)
    return page


def read_form(page: Page) -> tuple[str, list[dict]]:
    """The form's ARIA snapshot and its fields, read once per screen: [{role, name, row, nth, context, options}].

    A control inside a table row is found again by its row (row, nth), so unlabelled cells such as OpenEMR's
    vitals work. Its label comes only from what describes it: the row's cells to its left and its column's
    header ("Value", "Abn"). Cells further right, such as an earlier reading, are left out. Outside tables,
    nth tells apart controls sharing a name, like two Middle Names. A row with fewer cells than the header
    (its first cells are shared with the row above, as in OpenEMR's lab results) is aligned to the header from
    the right."""
    snapshot = page.locator("body").aria_snapshot()
    fields: list[dict] = []
    row, row_indent, cells, headers, row_has_field = None, -1, [], [], False
    in_row: list[tuple[dict, int]] = []  # this row's fields and the cell each sits in

    def close_row():
        shift = max(len(headers) - len(cells), 0)
        for field, column in in_row:
            header = headers[column + shift] if 0 <= column + shift < len(headers) else ""
            field["context"] = " ".join(c for c in cells[:column] if c) + (f" ({header})" if header else "")
        in_row.clear()

    for line in snapshot.splitlines():
        if match := ROW.match(line):
            close_row()
            if row and not row_has_field and cells and all(cells):
                headers = cells  # a row of named cells and no controls heads the columns below it
            row, row_indent, cells, row_has_field = match[2], len(match[1]), [], False
        elif (match := CELL.match(line)) and row and len(match[1]) == row_indent + 2:
            cells.append(match[2] or "")
        elif match := FIELD.match(line):
            if len(match[1]) <= row_indent:
                close_row()
                row, row_indent, cells, headers = None, -1, [], []
            role, name = match[2], match[3] or ""
            nth = sum(f["role"] == role and f["row"] == row and (bool(row) or f["name"] == name) for f in fields)
            fields.append({"role": role, "name": name, "row": row, "nth": nth, "context": "", "options": []})
            if row:
                row_has_field = True
                in_row.append((fields[-1], len(cells) - 1))
        elif (match := OPTION.match(line)) and fields and fields[-1]["role"] == "combobox":
            fields[-1]["options"].append(match[1] or "")
    close_row()
    return snapshot, [f for f in fields if f["name"] or f["row"]]


def label(field: dict) -> str:
    """How a field is described to the models and the reviewer."""
    return " - ".join(part for part in (field.get("context") or field.get("row"), field.get("name")) if part)


def field_id(field: dict) -> tuple:
    return field["role"], field.get("row") or "", "" if field.get("row") else field.get("name", ""), field.get("nth", 0)


def row_pattern(row: str) -> re.Pattern:
    """A row's name grows with what it shows (selected options, typed text, earlier readings), so match its
    words in order with gaps allowed, anchored at the start only."""
    return re.compile("^" + ".*".join(re.escape(w).replace("/", r"\/") for w in row.split()))


def same_control(a: dict, b: dict) -> bool:
    """Whether two field descriptions, read at different times, are the same control."""
    if a["role"] != b["role"] or a.get("nth", 0) != b.get("nth", 0) or bool(a.get("row")) != bool(b.get("row")):
        return False
    if a.get("row"):
        return bool(row_pattern(a["row"]).match(b["row"]) or row_pattern(b["row"]).match(a["row"]))
    return a["name"] == b["name"]


def pin(page: Page, fields: list[dict]) -> None:
    """Mark each control on the page once, right after the screen is read, so entry finds it again even when
    typing changes what describes it (a table row's name includes its inputs' values)."""
    for k, field in enumerate(fields):
        try:
            locate(page, field).evaluate("(e, k) => e.setAttribute('data-entry-pin', k)", k, timeout=2000)
            field["pin"] = k
        except PlaywrightError:  # not found as read: left to the usual lookup
            pass


def locate(page: Page, field: dict):
    if "pin" in field:
        return page.locator(f'[data-entry-pin="{field["pin"]}"]')
    if field.get("row"):
        return page.get_by_role("row", name=row_pattern(field["row"])).get_by_role(field["role"]).nth(field.get("nth", 0))
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
