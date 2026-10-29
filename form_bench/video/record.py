"""Record a captioned demo of the chart entry console, from a doctor's note to a saved chart in OpenEMR.

    uv run --env-file .env uvicorn app:app --port 8050      # in another terminal
    uv run --env-file .env python video/record.py

Drives the real console in a 1920x1080 browser: one note, read by Jev into the three OpenEMR screens it needs,
reviewed, then entered and saved while Jev finds its way through OpenEMR's own menus. Captions are tied to
what the console shows: each waits for the step it describes. Then OpenEMR's own record of the visit is opened
and shown, so the video ends on what was saved, not on what the console says. Writes
video/out/chart-entry-demo.mp4 (and a smaller copy).

Saving writes to the EMR: one new patient with a visit, vitals and lab results.
"""

import base64
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

HERE = Path(__file__).parent
SECONDS: list[str] = []  # filled by the take
OUT = HERE / "out"
URL = "http://127.0.0.1:8050"
W, H = 1920, 1080
TRIM = 1.0  # seconds cut from the start of the recording (page load)
PATIENT = {"first name": "Adaora", "last name": "Okonkwo", "birth date": "1974-03-22"}
NOTE = ("New patient: Adaora Okonkwo, DOB 03/22/1974, female. Lives at 418 Brookfield Rd, Apt 3B, Springfield, "
        "Illinois 62704.\nSeen today for follow-up of borderline diabetes. BP on arrival 146/90, rechecked after "
        "5 min rest 134/84. HR 76, RR 14, SpO2 97% on room air. Temp 36.8 C oral. Wt 79 kg, ht 162 cm.\nLabs drawn "
        "this morning (collected {today}): glucose 112 mg/dL, sodium 139, potassium 4.6, chloride 103, CO2 25, BUN "
        "15, creatinine 0.8, calcium 9.4. HbA1c 6.3% (was 6.0% in April). Lipids: total cholesterol 214, LDL 138, "
        "HDL 49, triglycerides 160.")

OVERLAY = """
#tag { position: fixed; left: 32px; bottom: 96px; width: 760px; z-index: 9999; background: #1c2a35; color: #fff;
  padding: 16px 22px 18px; border-radius: 6px; font: 400 22px/1.4 'Atkinson Hyperlegible Next', sans-serif;
  box-shadow: 0 10px 28px rgba(28,42,53,.28); transition: opacity .3s; }
#tag .where { display: block; font-weight: 700; font-size: 15px; color: #ffe14d; margin-bottom: 6px; }
#cursor { position: fixed; z-index: 10000; width: 26px; height: 26px; border-radius: 50%; left: 960px; top: 540px;
  background: rgba(28,42,53,.18); border: 2px solid #1c2a35; pointer-events: none; transform: translate(-50%,-50%);
  transition: left .6s ease, top .6s ease; }
#cursor.click { animation: pulse .5s ease-out; }
@keyframes pulse { from { box-shadow: 0 0 0 0 rgba(28,42,53,.55); } to { box-shadow: 0 0 0 22px rgba(28,42,53,0); } }
"""


class Stage:
    """The recorded console, with a caption tag and a visible cursor."""

    def __init__(self, page):
        self.page = page

    def setup(self):
        self.page.add_style_tag(content=OVERLAY)
        self.page.evaluate("""() => { for (const id of ['tag', 'cursor']) { const el = document.createElement('div');
            el.id = id; document.body.appendChild(el); } document.getElementById('tag').style.opacity = 0; }""")

    def tag(self, where, text, hold=3200):
        self.page.evaluate("""([where, text]) => { const el = document.getElementById('tag');
            el.innerHTML = `<span class="where">${where}</span>${text}`; el.style.opacity = 1; }""", [where, text])
        self.page.wait_for_timeout(hold)

    def point(self, target):
        locator = self.page.locator(target).first if isinstance(target, str) else target
        locator.scroll_into_view_if_needed()
        box = locator.bounding_box()
        self.page.evaluate("([x, y]) => { const c = document.getElementById('cursor'); c.style.left = x + 'px';"
                           " c.style.top = y + 'px'; }", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        locator.hover()
        self.page.wait_for_timeout(750)

    def click(self, target):
        self.point(target)
        self.page.evaluate("() => { const c = document.getElementById('cursor'); c.classList.remove('click');"
                           " void c.offsetWidth; c.classList.add('click'); }")
        (self.page.locator(target).first if isinstance(target, str) else target).click()
        self.page.wait_for_timeout(450)

    def row(self, title_part, column="(Value)"):
        """A review row by the field's full label (its title), e.g. the A1c value."""
        return self.page.locator(".rows li").filter(
            has=self.page.locator(f'label.field-name[title*="{title_part}"][title*="{column}"]')).first

    def expect(self, condition, claim):
        """Stop the take if the screen does not show what the next caption claims."""
        if not self.page.evaluate(condition):
            raise SystemExit(f"Take stopped: the screen does not show that {claim}")

    def until(self, text, timeout=240000):
        """Wait until the console's step log shows this text, or stop the take on an error."""
        self.page.wait_for_function(
            "t => document.getElementById('journey').innerText.includes(t) || !document.getElementById('error').hidden",
            arg=text, timeout=timeout)
        self.expect("document.getElementById('error').hidden", f"{text!r} (the console reports an error)")

    def top(self):
        self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        self.page.wait_for_timeout(700)


def take(stage):
    page = stage.page
    stage.tag("The note", "A doctor's note, written the way it was said: a new patient, today's vitals and this "
                          "morning's labs. Synthetic patient.", 1200)
    stage.click("#note")
    page.type("#note", NOTE.format(today=date.today().isoformat()), delay=3)
    stage.tag("The note", "Two blood pressures, metric units, and two A1c results: today's and April's.", 3000)

    stage.tag("Read", "Jev reads the note and works out which OpenEMR screens it needs.", 1600)
    stage.click("#extract")
    page.wait_for_function("document.querySelectorAll('.screen').length > 0 || !document.getElementById('error').hidden",
                           timeout=180000)
    stage.expect("document.getElementById('error').hidden", "the note was read")
    page.wait_for_timeout(800)
    stage.tag("Read", page.inner_text("#read-log"), 4200)

    systolic = stage.row("BP Systolic")
    stage.point(systolic)
    pressure = "/".join(stage.row(f"BP {side}").locator("input").input_value() for side in ("Systolic", "Diastolic"))
    stage.tag("Review", f"Blood pressure was taken twice; Jev charted the rested recheck, {pressure}. Each value "
                        "lights up its words in the note.", 4200)
    stage.point(stage.row("Weight (LOINC:29463-7) lbs"))
    stage.tag("Review", "79 kg goes into OpenEMR's pounds field as 174.2; kilograms go in as written.", 3400)
    a1c = stage.row("4548-4")
    stage.point(a1c)
    stage.tag("Review", f"Two A1c results in the note: Jev entered today's, {a1c.locator('input').input_value()}%, "
                        "not April's 6.0. Above the range, so it is marked High.", 4200)
    for row in page.locator(".rows li.flagged:not(.resolved)").all():
        stage.point(row)
        stage.tag("Review", f"Flagged: {row.locator('.status span').first.inner_text()}. Checked against the note: "
                            "keep it.", 3200)
        stage.click(row.get_by_role("button", name="Keep this value"))
    stage.expect("!document.querySelector('#action').disabled", "entry is unlocked")
    stage.top()
    stage.tag("Enter and save", "Every value is checked. Now Jev goes into OpenEMR, the way a person would: through "
                                "its own menus and buttons.", 3200)
    stage.click("#action")

    phases = [
        ("Clicked New/Search", "New patient", "Patient, then New/Search, then the address section. These are routes "
                                              "Jev learned once and replays; the log marks each step."),
        ("Entered", "New patient", "Each value is typed, then read back from the page."),
        ("Saved New patient", "New patient", "Saved with OpenEMR's own Create New Patient button and its confirm "
                                             "popup."),
        ("Creating today's visit", "Vitals", "Vitals belong to a visit, so Jev creates today's visit for the new "
                                             "patient first."),
        ("Clicked Vitals", "Vitals", "Then Clinical, Vitals. 'Jev chose' marks a step Jev picked itself, with no "
                                     "saved route."),
        ("Saved Vitals", "Vitals", "Before every save Jev checks the screen is Adaora Okonkwo's chart."),
        ("Pending Review", "Lab results", "Procedures, then Pending Review, where results of the visit's lab orders "
                                          "are entered."),
        ("Saved Lab results", "Lab results", "45 results typed and read back, then signed."),
    ]
    for text, where, caption in phases:
        stage.until(text)
        stage.tag(f"In OpenEMR: {where}", caption, 900)
    page.wait_for_function("document.getElementById('status').className === 'saved'", timeout=120000)
    SECONDS.append(page.inner_text('[data-stage="enter"] .took'))
    stage.tag("Done", page.inner_text("#status"), 4200)
    page.locator("#journey").evaluate("e => e.scrollTop = 0")
    stage.point("#journey")
    stage.tag("Done", "The whole route through OpenEMR, step by step. No OpenEMR-specific code clicked anything: "
                      "Jev chose or replayed every step.", 4600)


def record() -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "raw"),
                                      record_video_size={"width": W, "height": H})
        page = context.new_page()
        page.goto(URL)
        page.wait_for_function("document.querySelectorAll('#target option').length >= 2")
        page.select_option("#target", "openemr")
        page.wait_for_timeout(600)
        stage = Stage(page)
        stage.setup()
        started = time.monotonic()
        take(stage)
        print(f"take: {time.monotonic() - started:.0f} s")
        video = page.video.path()
        context.close()
        browser.close()
    return Path(video)


def read_back() -> list[Path]:
    """Open the visit in OpenEMR and photograph its own summary: the vitals and the lab results it saved."""
    import emr
    from mapping import whole
    from targets import openemr, openemr_home

    target = openemr()
    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(ignore_https_errors=True, viewport={"width": 1280, "height": 860}).new_page()
        openemr_home(page)
        emr.procedures(target).do(page, "Open today's visit ({today}) of the patient {first name} {last name} "
                                       "(born {birth date}). Do not create a new visit.",
                                       PATIENT | {"today": date.today().isoformat()})
        page.wait_for_timeout(2500)
        frame = next(f for f in page.frames if "forms.php" in f.url)
        with whole(frame) as (shot, _):
            def tables(text):  # the innermost visible tables holding this text, top to bottom
                boxes = [t.bounding_box() for t in frame.locator("table").filter(has_text=text).all() if t.is_visible()]
                inner = [b for b in boxes if not any(o is not b and b["x"] <= o["x"] and b["y"] <= o["y"] and
                                                     o["x"] + o["width"] <= b["x"] + b["width"] and
                                                     o["y"] + o["height"] <= b["y"] + b["height"] for o in boxes)]
                return sorted(inner, key=lambda b: b["y"])

            def crop(b):  # boxes are already in the window's coordinates
                return shot.crop((int(b["x"]) - 4, int(b["y"]) - 4, int(b["x"] + b["width"]) + 4,
                                  int(b["y"] + b["height"]) + 4))

            labs = [crop(b) for b in tables("Ordered Procedure")]
            vitals = [crop(b) for b in tables("Blood Pressure")][:1]
            for name, parts in (("left", vitals + labs[2:]), ("right", labs[:2])):
                width, height = max(p.width for p in parts), sum(p.height for p in parts) + 24 * (len(parts) - 1)
                column = Image.new("RGB", (width, height), "white")
                y = 0
                for part in parts:
                    column.paste(part, (0, y))
                    y += part.height + 24
                path = OUT / f"readback-{name}.png"
                column.save(path)
                shots.append(path)
        page.screenshot(path=str(OUT / "readback-header.png"), clip={"x": 0, "y": 0, "width": 1280, "height": 150})
        browser.close()
    return shots


CARD = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Mono:wght@400;600&family=Atkinson+Hyperlegible+Next:wght@400;600;700&display=swap" rel="stylesheet">
<style>
body {{ margin: 0; width: 1920px; height: 1080px; background: #f6f8f8; color: #1c2a35;
  font-family: 'Atkinson Hyperlegible Next', sans-serif; display: flex; flex-direction: column;
  justify-content: center; padding: 0 160px; box-sizing: border-box; }}
h1 {{ font-size: 88px; line-height: 1.05; margin: 0 0 26px; letter-spacing: -.015em; }}
h2 {{ font-size: 56px; margin: 0 0 22px; }}
.sub {{ font-size: 34px; line-height: 1.4; color: #5d6b75; max-width: 1450px; }}
mark {{ background: #ffe14d; color: inherit; padding: 0 6px; border-radius: 3px; }}
.foot {{ margin-top: 70px; font-size: 24px; color: #5d6b75; }}
ol {{ margin: 34px 0 0; padding: 0; list-style: none; display: grid; gap: 24px; counter-reset: step; }}
li {{ counter-increment: step; display: grid; grid-template-columns: 90px 1fr; align-items: baseline; }}
li > div {{ grid-column: 2; }}
li::before {{ content: counter(step); font: 600 50px 'Atkinson Hyperlegible Mono', monospace; color: #13756b; }}
li b {{ font-size: 38px; }}
li span {{ display: block; font-size: 27px; line-height: 1.4; color: #5d6b75; margin-top: 4px; max-width: 1400px; }}
.big {{ display: flex; gap: 90px; margin: 40px 0 30px; }}
.big div {{ font: 600 96px 'Atkinson Hyperlegible Mono', monospace; }}
.big span {{ display: block; font: 400 26px 'Atkinson Hyperlegible Next'; color: #5d6b75; margin-top: 6px; max-width: 420px; }}
.shots {{ display: flex; gap: 40px; align-items: flex-start; }}
.shots img {{ width: 1600px; height: 700px; object-fit: contain; object-position: top left; }}
</style></head><body>{body}</body></html>"""


def cards(shots: list[Path], seconds: str) -> dict:
    images = [f'<img src="data:image/png;base64,{base64.b64encode(p.read_bytes()).decode()}">' for p in shots]
    return {
        "00-title": (5, """<h1>Chart entry</h1>
            <div class="sub">From a doctor's note to a saved chart in OpenEMR. Jev reads the note, works out the
              screens, and <mark>finds its own way</mark> through the EMR to enter and save them, after a person
              reviews every value.</div>
            <div class="foot">Jev (TypeSafe's System One model) makes every judgment. Shown on OpenEMR with a
              synthetic patient.</div>"""),
        "01-how": (12, """<h1>How it works</h1><ol>
            <li><div><b>Read</b><span>Code finds every name, date, address and number in the note; Jev picks which
              one each field takes, such as the rested blood pressure or today's A1c. Code converts units.</span></div></li>
            <li><div><b>Plan</b><span>Each EMR screen was looked at once by Jev, from screenshots, and kept: which
              field holds which part of the patient record.</span></div></li>
            <li><div><b>Review</b><span>A person sees each value beside the words it came from. Anything Jev is
              unsure of must be kept or corrected before entry unlocks.</span></div></li>
            <li><div><b>Enter and save</b><span>Jev reaches each screen through the EMR's own menus, replaying routes
              it learned and choosing any step that changed. Values are typed and read back; Jev checks it is the
              right patient; the EMR's own button saves.</span></div></li></ol>"""),
        **{f"98-readback-{n}": (7, f"""<h2>What OpenEMR saved</h2>
            <div class="sub" style="margin-bottom:28px">OpenEMR's own summary of Adaora Okonkwo's visit, opened after
              the run{part}.</div><div class="shots">{image}</div>""")
           for n, (image, part) in enumerate(zip(images, (": vitals and A1c", ": metabolic panel and lipids")))},
        "99-results": (10, f"""<h1>Measured</h1>
            <div class="big"><div>99.8%<span>of fields right on 154 messy notes Jev had not seen, with no wrong
              value left unflagged</span></div>
              <div>263/263<span>screen fields mapped right from screenshots, inside OpenEMR's own menus</span></div>
              <div>{seconds}<span>to enter and save the three screens, after review</span></div></div>
            <div class="sub">Synthetic patients. Lab-note accuracy has not been measured on a held-out set yet, and
              routes and thresholds have so far been tried on one EMR.</div>"""),
    }


def render_cards(shots, seconds):
    paths = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for name, (length, body) in cards(shots, seconds).items():
            page.set_content(CARD.format(body=body), wait_until="networkidle")
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path))
            paths[name] = (path, length)
        browser.close()
    return paths


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True).stdout)


def still(path, length, clip, fade_out=0.5):
    encode = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-preset", "medium", "-crf", "20"]
    ffmpeg("-loop", "1", "-i", str(path), "-t", str(length),
           "-vf", f"fade=in:st=0:d=0.5,fade=out:st={length - fade_out}:d={fade_out}", *encode, str(clip))


def main():
    OUT.mkdir(exist_ok=True)
    takes = sorted((OUT / "raw").glob("*.webm"), key=lambda p: p.stat().st_mtime)
    raw = takes[-1] if "--reuse" in sys.argv and takes else record()  # --reuse: keep the last take
    if not SECONDS:
        SECONDS.append(next((a for a in sys.argv if a.endswith("s") and a[:-1].replace(".", "").isdigit()), "55s"))
    shots = read_back()
    seconds = f"{round(float(SECONDS[0].split()[0].rstrip('s')))} s"  # the console's own timing of entry and save
    rendered = render_cards(shots, seconds)
    clips = []
    for name in ("00-title", "01-how"):
        clip = OUT / f"{name}.mp4"
        still(*rendered[name], clip)
        clips.append(clip)
    live = OUT / "02-live.mp4"
    ffmpeg("-ss", str(TRIM), "-i", str(raw), "-vf", "fade=in:st=0:d=0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           "-r", "30", "-preset", "medium", "-crf", "20", str(live))
    clips.append(live)
    for name in ("98-readback-0", "98-readback-1", "99-results"):
        clip = OUT / f"{name}.mp4"
        still(*rendered[name], clip, 0.8)
        clips.append(clip)
    listing = OUT / "concat.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips))
    final = OUT / "chart-entry-demo.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", "-movflags", "+faststart", str(final))
    small = OUT / "chart-entry-demo-small.mp4"  # for phones and upload limits
    ffmpeg("-i", str(final), "-c:v", "libx264", "-crf", "28", "-preset", "slow", "-pix_fmt", "yuv420p",
           "-movflags", "+faststart", str(small))
    for path in (final, small):
        print(f"{path}: {duration(path):.0f} s, {path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    sys.exit(main())
