"""The EMRs: each a list of screens, described twice, for two users.

The bench (bench.py) opens each screen by its address and scores what the agents typed against an answer key:
a case's field keys mapped to controls, by ARIA role and accessible name or table row. Only the scorer uses
it; the agents under test see the page and the note, nothing else.

The console (app.py, emr.py) reaches each screen the way a person does, through the EMR's own menus, and saves
with its own buttons. Its screens are described as goals in plain language, which Jev pursues (steps.py), not
as scripts. Goals may use the EMR's own words ("Pending Review", "Sign Results") where Jev could not otherwise
know which of its screens is meant; they never say where to click. Two things stay as code, and neither is
entry: signing in (credentials never go to a model) and placing lab orders (test setup: results only arrive
for orders).
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext

import labs


@dataclass(frozen=True)
class Screen:
    # For the bench: where the screen is, and how to score it.
    url: str
    answers: dict[str, dict | list[dict]]  # field key -> named(), in_row(), any_of(), or a list joined in order
    prepare: callable = None  # prepare(page): puts the screen in its starting state, the same for every agent
    derived: list[dict] = field(default_factory=list)  # fields the page fills itself, ignored as agent extras
    save: callable = None  # save(page) -> what was saved: a scripted save, for a screen with no save_goal
    setup: callable = None  # setup(page, record): the bench's preparation by address, such as placing lab orders

    # For the console.
    title: str = ""  # how the console names the screen
    open: tuple = ()  # how to reach it: goals Jev pursues in order, and setup(page, record) calls between them
    save_goal: str = ""  # the goal that saves it with the EMR's own button
    patient: bool = False  # in a patient's chart: Jev checks it shows the right patient before saving
    visit: bool = False  # inside a visit: today's visit is created first, once per note


def named(role: str, name: str, nth: int = 0) -> dict:
    """A control by ARIA role and accessible name; nth picks among controls that share the name."""
    return {"role": role, "name": name, "nth": nth}


def any_of(*fields: dict) -> dict:
    """Either control may hold the value: a note's "female" fits OpenEMR's Birth Sex and Sex alike."""
    return {"any": list(fields)}


def in_row(role: str, row: str, nth: int = 0) -> dict:
    """An unlabelled control, by the accessible name of its table row."""
    return {"role": role, "row": row, "nth": nth}


@dataclass
class Target:
    name: str
    screens: list[Screen]
    login: callable = None  # login(context) before the first case
    saves: list[str] = field(default_factory=list)  # URL globs that save; requests to them are blocked and counted
    home: callable = None  # home(page): sign in on this page and wait at the EMR's first screen (the console)
    visit: str = ""  # the goal that creates a patient's visit for this note
    fixture: dict = field(default_factory=dict)  # a test patient's details, to look at a screen before any note

    def blocked_saves(self, context: BrowserContext) -> list[str]:
        """Block every save request and return the list they are recorded in, so no run writes data."""
        attempts: list[str] = []

        def block(route):
            attempts.append(route.request.url)
            route.abort()

        for pattern in self.saves:
            context.route(pattern, block)
        return attempts


PATIENT = {"first_name": named("textbox", "First Name"), "last_name": named("textbox", "Last Name"),
           "dob": named("textbox", "DOB"), "sex": named("combobox", "Sex"), "street": named("textbox", "Address"),
           "city": named("textbox", "City"), "state": named("combobox", "State"), "postal_code": named("textbox", "Postal Code")}
VITALS = {"weight": named("spinbutton", "Weight"), "height": named("spinbutton", "Height"),
          "bp_systolic": named("spinbutton", "BP Systolic"), "bp_diastolic": named("spinbutton", "BP Diastolic"),
          "pulse": named("spinbutton", "Pulse"), "respiration": named("spinbutton", "Respiration"),
          "temperature": named("spinbutton", "Temperature"), "temp_method": named("combobox", "Temp Location"),
          "oxygen_saturation": named("spinbutton", "Oxygen Saturation")}

def save_local(page) -> str:
    page.get_by_role("button", name="Save").click()
    return "Saved on the local intake form" if page.evaluate("document.body.dataset.submitted") else ""


LOCAL = Target("local", [Screen((Path(__file__).parent / "fixtures" / "intake.html").as_uri(),
                                {**PATIENT, **VITALS, "allergies": named("textbox", "Allergies")},
                                title="Intake form (local)", save=save_local)])


FIXTURE = Path(__file__).parent / "data" / "openemr_fixture.json"


def openemr_fixture(page, base: str) -> dict:
    """A test patient with one encounter, for screens that live inside an encounter such as vitals.
    Created once and remembered in data/. The forms are submitted directly because OpenEMR's own submit
    scripts need its tabbed shell."""
    if FIXTURE.exists():
        ids = json.loads(FIXTURE.read_text())
        page.goto(f"{base}/interface/patient_file/summary/demographics.php?set_pid={ids['pid']}")
        if "Bench Fixture" in page.locator("body").inner_text():
            return ids
    page.goto(f"{base}/interface/new/new.php")
    page.locator("[name=form_fname]").fill("Bench")
    page.locator("[name=form_lname]").fill("Fixture")
    page.locator("[name=form_DOB]").fill("1970-01-01")
    with page.expect_navigation():
        page.evaluate("document.querySelector('form[action*=new_comprehensive_save]').submit()")
    pid = parse_qs(urlparse(page.url).query)["set_pid"][0]
    page.goto(f"{base}/interface/forms/newpatient/new.php")
    page.locator("[name=pc_catid]").select_option(label="Office Visit")
    with page.expect_navigation():
        page.evaluate("document.getElementById('new-encounter-form').submit()")
    page.goto(f"{base}/interface/patient_file/history/encounters.php")
    encounter = re.search(r'id="(\d+)~\d{4}-\d\d-\d\d"', page.content())[1]
    FIXTURE.parent.mkdir(exist_ok=True)
    FIXTURE.write_text(json.dumps({"pid": pid, "encounter": encounter}))
    return {"pid": pid, "encounter": encounter}


def openemr_login(context: BrowserContext) -> None:
    """Log in, then make the fixture patient and encounter current for this session."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    page = context.new_page()
    page.goto(f"{base}/interface/login/login.php?site=default")
    page.fill("#authUser", os.environ["OPENEMR_USER"])
    page.fill("#clearPass", os.environ["OPENEMR_PASSWORD"])
    page.click("#login-button")
    page.wait_for_url("**/interface/main/**")
    ids = openemr_fixture(page, base)
    page.goto(f"{base}/interface/patient_file/summary/demographics.php?set_pid={ids['pid']}")
    page.goto(f"{base}/interface/patient_file/encounter/encounter_top.php?set_encounter={ids['encounter']}")
    page.close()


def open_contact(page) -> None:
    """OpenEMR collapses every section after Who; the address is under Contact."""
    page.get_by_role("button", name="Contact", exact=True).click()
    page.get_by_role("textbox", name="Street and Number", exact=True).wait_for()


OPENEMR_PATIENT = {"first_name": named("textbox", "First Name"), "last_name": named("textbox", "Last Name"),
                   "dob": named("textbox", "Date of Birth"), "sex": any_of(named("combobox", "Birth Sex"), named("combobox", "Sex")),
                   "street": [named("textbox", "Street and Number"), named("textbox", "Address Line 2")], "city": named("textbox", "City Name"),
                   "state": named("combobox", "State/Locality"), "postal_code": named("textbox", "Postal Code")}
# OpenEMR's vitals inputs have no labels; each sits in a table row named by the vital, its LOINC code and unit.
OPENEMR_VITALS = {"weight": in_row("textbox", "Weight (LOINC:29463-7) lbs *"),
                  "height": in_row("textbox", "Height/Length (LOINC:8302-2) in *"),
                  "bp_systolic": in_row("textbox", "BP Systolic (LOINC:8480-6) mmHg *"),
                  "bp_diastolic": in_row("textbox", "BP Diastolic (LOINC:8462-4) mmHg *"),
                  "pulse": in_row("textbox", "Pulse (LOINC:8867-4) per min *"),
                  "respiration": in_row("textbox", "Respiration (LOINC:9279-1) per min *"),
                  "temperature": in_row("textbox", "Temperature (LOINC:8310-5) F *"),
                  "temp_method": in_row("combobox", "Temp Location (LOINC:8327-9) *"),
                  "oxygen_saturation": in_row("textbox", "Oxygen Saturation (LOINC:59408-5) % *")}


# Filled by OpenEMR's scripts when the US-unit fields change.
OPENEMR_VITALS_DERIVED = [in_row("textbox", row) for row in (
    "Weight (LOINC:29463-7) kg", "Height/Length (LOINC:8302-2) cm", "Temperature (LOINC:8310-5) C",
    "Head Circumference (LOINC:9843-4) cm", "Waist Circumference (LOINC:9843-4) cm",
    "BMI (LOINC:39156-5) kg/m^2 *", "BMI Status Type *")]


def submit(page, form: str) -> None:
    """Submit an OpenEMR form directly: its own submit scripts need the tabbed shell around the page."""
    with page.expect_navigation():
        page.evaluate(f"document.querySelector({form!r}).submit()")


def save_patient(page) -> str:
    submit(page, "form[action*=new_comprehensive_save]")
    pid = parse_qs(urlparse(page.url).query).get("set_pid", [""])[0]
    return f"Created patient {pid} in OpenEMR" if pid else ""


def use_fixture_visit(page) -> None:
    """OpenEMR saves an encounter form to its current patient and visit, which saving a new patient changes:
    make the fixture's current again first (through the page's own session)."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    ids = json.loads(FIXTURE.read_text())
    page.request.get(f"{base}/interface/patient_file/summary/demographics.php?set_pid={ids['pid']}")
    page.request.get(f"{base}/interface/patient_file/encounter/encounter_top.php?set_encounter={ids['encounter']}")


def save_vitals(page) -> str:
    use_fixture_visit(page)
    submit(page, "form[name=vitals]")
    return f"Saved vitals to the Bench Fixture visit (encounter {json.loads(FIXTURE.read_text())['encounter']})"


def setup_labs(page, record: dict) -> None:
    """Order the panels the record has results for, on the fixture visit, after clearing earlier orders."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    labs.setup(page, base)
    labs.clear_orders(page, base)
    for panel in labs.panels_in(record):
        labs.place_order(page, base, panel)
    page.goto(f"{base}/interface/orders/orders_results.php?review=1")


def save_labs(page) -> str:
    """OpenEMR keeps results only under a report with a date: each empty report date takes its collection date."""
    use_fixture_visit(page)
    reports = page.get_by_role("textbox", name="Date and time of this report")
    collected = page.get_by_role("textbox", name="Date and time of sample collection")
    for k in range(reports.count()):
        if not reports.nth(k).input_value():
            reports.nth(k).fill(collected.nth(k).input_value() or time.strftime("%Y-%m-%d"))
    with page.expect_navigation():
        page.evaluate("f => f.requestSubmit(f.querySelector('[name=form_submit]'))",
                      page.locator("form:has([name=form_submit])").element_handle())
    return "Saved lab results to the Bench Fixture visit"


def openemr_home(page) -> None:
    """Sign in, and wait at OpenEMR's first screen. Credentials come from .env and never go to a model."""
    page.goto(f"{os.environ['OPENEMR_URL'].rstrip('/')}/interface/login/login.php?site=default",
              wait_until="domcontentloaded")
    if page.locator("#authUser").count():
        page.fill("#authUser", os.environ["OPENEMR_USER"])
        page.fill("#clearPass", os.environ["OPENEMR_PASSWORD"])
        page.click("#login-button")
    page.wait_for_url("**/interface/main/**")
    page.wait_for_load_state("load")


def order_panels(page, record: dict) -> None:
    """Test setup, not entry: results arrive for orders, so order the panels the note has results for at the
    visit open in the EMR. Done in a second tab of the same session."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    tab = page.context.new_page()
    try:
        labs.setup(tab, base)
        labs.clear_orders(tab, base)
        for panel in labs.panels_in(record):
            labs.place_order(tab, base, panel)
    finally:
        tab.close()


# Goals name the values they need in braces; emr.patient_of supplies them, and a goal is offered only those.
PATIENT_GOAL = "the patient {first name} {last name} (born {birth date})"


def openemr() -> Target:
    """OpenEMR's new-patient screen, the vitals form and the lab results screen. The bench opens them by URL;
    the console reaches them through OpenEMR's own menus and saves with its own buttons."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    return Target("openemr", [
        Screen(f"{base}/interface/new/new.php", OPENEMR_PATIENT, open_contact, title="New patient", save=save_patient,
               open=("Open the blank Search or Add Patient form, where a new patient is registered (Patient menu, "
                     "New/Search). Only open it: press no button on the form itself.",
                     "On the new patient form, open the collapsed section that holds the patient's address, so its "
                     "fields show. Type nothing."),
               save_goal="Create the new patient from the filled-in registration form, confirming if the EMR asks, "
                         "and stop once the new patient's chart is shown."),
        Screen(f"{base}/interface/forms/vitals/new.php", OPENEMR_VITALS, derived=OPENEMR_VITALS_DERIVED,
               title="Vitals", save=save_vitals, patient=True, visit=True,
               open=(f"Open the Vitals form, ready to record vital signs, inside today's visit ({{today}}) of "
                     f"{PATIENT_GOAL}. Do not create a new visit.",),
               save_goal="Save the vital signs filled in on the Vitals form, and stop once the EMR shows them saved."),
        Screen(f"{base}/interface/orders/orders_results.php?review=1", {}, title="Lab results", save=save_labs,
               setup=setup_labs, patient=True, visit=True,
               open=(f"Open today's visit ({{today}}) of {PATIENT_GOAL}. Do not create a new visit.", order_panels,
                     "From the Procedures menu, open Pending Review: the screen where results of the patient's "
                     "pending lab orders are entered."),
               save_goal="Save the lab results filled in on the screen with its Sign Results button. They are saved "
                         "once the screen no longer lists them for review; stop then.")],
        login=openemr_login, home=openemr_home,
        saves=[f"{base}/interface/new/new_comprehensive_save.php*", f"{base}/interface/forms/*/save.php*"],
        visit=f"Create a new office visit (encounter) for {PATIENT_GOAL} and save it. Keep every other field as "
              "it is. Stop once the visit is saved.",
        fixture={"first name": "Bench", "last name": "Fixture", "birth date": "1970-01-01"})


TARGETS = {"local": lambda: LOCAL, "openemr": openemr}
