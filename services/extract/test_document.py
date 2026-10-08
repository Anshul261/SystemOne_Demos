"""Run with: uv run --project services/extract --with pytest pytest services/extract/test_document.py"""

from pathlib import Path

from document import blocks, open_pdf, read_page, reading_order, span_lines

DOCS = Path(__file__).resolve().parents[2] / "fixtures" / "documents"


def page(name, number):
    return read_page(open_pdf((DOCS / name).read_bytes()), number - 1, scale=1.0)


def test_every_character_keeps_its_box_and_hyphenated_words_are_joined():
    p = page("sfip-dwelling-form-44cfr61-appA1-2024.pdf", 2)
    assert len(p.text) == len(p.boxes)
    assert all(b is not None for c, b in zip(p.text, p.boxes) if c != " ")
    assert "including any sunken room" in p.text  # "includ-\ning" in the PDF
    assert "￾" not in p.text


def test_two_columns_read_column_by_column():
    text = page("sfip-dwelling-form-44cfr61-appA1-2024.pdf", 2).text
    assert text.index("12. Deductible") < text.index("13. Described Location") < text.index("24. Post-FIRM")


def test_filled_form_values_return_to_their_table_cells():
    text = page("cms-abn-filled-en.pdf", 1).text
    assert text.index("Estimated cost") < text.index("Vitamin D, 25-hydroxy") < text.index("What to do now")
    assert text.index("$54.00") < text.index("What to do now")


def test_blocks_split_at_numbered_items_but_keep_lettered_sub_items():
    text = "4. Base Flood. A flood. 5. Basement. Any area. 6. Building a. A structure; b. A home. 7. Cancellation. The end."
    parts = [text[a:b].strip() for a, b in blocks(text)]
    assert parts == ["4. Base Flood. A flood.", "5. Basement. Any area.", "6. Building a. A structure; b. A home.",
                     "7. Cancellation. The end."]


def test_reading_order_prefers_a_real_gutter_over_line_gaps():
    left = [(0.1, y, 0.45, y + 0.01) for y in (0.1, 0.12, 0.14)]
    right = [(0.55, y, 0.9, y + 0.01) for y in (0.1, 0.12, 0.14)]
    header = [(0.1, 0.05, 0.9, 0.06)]
    boxes = header + right + left  # stream order scrambled
    assert reading_order(boxes, 612, 792) == [0, 4, 5, 6, 1, 2, 3]


def test_span_lines_merge_by_line_including_punctuation():
    p = page("sfip-dwelling-form-44cfr61-appA1-2024.pdf", 2)
    start = p.text.index("Any area of a building")
    end = p.text.index("on all sides.", start) + len("on all sides.")
    lines = span_lines(p, start, end)
    assert 3 <= len(lines) <= 5  # the definition wraps over a few lines, not one box per comma


def test_scanned_tables_read_row_by_row_with_cells_kept_apart():
    from document import assemble, raster_rows

    def line(text, x0, y0):
        step = 0.01
        return [(c, (x0 + i * step, y0, x0 + (i + 1) * step, y0 + 0.015)) for i, c in enumerate(text)]

    # A slightly skewed two-row table, given in column order as OCR often returns it.
    lines = [line("Haemoglobin", 0.1, 0.30), line("RBC Count", 0.1, 0.35),
             line("12.5", 0.4, 0.302), line("4.62", 0.4, 0.352), line("11.5-16.0", 0.7, 0.305)]
    rows = raster_rows([(b[0][1][0], b[0][1][1], b[-1][1][2], b[-1][1][3]) for b in lines])
    assert rows == [[0, 2, 4], [1, 3]]
    ordered = [i for row in rows for i in row]
    text, boxes, starts = assemble(lines, ordered, {1: " | ", 2: " | ", 4: " | "})
    assert text == "Haemoglobin | 12.5 | 11.5-16.0 RBC Count | 4.62"
    assert len(text) == len(boxes) and boxes[text.index("|")] is None
    assert [text[s:s + 3] for s in starts] == ["Hae", "12.", "11.", "RBC", "4.6"]
