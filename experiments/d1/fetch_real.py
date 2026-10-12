"""Materialise real-document eval sets from the Hub as PNG + labels.json.

  data/staver   adambozson/StaVer validation split: invoices with a blue logo and maybe a stamp.
                label = stamp / no_stamp from the stamp segmentation mask (>= 200 stamp pixels).
  data/sifta    zodumair/sifta-document-forgery-dataset, all splits: real / forged documents.

Neither dataset states a license and SIFTA holds real names: keep the output local or in a private bucket.
Needs HF_TOKEN in the environment.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import duckdb
import numpy as np
from PIL import Image

STAVER = "hf://datasets/adambozson/StaVer/data/validation-00000-of-00001.parquet"
SIFTA = "hf://datasets/zodumair/sifta-document-forgery-dataset/data/{split}-00000-of-00001.parquet"


def main() -> None:
    con = duckdb.connect()
    con.execute(f"CREATE SECRET hf (TYPE HUGGINGFACE, TOKEN '{os.environ['HF_TOKEN']}')")

    out = Path("data/staver"); (out / "docs").mkdir(parents=True, exist_ok=True)
    items = []
    for i, (img, mask) in enumerate(con.execute(f"SELECT image.bytes, label.bytes FROM '{STAVER}'").fetchall()):
        stamp_px = int((np.asarray(Image.open(io.BytesIO(mask)).convert("L")) > 127).sum())
        name = f"docs/staver-{i:03d}.png"
        Image.open(io.BytesIO(img)).convert("RGB").save(out / name)
        items.append({"file": name, "label": "stamp" if stamp_px >= 200 else "no_stamp", "stamp_px": stamp_px})
    (out / "labels.json").write_text(json.dumps({"source": "adambozson/StaVer@validation", "items": items}, indent=1))
    print("staver", len(items), {k: sum(it["label"] == k for it in items) for k in ("stamp", "no_stamp")})

    out = Path("data/sifta"); (out / "docs").mkdir(parents=True, exist_ok=True)
    items = []
    for split in ("train", "validation", "test"):
        rows = con.execute(f"SELECT image.bytes, label_name FROM '{SIFTA.format(split=split)}'").fetchall()
        for i, (img, label) in enumerate(rows):
            name = f"docs/sifta-{split}-{i:03d}.png"
            Image.open(io.BytesIO(img)).convert("RGB").save(out / name)
            items.append({"file": name, "label": label, "split": split})
    (out / "labels.json").write_text(json.dumps({"source": "zodumair/sifta-document-forgery-dataset", "items": items}, indent=1))
    print("sifta", len(items), {k: sum(it["label"] == k for it in items) for k in ("real", "forged")})


if __name__ == "__main__":
    main()
