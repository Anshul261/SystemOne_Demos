"""Fill CMS's official fillable ABN forms (English and Spanish) with synthetic data and flatten them.

Source: CMS Beneficiary Notices Initiative, "ABN forms, English and Spanish, including large print" (zip).
The filled values become page content, so a text-layer reader sees them like any completed form.
Every value is invented; the Additional Information box says so on the page.

Usage: python make_abn.py ABN_ZIP_DIR OUT_DIR
"""

import io
import sys
from pathlib import Path

import pypdfium2 as pdfium
from pypdf import PdfReader, PdfWriter

FORMS = {
    "cms-abn-filled-en.pdf": ("ABNEnglish_508_v2029_Fillable.pdf", {
        "Item test service or careRow1": "Vitamin D, 25-hydroxy (82306)\nComprehensive metabolic panel (80053)",
        "Reason Medicare may not payRow1": "Medicare usually does not pay for vitamin D testing without a documented deficiency-related diagnosis.",
        "Estimated costRow1": "$54.00\n$32.00",
        "Additional Information": "Synthetic example for a software demo; all names and values are invented. Lab questions: Riverbend Community Lab, 1-555-010-4477.",
        "Signature": "Avery Morgan",
        "Date mmddyyyy": "10/08/2026",
        "Group1": "/Option 1",
    }),
    "cms-abn-filled-es.pdf": ("ABN_Spanish_508_v2029_Fillable.pdf", {
        "Artículo prueba servicio o cuidadoRow1": "Vitamina D, 25-hidroxi (82306)\nPanel metabólico completo (80053)",
        "Razón por la cual Medicare posiblemente no pagueRow1": "Medicare normalmente no paga la prueba de vitamina D sin un diagnóstico documentado de deficiencia.",
        "Costo estimadoRow1": "$54.00\n$32.00",
        "Información adicional": "Ejemplo sintético para una demostración de software; todos los nombres y valores son inventados. Laboratorio: Riverbend Community Lab, 1-555-010-4477.",
        "Firma": "Avery Morgan",
        "Fecha mmddaaaa": "10/08/2026",
        "Group1": "/Opcion 1",
    }),
}


def fill(source: Path, values: dict[str, str]) -> bytes:
    writer = PdfWriter(clone_from=PdfReader(source))
    for page in writer.pages:
        writer.update_page_form_field_values(page, values, auto_regenerate=False)
    writer.set_need_appearances_writer(True)
    buffer = io.BytesIO()
    writer.write(buffer)
    # Flatten with pdfium so field values are drawn into the page content and its text layer.
    pdf = pdfium.PdfDocument(buffer.getvalue())
    pdf.init_forms()
    for i in range(len(pdf)):
        page = pdf[i]
        page.flatten()
        page.close()
    out = io.BytesIO()
    pdf.save(out)
    pdf.close()
    return out.getvalue()


def main() -> None:
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    for name, (form, values) in FORMS.items():
        data = fill(src / form, values)
        (out / name).write_bytes(data)
        pdf = pdfium.PdfDocument(data)
        text = " ".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
        missing = [v for v in values.values() if not v.startswith("/") and v.split("\n")[0][:20] not in text.replace("\r\n", " ")]
        print(name, f"{len(data) // 1024} KB, {len(pdf)} pages, values missing from text layer: {missing or 'none'}")


if __name__ == "__main__":
    main()
