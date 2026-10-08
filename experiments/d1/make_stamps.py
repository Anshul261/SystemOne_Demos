"""Synthetic insurance certificates with a required official stamp, plus labelled defects.

The required stamp: round, blue, "SPRINGFIELD MUTUAL INSURANCE" around the top, "CLAIMS DEPARTMENT"
around the bottom, "APPROVED" and the document's issue date in the centre, inside the "Official stamp" box,
pressed in real ink on the original. Each defect class breaks exactly one rule.

Realism: stamps vary in font, size, pressure (partial impressions, faded sides), ghost double-strikes and
ink bleed; every page goes through a capture model — clean PDF render, flatbed scan (skew, blur, paper tint,
JPEG) or phone photo (perspective, uneven light, shadow, JPEG).

Usage: python make_stamps.py OUT_DIR [--per-class 6] [--split test]
"""

from __future__ import annotations

import argparse
import io
import json
import math
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

FONTS = "/usr/share/fonts/truetype/dejavu/"
STAMP_FONTS = ["DejaVuSans-Bold.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSansCondensed-Bold.ttf", "DejaVuSerifCondensed-Bold.ttf"]
SERIF, SERIF_BOLD = FONTS + "DejaVuSerif.ttf", FONTS + "DejaVuSerif-Bold.ttf"
PAGE = (1240, 1754)  # A4 at 150 dpi
BOX = (760, 1300, 1160, 1660)  # "Official stamp" box
ISSUE_DATE = "14 SEP 2026"
BLUES = [(28, 58, 168), (40, 52, 150), (52, 74, 190), (36, 44, 120)]
REDS = [(190, 30, 40), (170, 40, 60), (205, 50, 50)]

SPEC = {"top": "SPRINGFIELD MUTUAL INSURANCE", "bottom": "CLAIMS DEPARTMENT", "status": "APPROVED",
        "date": ISSUE_DATE, "ink": "blue", "shape": "round", "where": "box", "finish": "ink"}
CLASSES = {
    "valid": {},
    "missing": {"where": None},
    "outside_box": {"where": "header"},
    "wrong_shape": {"shape": "rect"},
    "wrong_color": {"ink": "red"},
    "wrong_issuer": {"top": "SHELBYVILLE MUTUAL INSURANCE"},
    "misspelled": {"top": "SPRINGFEILD MUTUAL INSURANCE"},
    "wrong_status": {"status": "VOID"},
    "date_mismatch": {"date": "02 MAR 2025"},
    "photocopy": {"finish": "photocopy"},       # whole page is a grey copy: no original ink to verify
    "pasted_digital": {"finish": "digital"},    # a flawless pasted image: no texture, no bleed, no rotation
}
REASONS = {
    "valid": "Stamp meets every requirement",
    "missing": "No stamp on the document",
    "outside_box": "Stamp is not inside the Official stamp box",
    "wrong_shape": "Stamp is not round",
    "wrong_color": "Stamp ink is not blue",
    "wrong_issuer": "Stamp names a different company",
    "misspelled": "Company name on the stamp is misspelled",
    "wrong_status": "Stamp does not say APPROVED",
    "date_mismatch": "Stamp date does not match the issue date",
    "photocopy": "Copy, not an original: ink colour cannot be verified",
    "pasted_digital": "Stamp looks digitally inserted",
}


def ring_text(layer: Image.Image, text: str, cx: float, cy: float, r: float, font, color, top: bool) -> None:
    """Letters around a circle: clockwise across the top, counter-clockwise across the bottom."""
    widths = [font.getlength(ch) for ch in text]
    total = sum(widths) / r
    angle = -math.pi / 2 - total / 2 if top else math.pi / 2 + total / 2
    for ch, w in zip(text, widths):
        step = w / r
        mid = angle + (step / 2 if top else -step / 2)
        x, y = cx + r * math.cos(mid), cy + r * math.sin(mid)
        glyph = Image.new("RGBA", (int(w) + 8, font.size + 10), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((4, 2), ch, font=font, fill=color + (255,))
        rot = -math.degrees(mid) - 90 if top else -math.degrees(mid) + 90
        glyph = glyph.rotate(rot, resample=Image.Resampling.BICUBIC, expand=True)
        layer.alpha_composite(glyph, (int(x - glyph.width / 2), int(y - glyph.height / 2)))
        angle += step if top else -step


def stamp(spec: dict, rng: random.Random, clean: bool = False) -> Image.Image:
    size = 300 if clean else rng.randint(270, 330)
    font_file = FONTS + (STAMP_FONTS[0] if clean else rng.choice(STAMP_FONTS))
    color = BLUES[0] if clean else rng.choice(BLUES if spec["ink"] == "blue" else REDS)
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c, ink, k = size / 2, color + (255,), size / 300
    small = ImageFont.truetype(font_file, round(21 * k))
    big, mid = ImageFont.truetype(font_file, round(34 * k)), ImageFont.truetype(font_file, round(22 * k))
    if spec["shape"] == "round":
        d.ellipse((6, 6, size - 6, size - 6), outline=ink, width=round(6 * k))
        d.ellipse((50 * k, 50 * k, size - 50 * k, size - 50 * k), outline=ink, width=3)
        ring_text(layer, spec["top"], c, c, c - 30 * k, small, color, top=True)
        ring_text(layer, spec["bottom"], c, c, c - 30 * k, small, color, top=False)
        for sx in (-1, 1):
            d.text((c + sx * (c - 30 * k) - 8, c - 12), "★", font=small, fill=ink)
    else:
        d.rectangle((6, 40 * k, size - 6, size - 40 * k), outline=ink, width=round(6 * k))
        d.rectangle((18 * k, 52 * k, size - 18 * k, size - 52 * k), outline=ink, width=2)
        name, word = spec["top"].rsplit(" ", 1)
        for y, line in ((64 * k, name), (90 * k, word), (size - 92 * k, spec["bottom"])):
            d.text((c, y), line, font=small, fill=ink, anchor="mt")
    d.text((c, c - 4), spec["status"], font=big, fill=ink, anchor="ms")
    d.text((c, c + 30 * k), spec["date"], font=mid, fill=ink, anchor="ms")
    if clean or spec["finish"] == "digital":
        return layer
    a = np.asarray(layer).astype(np.float32)
    nprng = np.random.default_rng(rng.randrange(1 << 30))
    alpha = a[..., 3] / 255
    # ink bleed: soften and slightly spread the strokes
    bleed = np.asarray(Image.fromarray((alpha * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3))
                       .filter(ImageFilter.GaussianBlur(1.1))).astype(np.float32) / 255
    alpha = np.maximum(alpha * 0.9, bleed * rng.uniform(0.45, 0.75))
    # speckled dropout from paper grain
    alpha *= nprng.random(alpha.shape) > rng.uniform(0.05, 0.2)
    # uneven pressure: one side fades, sometimes strongly (partial impression)
    yy, xx = np.mgrid[0:size, 0:size] / size
    theta = rng.uniform(0, 2 * math.pi)
    ramp = (np.cos(theta) * (xx - .5) + np.sin(theta) * (yy - .5)) + .5
    low = rng.uniform(0.1, 0.35) if rng.random() < 0.3 else rng.uniform(0.5, 0.8)
    alpha *= low + (1 - low) * ramp.clip(0, 1) ** 0.8
    a[..., 3] = (alpha * 255 * rng.uniform(0.8, 0.95)).clip(0, 255)
    out = Image.fromarray(a.astype(np.uint8))
    if rng.random() < 0.15:  # ghost double strike
        ghost = out.copy(); ghost.putalpha(ghost.getchannel("A").point(lambda v: v * 0.35))
        base = Image.new("RGBA", out.size, (0, 0, 0, 0))
        base.alpha_composite(ghost, (rng.randint(3, 8), rng.randint(-6, 6))); base.alpha_composite(out)
        out = base
    return out


def page(rng: random.Random, insured: str, policy: str) -> Image.Image:
    img = Image.new("RGB", PAGE, (252, 251, 247))
    d = ImageDraw.Draw(img)
    h1, body, bold = ImageFont.truetype(SERIF_BOLD, 40), ImageFont.truetype(SERIF, 26), ImageFont.truetype(SERIF_BOLD, 26)
    d.text((PAGE[0] / 2, 120), "CERTIFICATE OF INSURANCE COVERAGE", font=h1, fill=(20, 20, 20), anchor="mm")
    d.text((PAGE[0] / 2, 170), "Springfield Mutual Insurance · Claims Department", font=body, fill=(70, 70, 70), anchor="mm")
    d.line((100, 210, PAGE[0] - 100, 210), fill=(60, 60, 60), width=2)
    rows = [("Insured", insured), ("Policy number", policy), ("Coverage", rng.choice(["Commercial property, all risks", "General liability", "Business interruption", "Professional indemnity"])),
            ("Limit", f"${rng.choice([250, 500, 750, 1000])},000 per occurrence"), ("Issue date", "14 September 2026"),
            ("Valid until", "13 September 2027")]
    for i, (k, v) in enumerate(rows):
        d.text((120, 280 + i * 62), f"{k}:", font=bold, fill=(30, 30, 30))
        d.text((420, 280 + i * 62), v, font=body, fill=(30, 30, 30))
    para = ("This certificate is issued as a matter of information only and confers no rights upon the "
            "certificate holder. It is valid only when it bears the official round blue stamp of the "
            "Springfield Mutual Insurance Claims Department in the box below.")
    y, line = 720, ""
    for word in para.split():
        if body.getlength(line + " " + word) > PAGE[0] - 240:
            d.text((120, y), line.strip(), font=body, fill=(50, 50, 50)); y, line = y + 40, ""
        line += " " + word
    d.text((120, y), line.strip(), font=body, fill=(50, 50, 50))
    d.line((120, 1560, 620, 1560), fill=(40, 40, 40), width=2)
    d.text((120, 1575), "Authorized signature", font=body, fill=(80, 80, 80))
    pts = [(150 + i * 9, 1530 + 22 * math.sin(i / 2.3 + rng.random())) for i in range(48)]
    d.line(pts, fill=(25, 25, 80), width=3, joint="curve")
    d.rectangle(BOX, outline=(120, 120, 120), width=2)
    d.text((BOX[0] + 10, BOX[1] - 34), "Official stamp", font=body, fill=(80, 80, 80))
    return img


def place(doc: Image.Image, st: Image.Image, spec: dict, rng: random.Random) -> Image.Image:
    if spec["finish"] != "digital":
        st = st.rotate(rng.uniform(-25, 25), resample=Image.Resampling.BICUBIC, expand=True)
    if spec["where"] == "box":
        cx = (BOX[0] + BOX[2]) / 2 + rng.uniform(-35, 35)
        cy = (BOX[1] + BOX[3]) / 2 + rng.uniform(-30, 30)
    else:
        cx, cy = rng.uniform(220, 420), rng.uniform(330, 520)
    out = doc.convert("RGBA")
    ink = Image.new("RGBA", out.size, (0, 0, 0, 0))
    ink.alpha_composite(st, (int(cx - st.width / 2), int(cy - st.height / 2)))
    if spec["finish"] == "digital":  # pasted image: hard edges and a faint white matte around the strokes
        matte = Image.new("RGBA", out.size, (0, 0, 0, 0))
        m = ink.getchannel("A").filter(ImageFilter.MaxFilter(7))
        matte.putalpha(m.point(lambda v: 255 if v > 0 else 0))
        white = Image.new("RGBA", out.size, (255, 255, 255, 255)); white.putalpha(matte.getchannel("A"))
        out = Image.alpha_composite(out, white)
    return Image.alpha_composite(out, ink).convert("RGB")


def jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=quality); return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def capture(img: Image.Image, rng: random.Random, photocopy: bool) -> tuple[Image.Image, str]:
    """A clean render, a flatbed scan or a phone photo of the page."""
    nprng = np.random.default_rng(rng.randrange(1 << 30))
    if photocopy:
        img = ImageEnhance.Contrast(img.convert("L")).enhance(1.35).convert("RGB")
    mode = rng.choices(["clean", "scan", "photo"], weights=[1, 2, 2])[0]
    if mode == "clean":
        out = img
    elif mode == "scan":
        out = img.rotate(rng.uniform(-1.5, 1.5), resample=Image.Resampling.BICUBIC, fillcolor=(245, 244, 240))
        out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 0.8)))
        tint = np.array([rng.uniform(0.95, 1.0), rng.uniform(0.94, 0.99), rng.uniform(0.88, 0.96)])
        a = np.asarray(out).astype(np.float32) * tint + nprng.normal(0, 4, (PAGE[1], PAGE[0], 1))
        out = jpeg(Image.fromarray(a.clip(0, 255).astype(np.uint8)), rng.randint(70, 90))
    else:
        w, h = PAGE
        j = lambda: rng.uniform(-0.035, 0.035) * w  # noqa: E731 — corner jitter of a doc-scanner crop
        quad = (j(), j(), j(), h + j(), w + j(), h + j(), w + j(), j())
        out = img.transform(PAGE, Image.Transform.QUAD, quad, resample=Image.Resampling.BICUBIC, fillcolor=(90, 85, 80))
        yy, xx = np.mgrid[0:h, 0:w]
        lx, ly = rng.uniform(0, w), rng.uniform(0, h)
        light = 1.0 - rng.uniform(0.12, 0.3) * np.hypot((xx - lx) / w, (yy - ly) / h)
        if rng.random() < 0.5:  # soft shadow of a phone or hand across one edge
            edge = rng.choice(["left", "top", "right"])
            dist = {"left": xx / w, "top": yy / h, "right": 1 - xx / w}[edge]
            light *= 1 - 0.28 * np.clip(1 - dist / rng.uniform(0.15, 0.35), 0, 1)
        warm = np.array([1.0, rng.uniform(0.96, 0.99), rng.uniform(0.88, 0.95)])
        a = np.asarray(out).astype(np.float32) * light[..., None] * warm + nprng.normal(0, 5, (h, w, 1))
        out = Image.fromarray(a.clip(0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.2)))
        out = jpeg(out, rng.randint(60, 82))
    return out, mode


NAMES = ["Avery Morgan LLC", "Harbor Point Logistics", "Delgado & Sons Bakery", "Northwind Clinic Group",
         "Okafor Engineering", "Lindqvist Dental Studio", "Patel Family Pharmacy", "Riverside Auto Body",
         "Kowalski Print Works", "Tanaka Robotics", "Blue Fern Landscaping", "Mendes Freight Co."]


def make(cls: str, rng: random.Random) -> tuple[Image.Image, str]:
    spec = {**SPEC, **CLASSES[cls]}
    doc = page(rng, rng.choice(NAMES), f"SMI-{rng.randrange(100000, 999999)}")
    if spec["where"]:
        doc = place(doc, stamp(spec, rng), spec, rng)
    return capture(doc, rng, spec["finish"] == "photocopy")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--per-class", type=int, default=6)
    ap.add_argument("--split", default="test", help="seed namespace, so splits never share a document")
    args = ap.parse_args()
    out = Path(args.out)
    (out / "docs").mkdir(parents=True, exist_ok=True)
    labels = []
    for cls in CLASSES:
        for i in range(args.per_class):
            img, mode = make(cls, random.Random(f"{args.split}-{cls}-{i}"))
            name = f"docs/{cls}-{i}.jpg"
            img.save(out / name, quality=92)
            labels.append({"file": name, "label": cls, "capture": mode, "reason": REASONS[cls]})
    stamp(SPEC, random.Random("ref"), clean=True).save(out / "reference_stamp.png")
    (out / "labels.json").write_text(json.dumps({"split": args.split, "box": BOX, "page": PAGE,
                                                 "issue_date": ISSUE_DATE, "items": labels}, indent=1))
    print(f"wrote {len(labels)} documents to {out}")


if __name__ == "__main__":
    main()
