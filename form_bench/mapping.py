"""Map an EMR screen's fields to the patient record (schema.py) by looking at the screen, with Jev.

Nothing here knows an EMR. One question about a picture of the whole screen: which section of the record it
takes. Then, per field, a crop of the screen around the field with the field outlined (and, inside a table,
the table's header row above it), and what the page says about the field: which item of that section it is
about, which attribute of the item it holds, and in what unit. The per-field questions go together; code uses
the ones that apply. The mapping is made once per screen and checked by a person; entry then uses it as-is.
"""

import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from PIL import Image, ImageDraw

from forms import label, locate
from models import see
from schema import ATTRIBUTES, ITEMS, RECORD_UNITS, SECTIONS, UNITS, VALUE, WHOLE

SMALLEST, LARGEST = 280, 1568  # TypeSafe's limits on each side of an image
NONE = "none"
# A person checks a screen's mapping once; fields Jev maps below this are marked for that check. On the first
# three OpenEMR screens (223 fields) it marked both wrong mappings and one right one.
REVIEW_BELOW = 0.6


def fit(image: Image.Image) -> Image.Image:
    """Within TypeSafe's size limits: scaled down to the largest side, padded up to the smallest."""
    scale = min(1.0, LARGEST / max(image.size))
    if scale < 1:
        image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))))
    if min(image.size) < SMALLEST:
        canvas = Image.new("RGB", (max(image.width, SMALLEST), max(image.height, SMALLEST)), "white")
        canvas.paste(image, (0, 0))
        image = canvas
    return image


def png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def around(shot: Image.Image, box: dict, header: dict | None) -> Image.Image:
    """The screen around a field, wide enough to the left and above for its label, with the field outlined in
    red; a table's header row, if it is not already in view, is stacked above it."""
    x, y, w, h = box["x"], box["y"], box["width"], box["height"]
    left, top, right = max(0, int(x - 480)), max(0, int(y - 140)), min(shot.width, int(x + w + 160))
    region = shot.crop((left, top, right, min(shot.height, int(y + h + 70))))
    ImageDraw.Draw(region).rectangle((x - left - 3, y - top - 3, x - left + w + 3, y - top + h + 3),
                                     outline=(220, 0, 0), width=3)
    if header and header["y"] + header["height"] < top:
        strip = shot.crop((left, int(header["y"]), right, int(header["y"] + header["height"])))
        stacked = Image.new("RGB", (region.width, strip.height + 6 + region.height), (120, 120, 120))
        stacked.paste(strip, (0, 0))
        stacked.paste(region, (0, strip.height + 6))
        region = stacked
    return fit(region)


# The nearest row above a field's row, in its table, that has text in two or more cells and no controls.
HEADER = """e => { const row = e.closest('tr'), table = e.closest('table'); if (!row || !table) return null;
  const rows = [...table.rows];
  for (let k = rows.indexOf(row) - 1; k >= 0; k--) {
    const r = rows[k];
    if (!r.querySelector('input, select, textarea') && [...r.cells].filter(c => c.innerText.trim()).length > 1) {
      const b = r.getBoundingClientRect(); return {x: b.x, y: b.y, width: b.width, height: b.height}; } }
  return null; }"""


def section_of(shot: Image.Image, title: str) -> tuple[str, float]:
    answer = see({"screen": "screen-image", "title": title}, {"screen-image": png(fit(shot))},
                 {"section": {"type": "choice", "criteria": SECTIONS,
                              "instructions": "What is this EMR screen in `screen` for?"}})["section"]
    return answer["choice"], answer["probabilities"][answer["choice"]]


def field_questions(section: str) -> dict:
    """Which item, which attribute (when the section's items have several), and which unit (when the record
    converts between units for this section)."""
    about = "The field outlined in red in `crop`; `label` is what the page says about it."
    questions = {"item": {"type": "choice", "instructions": f"{about} Which of these is the field about?",
                          "criteria": ITEMS[section] | {NONE: "none of these: something this record does not hold"}}}
    if section in ATTRIBUTES:
        questions["attribute"] = {"type": "choice", "instructions": f"{about} What does the field hold about it?",
                                  "criteria": ATTRIBUTES[section] | {NONE: "none of these"}}
    if any(item in RECORD_UNITS for item in ITEMS[section]):
        questions["unit"] = {"type": "choice", "instructions": f"{about} In what unit does it take a value?",
                             "criteria": UNITS | {NONE: "no unit, or a unit not listed here"}}
    return questions


def map_field(shot: Image.Image, field: dict, box: dict | None, header: dict | None, section: str,
              questions: dict) -> dict:
    """{"item", "attribute", "unit", "confidence"}: item None when the field holds nothing the record fills."""
    if not box:
        return {"item": None, "attribute": None, "unit": None, "confidence": None, "why": "not visible"}
    state = {"crop": "field-crop", "label": label(field) or "(nothing)", "control": field["role"]}
    if field.get("options"):
        state["choices"] = field["options"][:12]
    answers = see(state, {"field-crop": png(around(shot, box, header))}, questions)

    def pick(qid):
        a = answers.get(qid)
        return (a["choice"], a["probabilities"][a["choice"]]) if a else (None, 1.0)

    (item, p_item), (attribute, p_attribute), (unit, p_unit) = pick("item"), pick("attribute"), pick("unit")
    if item in WHOLE.get(section, ()):
        attribute, p_attribute = VALUE, 1.0
    if item == NONE or attribute == NONE:
        return {"item": None, "attribute": None, "unit": None, "confidence": min(p_item, p_attribute)}
    unit_applies = item in RECORD_UNITS and (attribute or VALUE) == VALUE
    return {"item": item, "attribute": attribute or VALUE, "unit": unit if unit_applies and unit != NONE else None,
            "confidence": min(p_item, p_attribute, p_unit if unit_applies else 1.0)}


def key(mapped: dict) -> str | None:
    """A mapping as one string: item, item.attribute, or item.attribute@unit."""
    if not mapped.get("item"):
        return None
    text = mapped["item"] if mapped["attribute"] == VALUE and not mapped["unit"] else f"{mapped['item']}.{mapped['attribute']}"
    return f"{text}@{mapped['unit']}" if mapped["unit"] else text


@contextmanager
def whole(frame):
    """A picture of the whole screen and where its frame sits in it. A form in an inner frame (an EMR's tabbed
    shell) is shown whole by making the window tall enough, as a person would scroll, and put back after."""
    page = getattr(frame, "page", frame)
    if frame == page.main_frame or frame is page:
        yield Image.open(io.BytesIO(page.screenshot(full_page=True))).convert("RGB"), page.evaluate("[scrollX, scrollY]")
        return
    size = page.viewport_size
    frame.evaluate("window.scrollTo(0, 0)")
    top = frame.frame_element().bounding_box()["y"]
    height = frame.evaluate("document.documentElement.scrollHeight")
    page.set_viewport_size({"width": size["width"], "height": min(int(top + height + 40), 6000)})
    try:
        page.wait_for_timeout(400)
        yield Image.open(io.BytesIO(page.screenshot())).convert("RGB"), (0, 0)
    finally:
        page.set_viewport_size(size)


def map_screen(page, fields: list[dict], section: str | None = None, workers: int = 8) -> dict:
    """The screen's section (asked unless known) and, per field (same order), what it holds. page may be an
    inner frame holding the form."""
    inner = getattr(page, "page", None) and page != page.page.main_frame
    with whole(page) as (shot, (scroll_x, scroll_y)):
        at = page.frame_element().bounding_box() if inner else {"x": 0, "y": 0}  # where the frame's own boxes start
        shifted = lambda b: b and b | {"x": b["x"] + scroll_x, "y": b["y"] + scroll_y}  # noqa: E731
        placed = lambda b: b and b | {"x": b["x"] + at["x"] + scroll_x, "y": b["y"] + at["y"] + scroll_y}  # noqa: E731
        boxes, headers = [], []
        for field in fields:
            control = locate(page, field)
            visible = control.count() and control.is_visible()
            boxes.append(shifted(control.bounding_box(timeout=2000)) if visible else None)
            headers.append(placed(control.evaluate(HEADER)) if visible else None)
        title = page.title()
    section, confidence = (section, None) if section else section_of(shot, title)
    questions = field_questions(section)
    with ThreadPoolExecutor(workers) as pool:
        mapped = list(pool.map(lambda args: map_field(shot, *args, section, questions), zip(fields, boxes, headers)))
    for m in mapped:
        m["review"] = m["confidence"] is not None and m["confidence"] < REVIEW_BELOW
    return {"section": section, "section_confidence": confidence, "fields": mapped}
