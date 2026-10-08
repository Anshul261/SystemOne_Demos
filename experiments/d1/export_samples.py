"""Copy demo samples into ../../fixtures/stamps: two test-split documents per class with different
captures, the blank certificate template for printing, and the reference stamp."""

import json
import random
from pathlib import Path

from PIL import Image

import make_stamps

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "fixtures" / "stamps"
TITLES = {
    "valid": "Valid stamp", "missing": "No stamp", "outside_box": "Stamp outside the box", "wrong_shape": "Rectangular stamp",
    "wrong_color": "Red ink", "wrong_issuer": "Different insurer", "misspelled": "Misspelled insurer",
    "wrong_status": "VOID stamp", "date_mismatch": "Wrong date", "photocopy": "Photocopy", "pasted_digital": "Pasted digital stamp",
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = json.loads((Path("data/stamps_test/labels.json")).read_text())
    items = []
    for cls in make_stamps.CLASSES:
        picked, captures = [], set()
        for it in (i for i in meta["items"] if i["label"] == cls):
            if it["capture"] not in captures:
                picked.append(it); captures.add(it["capture"])
            if len(picked) == 2:
                break
        for n, it in enumerate(picked, start=1):
            doc_id = f"{cls}-{n}"
            Image.open(Path("data/stamps_test") / it["file"]).convert("RGB").save(OUT / f"{doc_id}.jpg", quality=85)
            items.append({"id": doc_id, "file": f"{doc_id}.jpg", "label": cls, "capture": it["capture"],
                          "title": TITLES[cls], "source_file": it["file"]})
    blank = make_stamps.page(random.Random("template"), "Your company name", "SMI-000000")
    blank.save(OUT / "certificate_template.png")
    make_stamps.stamp(make_stamps.SPEC, random.Random("ref"), clean=True).save(OUT / "reference_stamp.png")
    (OUT / "labels.json").write_text(json.dumps({"split": "test", "items": items}, indent=1) + "\n")
    print(len(items), "samples;", sum(f.stat().st_size for f in OUT.iterdir()) / 1e6, "MB")


if __name__ == "__main__":
    main()
