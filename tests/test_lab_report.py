import pytest
from PIL import Image

import lab_report as L


@pytest.mark.parametrize("text, expected", [
    ("11.5-16.0", (11.5, 16.0)),
    ("Female 1 4.5-5.1", (4.5, 5.1)),            # a qualifier with OCR noise before the range
    ("cells/cu mm 4000-11000", (4000, 11000)),   # unit and range in one cell
    (">14Y:40-75", (40, 75)),
    ("3.5 5.1", (3.5, 5.1)),                     # OCR dropped the dash
    ("0.60 -1 1.30", (0.6, 1.3)),                # OCR read the dash as a 1
    ("40-1 150", (40, 150)),
    ("mg/dL", None),
])
def test_ranges_survive_ocr_damage(text, expected):
    parsed = L.parse_range(text)
    assert (parsed[:2] if parsed else None) == expected


def test_numbers_and_lab_markers():
    assert L.number("27,0") == 27.0 and L.number("10,300") == 10300
    assert L.NUMBER.match("19.3 *").groups() == ("19.3", "*")
    assert L.NUMBER.match("7.2 H").group(2) == "H"
    assert not L.NUMBER.match("NORMOCYTIC")


def test_alignment_keeps_order_and_finds_a_one_line_offset():
    tests = [0.30, 0.33, 0.36, 0.39]
    slots = [0.315, 0.345, 0.405]  # ranges printed half a line low; the third test's range is missing
    assert L.align(tests, slots) == {0: 0, 1: 1, 3: 2}


def cell(text, x0, y0, x1=None, h=0.012):
    return {"text": text, "box": [x0, y0, x1 if x1 is not None else x0 + 0.01 * max(3, len(text)), y0 + h], "row": 0}


def stub_decide(model, requests):
    """Answers the reader's questions from the text alone, like a perfect d1."""
    answers = []
    for state, questions, _ in requests:
        (name, q), = questions.items()
        text = (state or "").split('"')[1] if state and '"' in state else ""
        if name == "column":
            choice = {"TEST": "test", "RESULT": "result", "UNITS": "unit", "RANGE": "range"}.get(text, "none")
        elif name == "role":
            choice = ("method" if text.startswith("(") else "section" if text.isupper() and len(text) > 4
                      else "test_name")
        elif name == "unit":
            choice = {"g/dl": "g/dL", "%": "%"}.get(text.lower(), "other")
        else:  # the lab's shaded box: none on this page
            choice = "plain"
        answers.append({name: {"type": "choice", "choice": choice, "confidence": 0.9,
                               "probabilities": {k: (0.9 if k == choice else 0.01) for k in q["criteria"]}}})
    return answers, 1.0


def test_reader_pairs_offset_ranges_and_skips_a_heading_read_as_a_test():
    cells = [cell("TEST", 0.1, 0.10), cell("RESULT", 0.35, 0.10), cell("UNITS", 0.6, 0.10), cell("RANGE", 0.75, 0.10),
             cell("BLOOD COUNT", 0.1, 0.14),
             cell("Haemoglobin", 0.1, 0.17), cell("12.5", 0.35, 0.17), cell("(Method: photometry)", 0.1, 0.19),
             cell("g/dl", 0.6, 0.19), cell("11.5-16.0", 0.75, 0.19),          # a line below its test
             cell("Hct", 0.1, 0.22), cell("37.3", 0.35, 0.22), cell("%", 0.6, 0.24), cell("41.5 50.4", 0.75, 0.24),
             cell("ESR", 0.1, 0.27),                                            # a heading the model calls a test
             cell("ESR 1 hr", 0.1, 0.30), cell("32 *", 0.35, 0.30), cell("mm", 0.6, 0.32), cell("0-15", 0.75, 0.32)]
    result = L.read_report(cells, Image.new("RGB", (800, 1000), "white"), stub_decide, "3b")
    records = {r["test"]: r for r in result["records"]}
    assert set(records) == {"Haemoglobin", "Hct", "ESR 1 hr"}
    assert (records["Haemoglobin"]["value"], records["Haemoglobin"]["unit"], records["Haemoglobin"]["low"]) == (12.5, "g/dL", 11.5)
    assert (records["Hct"]["low"], records["Hct"]["high"], records["Hct"]["flag"]) == (41.5, 50.4, "low")
    esr = records["ESR 1 hr"]
    assert (esr["value"], esr["unit"], esr["high"], esr["flag"], esr["printed_flag"]) == (32, "mm", 15, "high", "*")
    assert esr["lab_marked"] and esr["agrees_with_lab"]


def test_score_compares_every_field_with_the_answer_key():
    records = [{"test": "Haemoglobin", "value": 12.5, "unit": "g/dL", "low": 11.5, "high": 16.0, "flag": "normal",
                "printed_box": False}]
    truth = {"tests": [{"test": "Haemoglobin", "value": 12.5, "unit": "gms%", "low": 11.5, "high": 16.0, "flag": "normal"}],
             "findings": []}
    result = L.score(records, truth)
    assert result["totals"]["value"] == [1, 1] and result["totals"]["unit"] == [1, 1]
    assert records[0]["check"]["range"] is True
