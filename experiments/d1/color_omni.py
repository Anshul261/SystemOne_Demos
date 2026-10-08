"""Can omni see ink colour? Plain shapes, then real stamp crops, several phrasings."""
import json, torch
from PIL import Image, ImageDraw
from transformers import AutoModel
from calibrate import region
m = AutoModel.from_pretrained("LiquidAI/d1-omni-600M", trust_remote_code=True, dtype=torch.float16,
                              revision="414f8d6438174f5b2133a9c21a478fc42625e308").to("cuda")
Q = {
 "plain": {"type": "choice", "instructions": "What colour is it?", "criteria": {"blue": "Blue", "red": "Red", "black": "Black"}},
 "shape": {"type": "choice", "instructions": "Describe the image.", "criteria": {"blue": "A blue ring on white paper", "red": "A red ring on white paper", "black": "A black ring on white paper"}},
 "ink": {"type": "choice", "instructions": "Describe the image.", "criteria": {"blue": "A blue ink stamp", "red": "A red ink stamp", "black": "A black and white photocopy of a stamp"}},
}
for name, col in (("blue", (40, 60, 170)), ("red", (190, 30, 40)), ("black", (40, 40, 40))):
    im = Image.new("RGB", (448, 448), "white"); ImageDraw.Draw(im).ellipse((80, 80, 368, 368), outline=col, width=14)
    a = m.system_one(None, Q, images=[im])["answers"]
    print("ring", name, {k: (v["choice"], round(v["confidence"], 2)) for k, v in a.items()})
meta = json.load(open("data/stamps_test/labels.json"))
hit = {k: 0 for k in Q}; n = 0
for it in meta["items"]:
    if it["label"] not in ("valid", "wrong_color", "photocopy"): continue
    want = {"valid": "blue", "wrong_color": "red", "photocopy": "black"}[it["label"]]
    a = m.system_one(None, Q, images=[region(Image.open("data/stamps_test/" + it["file"]).convert("RGB"))])["answers"]
    n += 1
    for k in Q: hit[k] += a[k]["choice"] == want
print("stamp crops (blue/red/grey, n=%d):" % n, {k: f"{v}/{n}" for k, v in hit.items()})
