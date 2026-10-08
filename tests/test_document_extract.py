import base64
import json
from functools import partial

import pytest

import browser_forms
import document_extract


def test_schema_labels_must_be_safe_and_described():
    assert document_extract.validate_schema({"exclusion": " a clause "}) == {"exclusion": "a clause"}
    for bad in ({}, {"Bad Label": "x"}, {"ok": ""}, {f"l{i}": "d" for i in range(13)}):
        with pytest.raises(ValueError):
            document_extract.validate_schema(bad)


def test_uploads_must_be_pdfs():
    pdf = "data:application/pdf;base64," + base64.b64encode(b"%PDF-1.7 test").decode()
    assert document_extract.decode_pdf(pdf).startswith(b"%PDF")
    with pytest.raises(ValueError):
        document_extract.decode_pdf("data:application/pdf;base64," + base64.b64encode(b"not a pdf").decode())
    with pytest.raises(ValueError):
        document_extract.decode_pdf("data:image/png;base64,AAAA")


def test_samples_ship_their_pdfs_and_paraphrased_schemas():
    for doc in document_extract.load_samples():
        pdf = (document_extract.DOCUMENTS / doc["file"]).read_bytes()
        assert pdf.startswith(b"%PDF") and doc["source"].startswith("https://")
        document_extract.validate_schema(doc["schema"])


def test_every_page_streams_its_image_and_spans_and_the_summary_finds_the_longest(monkeypatch, tmp_path):
    monkeypatch.setattr(browser_forms, "AuditLog", partial(browser_forms.AuditLog, output_dir=str(tmp_path)))
    calls = []

    class StubExtract:
        def extract(self, model, pdf, page, schema, threshold, segment):
            calls.append((page, segment))
            words = 12 if page == 2 else 3
            span = {"label": "exclusion", "text": " ".join(["word"] * words), "start": 0, "end": 5 * words,
                    "score": 0.9, "words": words, "lines": [[0.1, 0.1, 0.9, 0.12]],
                    "legacy_cut": [0.5, 0.1, 0.55, 0.12] if words > 8 else None}
            return {"page_count": 3, "image": base64.b64encode(b"jpeg").decode(), "image_size": [10, 10],
                    "words": 100, "chars": 500, "parse_ms": 1.0, "model_ms": 5.0, "spans": [span],
                    "blocks": 4, "block_marks": []}

    events = []
    artifact = document_extract.run_extraction(
        StubExtract(), "multi", {"id": "doc", "title": "Doc"}, b"%PDF", "all", {"exclusion": "a clause"},
        on_event=lambda record, image: events.append((record["event"], image)))
    assert calls == [(1, True), (2, True), (3, True)]
    assert [e for e, _ in events].count("page_ready") == 3
    assert all(image == b"jpeg" for event, image in events if event == "page_ready")
    summary = [json.loads(line) for line in artifact.read_text().splitlines()][-1]
    assert summary["event"] == "completed" and summary["spans"] == 3 and summary["beyond_cap"] == 1
    assert summary["longest"]["page"] == 2 and summary["longest"]["words"] == 12
