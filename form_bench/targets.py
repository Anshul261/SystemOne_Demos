"""Where the agents type: a list of screens, each with the answer key the scorer reads back.

An answer key maps a case's field keys to controls, by ARIA role and accessible name or table row. Only the scorer
uses it; the agents under test see the page and the note, nothing else. Unscored fields are still open to
the agents, so a wrong entry there is not counted.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import BrowserContext


@dataclass(frozen=True)
class Screen:
    url: str
    answers: dict[str, dict | list[dict]]  # field key -> named(), in_row(), any_of(), or a list joined in order
    prepare: callable = None  # prepare(page): puts the screen in its starting state, the same for every agent
    derived: list[dict] = field(default_factory=list)  # fields the page fills itself, ignored as agent extras
    title: str = ""  # how the review console names the screen
    save: callable = None  # save(page) -> what was saved, used only by the review console after approval


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


def save_vitals(page) -> str:
    submit(page, "form[name=vitals]")
    return f"Saved vitals to the Bench Fixture visit (encounter {json.loads(FIXTURE.read_text())['encounter']})"


def openemr() -> Target:
    """OpenEMR's new-patient screen with Contact open, and the vitals screen of the fixture encounter."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    return Target("openemr", [Screen(f"{base}/interface/new/new.php", OPENEMR_PATIENT, open_contact,
                                     title="New patient", save=save_patient),
                              Screen(f"{base}/interface/forms/vitals/new.php", OPENEMR_VITALS,
                                     derived=OPENEMR_VITALS_DERIVED, title="Vitals (Bench Fixture visit)",
                                     save=save_vitals)],
                  login=openemr_login,
                  saves=[f"{base}/interface/new/new_comprehensive_save.php*", f"{base}/interface/forms/*/save.php*"])


TARGETS = {"local": lambda: LOCAL, "openemr": openemr}
