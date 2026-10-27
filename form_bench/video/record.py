"""Record a captioned demo of the chart entry console on every screen, end to end.

    uv run --env-file .env uvicorn app:app --port 8050      # in another terminal
    uv run python video/record.py

Drives the real console in a 1920x1080 browser: the local intake form, then OpenEMR's new-patient and vitals
screens, each through extract, review, entry and save. Caption tags say what is happening. Before a caption
claims something, the take checks the screen shows it, and stops otherwise. Title, how-it-works and results
cards come from the latest held-out bench results. Writes video/out/chart-entry-demo.mp4.

Saving writes to the EHR: one new patient, and vitals on the Bench Fixture visit.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE / "out"
URL = "http://127.0.0.1:8050"
W, H = 1920, 1080
TRIM = 1.0  # seconds cut from the start of the recording (page load)
NEW_PATIENT = ("Pt Kris, Franklin, 73M, DOB 1952-10-05. Addr 657 Bergnaum Ville Apt 4, Hopkinton Massachusetts "
               "01748. HR 72; BP 122/80.")
VITALS = ("Pt Okafor, Grace, 58F, DOB 1968-04-12. BP 152/96 on arrival, repeat 134/86 after rest; HR 88; RR 16; "
          "temp 98.9 F (oral); SpO2 96%; weight 171.4 lb; height 64.2 in.")

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

    def type_note(self, note):
        self.click("#note")
        self.page.fill("#note", "")
        self.page.type("#note", note, delay=12)
        self.page.wait_for_timeout(400)

    def row(self, name):
        exact = re.compile(f"^{re.escape(name)}$")
        return self.page.locator("#rows li").filter(has=self.page.locator("label.field-name", has_text=exact)).first

    def is_open_flag(self, row):
        classes = (row.get_attribute("class") or "").split() if row.count() else []
        return "flagged" in classes and "resolved" not in classes

    def expect(self, condition, claim):
        """Stop the take if the screen does not show what the next caption claims."""
        if not self.page.evaluate(condition):
            raise SystemExit(f"Take stopped: the screen does not show that {claim}")

    def extract(self, screen):
        self.page.select_option("#screen", screen)
        self.click("#extract")
        self.page.wait_for_selector("#rows li", timeout=120000)
        self.page.wait_for_timeout(900)

    def enter(self):
        self.click("#action")
        self.page.wait_for_function("document.querySelector('#action').textContent.startsWith('Save')", timeout=180000)
        self.page.wait_for_timeout(800)

    def save(self):
        self.click("#action")
        self.page.wait_for_function("document.querySelector('#status').className === 'saved'"
                                    " || !document.querySelector('#error').hidden", timeout=60000)
        self.expect("document.querySelector('#status').className === 'saved'", "the save succeeded")
        self.page.wait_for_timeout(600)

    def top(self):
        self.page.evaluate("window.scrollTo({top: 0, behavior: 'smooth'})")
        self.page.wait_for_timeout(700)


def local_form(stage):
    where = "Local intake form"
    stage.tag(where, "A clinician's visit note goes in on the left. This one is a synthetic patient.", 2400)
    stage.click("#sample")
    stage.page.wait_for_function("document.querySelector('#note').value.length > 0")
    stage.tag(where, "First, extraction: pick the screen and let the models read the note.", 2600)
    stage.extract("local:0")
    stage.tag(where, "Qwen3.8-27B pulled a value for each field. Jev checked every one against the note.", 3400)
    first = stage.page.locator("#rows li").first
    stage.point(first)
    stage.tag(where, "Point at a field and the words it came from light up in the note.", 3600)
    stage.point(stage.page.locator("#rows li").nth(4))
    stage.tag(where, "Each value shows how sure Jev is. Nothing here needs a person's decision.", 3200)
    stage.tag(where, "Second, entry. The approved values are typed into the form while you watch.", 2400)
    stage.enter()
    stage.tag(where, "Every value was typed, then read back from the page to confirm it stuck.", 3200)
    stage.save()
    stage.tag(where, "Saved. Next, the same flow on a real EHR.", 2400)
    stage.top()


def openemr_patient(stage):
    where = "OpenEMR: new patient"
    stage.click("#edit-note")
    stage.type_note(NEW_PATIENT)
    stage.tag(where, "A harder note: family name first, sex as shorthand (73M), and the apartment run into the street.",
              3600)
    stage.extract("openemr:0")
    stage.expect("document.querySelectorAll('#rows li.flagged:not(.resolved)').length > 0", "anything is flagged")
    flagged = stage.page.locator("#rows li.flagged:not(.resolved)").count()
    stage.tag(where, f"Jev flags {flagged} values it is less sure of. Entry stays locked until a person decides.", 3600)
    stage.point("#action")
    stage.tag(where, "The Enter button is locked while flags are open, on the page and on the server.", 3000)

    decisions = [
        ("Last Name", "keep", "'Pt Kris, Franklin' puts the family name first, so Kris is right. Keep it."),
        ("First Name", "keep", "And Franklin is the first name. Keep it."),
        ("Birth Sex", "keep", "73M means a 73-year-old male. Keep Male."),
    ]
    for name, _, caption in decisions:
        row = stage.row(name)
        if stage.is_open_flag(row):
            stage.point(row)
            stage.tag(where, caption, 3000)
            stage.click(row.get_by_role("button", name="Keep this value"))

    line_two = stage.row("Address Line 2")
    if line_two.count():
        stage.point(line_two)
        stage.tag(where, "Jev thinks the note gives a second address line. It does: Apt 4. Correct it.", 3200)
        stage.click(line_two.locator("input"))
        line_two.locator("input").fill("")
        line_two.locator("input").type("Apt 4", delay=60)
        stage.click(line_two.get_by_role("button", name="Use my value"))
        street = stage.row("Street and Number")
        stage.click(street.locator("input"))
        street.locator("input").fill("")
        street.locator("input").type("657 Bergnaum Ville", delay=40)
        stage.click(street.get_by_role("button", name="Use my value"))
        stage.tag(where, "And take Apt 4 out of the street.", 2200)

    for row in stage.page.locator("#rows li.flagged:not(.resolved)").all():  # anything else: keep, captioned
        stage.point(row)
        stage.tag(where, "Checked against the note. Keep it.", 2200)
        stage.click(row.get_by_role("button", name="Keep this value"))

    stage.expect("!document.querySelector('#action').disabled", "entry is unlocked")
    stage.tag(where, "Every flag is decided, so entry unlocks.", 2200)
    stage.enter()
    stage.tag(where, "Typed into OpenEMR and read back. Check it here before anything is saved.", 3400)
    stage.save()
    saved = stage.page.inner_text("#status")
    stage.tag(where, f"{saved}.", 2800)
    stage.top()


def openemr_vitals(stage):
    where = "OpenEMR: vitals"
    stage.click("#edit-note")
    stage.type_note(VITALS)
    stage.tag(where, "Vitals, on a test visit. The note gives a first blood pressure and a repeat after rest.", 3400)
    stage.extract("openemr:1")
    systolic = stage.row("BP Systolic mmHg")
    stage.point(systolic)
    stage.tag(where, f"It charted the rested repeat: BP {systolic.locator('input').input_value()}. "
                     "Point at it to see where it came from.", 3600)
    stage.point(stage.row("Temp Location"))
    stage.tag(where, "OpenEMR's vitals table has no field labels. The screen is read by its rows and columns.", 3400)
    stage.tag(where, "Entry: watch each value go into OpenEMR, outlined green once it is read back.", 2600)
    stage.enter()
    stage.tag(where, "OpenEMR fills in kg and cm itself. Every value the note gave is entered.", 3400)
    stage.save()
    stage.tag(where, f"{stage.page.inner_text('#status')}.", 2800)


CARD = """<!doctype html><html><head><meta charset="utf-8">
<link href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible+Mono:wght@400;600&family=Atkinson+Hyperlegible+Next:wght@400;600;700&display=swap" rel="stylesheet">
<style>
body {{ margin: 0; width: 1920px; height: 1080px; background: #f6f8f8; color: #1c2a35;
  font-family: 'Atkinson Hyperlegible Next', sans-serif; display: flex; flex-direction: column;
  justify-content: center; padding: 0 160px; box-sizing: border-box; }}
h1 {{ font-size: 92px; line-height: 1.05; margin: 0 0 26px; letter-spacing: -.015em; }}
.sub {{ font-size: 36px; line-height: 1.4; color: #5d6b75; max-width: 1400px; }}
mark {{ background: #ffe14d; color: inherit; padding: 0 6px; border-radius: 3px; }}
.foot {{ margin-top: 80px; font-size: 24px; color: #5d6b75; }}
ol {{ margin: 40px 0 0; padding: 0; list-style: none; display: grid; gap: 28px; counter-reset: step; }}
li {{ counter-increment: step; display: grid; grid-template-columns: 90px 1fr; align-items: baseline; }}
li > div {{ grid-column: 2; }}
li::before {{ content: counter(step); font: 600 54px 'Atkinson Hyperlegible Mono', monospace; color: #13756b; }}
li b {{ font-size: 40px; }}
li span {{ display: block; font-size: 29px; line-height: 1.4; color: #5d6b75; margin-top: 4px; max-width: 1350px; }}
.big {{ display: flex; gap: 110px; margin: 46px 0 34px; }}
.big div {{ font: 600 104px 'Atkinson Hyperlegible Mono', monospace; }}
.big span {{ display: block; font: 400 27px 'Atkinson Hyperlegible Next'; color: #5d6b75; margin-top: 6px; }}
</style></head><body>{body}</body></html>"""


def latest_results():
    """Totals from the most recent held-out OpenEMR run of the product agent."""
    for path in sorted((HERE.parent / "results").glob("openemr-*.json"), reverse=True):
        data = json.loads(path.read_text())
        if data.get("offset") and "mapfill" in data["summary"]:
            return data, data["summary"]["mapfill"]
    raise SystemExit("No held-out results: run bench.py run --target openemr --agents mapfill --offset 20 first")


def cards():
    data, s = latest_results()
    fields = sum(len(run["fields"]) for run in data["runs"])
    return {
        "00-title": (5, """<h1>Chart entry</h1>
            <div class="sub">From a clinician's visit note to an EHR record: <mark>extracted</mark>, checked,
              reviewed by a person, then entered while you watch.</div>
            <div class="foot">Qwen3.8-27B extracts, Jev checks, Playwright enters. Shown on OpenEMR with synthetic
              patients.</div>"""),
        "01-how": (11, """<h1>How it works</h1><ol>
            <li><div><b>Extraction</b><span>Qwen3.8-27B reads the note and proposes a value for every field on the
              screen. Jev, a decision model, scores how sure it is of each one.</span></div></li>
            <li><div><b>Review</b><span>A person sees each value beside the words it came from. Anything Jev is
              unsure of must be kept or corrected before entry unlocks.</span></div></li>
            <li><div><b>Entry</b><span>The approved values are typed into the EHR field by field and read back,
              while the person watches. Then they save.</span></div></li></ol>"""),
        "99-results": (9, f"""<h1>On {data['cases']} patients it had not seen</h1>
            <div class="big"><div>{s['field_accuracy']:.0%}<span>of {fields} fields entered right</span></div>
              <div>{s['unflagged_errors']}<span>errors nobody was warned about</span></div>
              <div>{s['median_ms'] / 1000:.1f} s<span>per screen, about ${s['usd_per_case']:.3f}</span></div></div>
            <div class="sub">{s['flagged']} fields were flagged for a person to check, and all of them were in fact
              right. Synthetic patients, with notes written from three templates; messier real notes are the next
              test.</div>"""),
    }


def render_cards():
    paths = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for name, (seconds, body) in cards().items():
            page.set_content(CARD.format(body=body), wait_until="networkidle")
            path = OUT / f"{name}.png"
            page.screenshot(path=str(path))
            paths[name] = (path, seconds)
        browser.close()
    return paths


def record() -> Path:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(OUT / "raw"),
                                      record_video_size={"width": W, "height": H})
        page = context.new_page()
        page.goto(URL)
        page.wait_for_function("document.querySelectorAll('#screen option').length >= 3")
        page.wait_for_timeout(600)
        stage = Stage(page)
        stage.setup()
        started = time.monotonic()
        local_form(stage)
        openemr_patient(stage)
        openemr_vitals(stage)
        print(f"take: {time.monotonic() - started:.0f} s")
        video = page.video.path()
        context.close()
        browser.close()
    return Path(video)


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def duration(path):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                 str(path)], capture_output=True, text=True).stdout)


def main():
    OUT.mkdir(exist_ok=True)
    encode = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", "30", "-preset", "medium", "-crf", "20"]
    clips = []
    rendered = render_cards()
    for name in ("00-title", "01-how"):
        path, seconds = rendered[name]
        clip = OUT / f"{name}.mp4"
        ffmpeg("-loop", "1", "-i", str(path), "-t", str(seconds),
               "-vf", f"fade=in:st=0:d=0.5,fade=out:st={seconds - 0.5}:d=0.5", *encode, str(clip))
        clips.append(clip)
    raw = record()
    live = OUT / "02-live.mp4"
    ffmpeg("-ss", str(TRIM), "-i", str(raw), "-vf", "fade=in:st=0:d=0.5", *encode, str(live))
    clips.append(live)
    path, seconds = rendered["99-results"]
    clip = OUT / "99-results.mp4"
    ffmpeg("-loop", "1", "-i", str(path), "-t", str(seconds),
           "-vf", f"fade=in:st=0:d=0.5,fade=out:st={seconds - 0.8}:d=0.8", *encode, str(clip))
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
