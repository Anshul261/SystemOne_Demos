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
