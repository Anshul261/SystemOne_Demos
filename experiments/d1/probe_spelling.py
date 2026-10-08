"""Follow-up: can d1-3B catch a transposed-letter forgery (SPRINGFEILD) if asked differently?"""
import json, torch
from pathlib import Path
from PIL import Image
from transformers import AutoModel
m = AutoModel.from_pretrained("LiquidAI/d1-3B", trust_remote_code=True, dtype=torch.bfloat16,
                              revision="da1fe36a861f24690f27f622dca1d8688503d113").to("cuda")
data = Path("data/stamps"); meta = json.loads((data / "labels.json").read_text()); b = meta["box"]
WITH = {"correct": "SPRINGFIELD", "ei": "SPRINGFEILD", "fil": "SPRINGFILED", "nf": "SPRINFGIELD"}       # contains the forgery
WITHOUT = {"correct": "SPRINGFIELD", "fil": "SPRINGFILED", "nf": "SPRINFGIELD", "ri": "SPIRNGFIELD"}  # does not
def q(c): return {"w": {"type": "choice", "instructions": "Read the first word around the top of the stamp letter by letter. Which spelling is printed?", "criteria": c}}
for scale in (1, 2):
    tally = {}
    for it in meta["items"]:
        if it["label"] not in ("valid", "misspelled"): continue
        crop = Image.open(data / it["file"]).convert("RGB").crop((b[0]-24, b[1]-24, b[2]+24, b[3]+24))
        crop = crop.resize((crop.width*scale, crop.height*scale), Image.Resampling.LANCZOS)
        for name, c in (("with_forgery_option", WITH), ("without_forgery_option", WITHOUT)):
            a = m.system_one(None, q(c), images=[crop])["answers"]["w"]
            ok = (a["choice"] == "correct") == (it["label"] == "valid")
            tally.setdefault(name, [0, 0]); tally[name][0] += ok; tally[name][1] += 1
            print(scale, name, it["label"], a["choice"], round(a["probabilities"]["correct"], 3))
    print(f"scale {scale}x:", {k: f"{v[0]}/{v[1]}" for k, v in tally.items()})
