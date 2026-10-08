"""Real PDF pages to model text and back to boxes.

pdfium gives every character on a page with its box. The model needs clean text, so line-break
hyphens are joined ("pri-\\nvate" -> "private") and line breaks become spaces, while a map keeps
each model character's source character. A span's characters then map back to boxes, merged into
one rectangle per text line so a 60-word clause is drawn as the lines it actually covers.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pypdfium2 as pdfium

SOFT_HYPHEN = "￾"  # pdfium's marker for a hyphen that breaks a word across lines
# A block starts at a numbered item ("12. Term") or a lettered or roman section ("B. Text", "IV. EXCLUSIONS")
# right after a sentence ends. Lowercase sub-items ("a. This form;") stay inside their block.
BLOCK_START = re.compile(r"(?<=[.;:\)])\s+(?=(?:\d{1,2}|[A-Z]|[IVX]{1,4})\.\s+[A-Z“\"(])")
TEXT_MARGIN = 0.025  # page fraction kept around the text when cropping blank margins


@dataclass
class Page:
    number: int
    width: float  # PDF points
    height: float
    text: str  # what the model reads
    boxes: list[tuple[float, float, float, float] | None]  # per model character, image fractions (x0, y0, x1, y1)
    image: bytes = field(repr=False, default=b"")  # rendered JPEG
    image_size: tuple[int, int] = (0, 0)
    rows: list[tuple[int, int]] | None = None  # OCR pages: character range of each visual row
    cells: list[dict] | None = None  # OCR pages: each OCR line with its text, box and row, in reading order


def open_pdf(data: bytes) -> pdfium.PdfDocument:
    return pdfium.PdfDocument(io.BytesIO(data))


def union(boxes) -> tuple[float, float, float, float]:
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def reading_order(boxes: list[tuple[float, float, float, float]], width: float, height: float) -> list[int]:
    """Line indices in reading order by recursive XY-cut: split at the widest whitespace gap, horizontal
    or vertical, measured in points. Two-column pages read column by column; a form's fields filled in
    after the fact land back in their table cells instead of at the end of the content stream."""

    def widest_gap(idx: list[int], lo: int, hi: int, scale: float) -> tuple[float, float]:
        spans = sorted((boxes[i][lo], boxes[i][hi]) for i in idx)
        best, cut, reach = 0.0, 0.0, spans[0][1]
        for start, end in spans[1:]:
            if (start - reach) * scale > best:
                best, cut = (start - reach) * scale, (start + reach) / 2
            reach = max(reach, end)
        return best, cut

    def cut(idx: list[int]) -> list[int]:
        if len(idx) <= 1:
            return idx
        y_gap, y_cut = widest_gap(idx, 1, 3, height)
        x_gap, x_cut = widest_gap(idx, 0, 2, width)
        # Columns need a real gutter; any positive gap between rows will do.
        if x_gap >= 6 and x_gap > y_gap:
            left = [i for i in idx if boxes[i][2] <= x_cut]
            return cut(left) + cut([i for i in idx if boxes[i][2] > x_cut])
        if y_gap > 0:
            top = [i for i in idx if boxes[i][3] <= y_cut]
            return cut(top) + cut([i for i in idx if boxes[i][3] > y_cut])
        return sorted(idx, key=lambda i: (boxes[i][1], boxes[i][0]))

    return cut(list(range(len(boxes))))


def read_page(pdf: pdfium.PdfDocument, index: int, scale: float = 3.0) -> Page:
    page = pdf[index]
    width, height = page.get_size()
    textpage = page.get_textpage()
    n = textpage.count_chars()
    raw = textpage.get_text_range(0, n) if n else ""
    # get_text_range returns one character per pdfium index for this API version; guard the assumption.
    if len(raw) != n:
        raw = "".join(textpage.get_text_range(i, 1) or " " for i in range(n))

    def box(i: int):
        left, bottom, right, top = textpage.get_charbox(i)
        if right <= left or top <= bottom:
            return None
        return (left / width, 1 - top / height, right / width, 1 - bottom / height)

    # Lines as the content stream gives them. pdfium ends most with "\r\n", but text appended later
    # (flattened form fields) can continue a line from elsewhere on the page, so a character that
    # jumps to another vertical position also starts a new line.
    lines: list[list[tuple[str, tuple | None]]] = [[]]
    last = None
    for i, ch in enumerate(raw):
        if ch in "\r\n":
            if lines[-1]:
                lines.append([])
            last = None
            continue
        b = box(i)
        if b and last and min(b[3], last[3]) - max(b[1], last[1]) < 0.3 * min(b[3] - b[1], last[3] - last[1]):
            lines.append([])
        lines[-1].append((ch, b))
        last = b or last
    lines = [line for line in lines if any(b for c, b in line if not c.isspace())]

    ordered = reading_order([union(b for _, b in line if b) for line in lines], width, height)
    text, boxes, _ = assemble(lines, ordered)
    image = page.render(scale=scale).to_pil().convert("RGB")
    return finish(index + 1, width, height, text, boxes, image, crop=True)


def assemble(lines: list[list[tuple[str, tuple | None]]], ordered: list[int],
             separators: dict[int, str] | None = None) -> tuple[str, list, list[int]]:
    """Ordered lines to one string and a box per character: spaces between lines (or `separators[k]`
    before the k-th ordered line), words broken by a line-end hyphen joined back together. Also returns
    where each ordered line starts in the text."""
    text: list[str] = []
    boxes: list[tuple[float, float, float, float] | None] = []
    starts: list[int] = []
    join_next = False
    for k, li in enumerate(ordered):
        chars = lines[li]
        while chars and chars[-1][0].isspace():
            chars = chars[:-1]
        following = lines[ordered[k + 1]] if k + 1 < len(ordered) else []
        broken = bool(chars) and (chars[-1][0] == SOFT_HYPHEN or (
            chars[-1][0] == "-" and len(chars) > 1 and chars[-2][0].isalpha()
            and following and following[0][0].islower()))
        if broken:  # a word broken across lines: drop the hyphen and keep the word whole
            chars = chars[:-1]
        if text and text[-1] != " " and not join_next:
            for ch in (separators or {}).get(k, " "):
                text.append(ch)
                boxes.append(None)
        starts.append(len(text))
        for ch, b in chars:
            if ch.isspace():
                if text and text[-1] != " ":
                    text.append(" ")
                    boxes.append(None)
            elif ch != SOFT_HYPHEN:
                text.append(ch)
                boxes.append(b)
        join_next = broken
    return "".join(text), boxes, starts


def finish(number: int, width: float, height: float, text: str, boxes: list, image, crop: bool) -> Page:
    """Strip the text, optionally crop blank margins (boxes move into the cropped frame), encode the image."""
    lead = len(text) - len(text.lstrip())
    text_boxes = boxes[lead: lead + len(text.strip())]
    real = [b for b in text_boxes if b]
    if crop and real:
        cx0 = max(0.0, min(b[0] for b in real) - TEXT_MARGIN)
        cy0 = max(0.0, min(b[1] for b in real) - TEXT_MARGIN)
        cx1 = min(1.0, max(b[2] for b in real) + TEXT_MARGIN)
        cy1 = min(1.0, max(b[3] for b in real) + TEXT_MARGIN)
        w, h = image.size
        image = image.crop((int(cx0 * w), int(cy0 * h), int(cx1 * w), int(cy1 * h)))
        sx, sy = cx1 - cx0, cy1 - cy0
        text_boxes = [None if b is None else ((b[0] - cx0) / sx, (b[1] - cy0) / sy, (b[2] - cx0) / sx, (b[3] - cy0) / sy)
                      for b in text_boxes]
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return Page(number, width, height, text.strip(), text_boxes, buffer.getvalue(), image.size)


def raster_rows(boxes: list[tuple[float, float, float, float]]) -> list[list[int]]:
    """Lines grouped into top-to-bottom rows, left to right within a row. For scanned tables this keeps a test, its result,
    unit and range together, where XY-cut would read whole columns one after another."""
    order = sorted(range(len(boxes)), key=lambda i: (boxes[i][1] + boxes[i][3]) / 2)
    rows: list[list[int]] = []
    for i in order:
        mid = (boxes[i][1] + boxes[i][3]) / 2
        if rows:
            row = rows[-1]
            row_mid = sum((boxes[j][1] + boxes[j][3]) / 2 for j in row) / len(row)
            height = sum(boxes[j][3] - boxes[j][1] for j in row) / len(row)
            if abs(mid - row_mid) < 0.5 * height:
                row.append(i)
                continue
        rows.append([i])
    return [sorted(row, key=lambda j: boxes[j][0]) for row in rows]


_ocr = None


def read_image(data: bytes) -> Page:
    """A scanned or photographed page: docTR finds and reads each word; a word's characters share its
    width evenly, which is close enough to outline a span."""
    global _ocr
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    image.load()
    image = image.convert("RGB")
    if _ocr is None:
        import torch
        from doctr.models import ocr_predictor

        _ocr = ocr_predictor(det_arch="db_resnet50", reco_arch="crnn_vgg16_bn", pretrained=True,
                             assume_straight_pages=True)
        if torch.cuda.is_available():
            _ocr = _ocr.cuda()
        _ocr.eval()
    import numpy as np

    result = _ocr([np.asarray(image)])
    lines: list[list[tuple[str, tuple | None]]] = []
    for block in result.pages[0].blocks:
        for line in block.lines:
            chars: list[tuple[str, tuple | None]] = []
            for word in line.words:
                (x0, y0), (x1, y1) = word.geometry
                value = word.value
                if chars:
                    chars.append((" ", None))
                step = (x1 - x0) / max(1, len(value))
                chars += [(ch, (x0 + k * step, y0, x0 + (k + 1) * step, y1)) for k, ch in enumerate(value)]
            if chars:
                lines.append(chars)
    rows = raster_rows([union(b for _, b in line if b) for line in lines])
    ordered = [i for row in rows for i in row]
    # Cells that share a row are separated by " | ", so a test name and its result read as two cells.
    first_of_row, k = set(), 0
    for row in rows:
        first_of_row.add(k)
        k += len(row)
    separators = {k: " | " for k in range(1, len(ordered)) if k not in first_of_row}
    text, boxes, starts = assemble(lines, ordered, separators)
    bounds = [starts[k] for k in sorted(first_of_row)] + [len(text) + 1]
    ranges = [(a, b - 1) for a, b in zip(bounds, bounds[1:])]
    page = finish(1, float(image.width), float(image.height), text, boxes, image, crop=False)
    page.rows = ranges
    page.cells = [{"text": "".join(c for c, _ in lines[i]).strip(), "box": [round(v, 5) for v in union(b for _, b in lines[i] if b)],
                   "row": r, "start": starts[k]}
                  for k, (r, i) in enumerate((r, i) for r, row in enumerate(rows) for i in row)]
    return page


def blocks(text: str) -> list[tuple[int, int]]:
    """Character ranges of the page's numbered items and sections, so a span cannot run into the next one."""
    cuts = [0] + [match.end() for match in BLOCK_START.finditer(text)] + [len(text)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]


def span_lines(page: Page, start: int, end: int) -> list[list[float]]:
    """One rectangle per text line the span covers, in image fractions."""
    lines: list[list[float]] = []
    for b in page.boxes[start:end]:
        if b is None:
            continue
        x0, y0, x1, y1 = b
        if lines:
            last = lines[-1]
            # Same line when the boxes overlap vertically; a comma or quote is short but sits inside the line.
            overlap = min(y1, last[3]) - max(y0, last[1])
            same_line = overlap > 0.3 * min(y1 - y0, last[3] - last[1]) and x0 >= last[0] - 0.01
            if same_line:
                last[0], last[1], last[2], last[3] = min(last[0], x0), min(last[1], y0), max(last[2], x1), max(last[3], y1)
                continue
        lines.append([x0, y0, x1, y1])
    return [[round(v, 5) for v in line] for line in lines]


def word_boxes(page: Page, start: int, end: int) -> list[list[float]]:
    """A box per word inside the span, for marking where a fixed-width span head would have stopped."""
    words, current = [], None
    for b in page.boxes[start:end] + [None]:
        if b is None:
            if current:
                words.append([round(v, 5) for v in current])
            current = None
            continue
        current = list(b) if current is None else [min(current[0], b[0]), min(current[1], b[1]),
                                                   max(current[2], b[2]), max(current[3], b[3])]
    return words
