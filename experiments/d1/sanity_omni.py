"""Is omni's image path behaving? Card example + trivially easy yes/no pairs, both polarities."""
import torch
from PIL import Image, ImageDraw
from transformers import AutoModel
from transformers.image_utils import load_image
m = AutoModel.from_pretrained("LiquidAI/d1-omni-600M", trust_remote_code=True, dtype=torch.float16,
                              revision="414f8d6438174f5b2133a9c21a478fc42625e308").to("cuda")
cats = load_image("http://images.cocodataset.org/val2017/000000039769.jpg")
print("card example:", m.system_one(None, {"cats": {"type": "choice", "instructions": "How many cats are there?",
      "criteria": {"one": "One", "two": "Two", "more": "Three or more"}}}, images=[cats])["answers"]["cats"])
blank = Image.new("RGB", (640, 480), "white")
red = Image.new("RGB", (640, 480), "white"); ImageDraw.Draw(red).ellipse((170, 90, 470, 390), fill=(200, 20, 20))
for state in (None, "A photo."):
    for name, img in (("cats", cats), ("blank", blank), ("red_circle", red)):
        a = m.system_one(state, {
            "cat": {"type": "noul", "instructions": "Is there a cat in the image?"},
            "nocat": {"type": "noul", "instructions": "Is the image free of any cats?"},
            "circle": {"type": "noul", "instructions": "Is there a red circle in the image?"},
            "what": {"type": "choice", "instructions": "What does the image show?",
                     "criteria": {"cats": "Cats on a sofa", "blank": "A plain white blank image", "circle": "A red circle on white"}},
        }, images=[img])["answers"]
        print(f"state={state!r:12} {name:10} P(cat)={a['cat']['noul']:.3f} P(no cat)={a['nocat']['noul']:.3f} "
              f"P(red circle)={a['circle']['noul']:.3f}  what={a['what']['choice']} {a['what']['confidence']:.2f}")
