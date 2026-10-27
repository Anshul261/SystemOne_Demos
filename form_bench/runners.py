"""The agents the bench compares. Each fills one screen from a note in a page it opens; models.metered()
records what it spent.

- mapfill: the product, run end to end with no person in the loop: extraction.extract, then entry.enter.
- jev: jev-ultrafast unchanged, given the note as its goal, as an outside reference. System One picks every
  action; a small LLM writes each typed value.
"""

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp
from jev_ultrafast import Agent, agent, browser
from playwright.sync_api import BrowserContext, Page

from entry import enter
from extraction import extract
from forms import open_screen, read_form
from models import Meter

GOAL = ("Enter this patient's details into the form exactly as the note states them. Leave any field the note "
        "does not mention empty. Do not press Save or submit the form. Stop when every stated detail is entered.\n"
        "Note:\n{note}")


def run_mapfill(context: BrowserContext, screen, note: str, spent: Meter) -> Page:
    page = open_screen(context, screen)
    snapshot, fields = read_form(page)
    values, flags, confidence = extract(note, snapshot, fields)
    flags |= enter(page, fields, values)
    spent.actions = len(values)
    spent.flagged = [control(fields[i]) for i in flags]
    spent.confidence = [(control(fields[i]), c) for i, c in confidence.items()]
    return page


def control(field: dict) -> dict:
    """Just what identifies a control, for the results file."""
    return {k: field.get(k) for k in ("role", "name", "row", "nth")}


class PageBrowser(browser.Browser):
    """jev-ultrafast's browser, attached to a tab Playwright opened instead of a new one, so the scorer sees
    the same page. Playwright has already navigated it."""

    def __init__(self, target_id: str):
        ensure_daemon()
        self.target = None  # Playwright owns the tab and closes it
        self.session = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)


def run_jev(context: BrowserContext, screen, note: str, spent: Meter) -> Page:
    """jev-ultrafast as shipped, with the note as its goal."""
    page = open_screen(context, screen)
    target_id = context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    agent.Browser = lambda _url: PageBrowser(target_id)
    jev = Agent(screen.url, GOAL.format(note=note))
    try:
        for _ in jev.run():
            pass
    except (ValueError, RuntimeError) as error:  # its step budget, or a model error: score what it entered
        spent.error = str(error)
    spent.actions = len(jev.state["history"])
    return page


AGENTS = {"mapfill": run_mapfill, "jev": run_jev}
