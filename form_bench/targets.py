"""Where the agents type: a list of screens, each with the answer key the scorer reads back.

An answer key maps a case's field keys to the control's ARIA role and accessible name. Only the scorer
uses it; the agents under test see the page and the note, nothing else. Unscored fields are still open to
the agents, so a wrong entry there is not counted.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import BrowserContext


@dataclass(frozen=True)
class Screen:
    url: str
    answers: dict[str, tuple]  # field key -> (role, accessible name) or (role, name, nth) for a repeated name
    prepare: callable = None  # prepare(page): puts the screen in its starting state, the same for every agent


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


PATIENT = {"first_name": ("textbox", "First Name"), "last_name": ("textbox", "Last Name"),
           "dob": ("textbox", "DOB"), "sex": ("combobox", "Sex"), "street": ("textbox", "Address"),
           "city": ("textbox", "City"), "state": ("combobox", "State"), "postal_code": ("textbox", "Postal Code")}
VITALS = {"weight": ("spinbutton", "Weight"), "height": ("spinbutton", "Height"),
          "bp_systolic": ("spinbutton", "BP Systolic"), "bp_diastolic": ("spinbutton", "BP Diastolic"),
          "pulse": ("spinbutton", "Pulse"), "respiration": ("spinbutton", "Respiration"),
          "temperature": ("spinbutton", "Temperature"), "temp_method": ("combobox", "Temp Location"),
          "oxygen_saturation": ("spinbutton", "Oxygen Saturation")}

LOCAL = Target("local", [Screen((Path(__file__).parent / "fixtures" / "intake.html").as_uri(),
                                {**PATIENT, **VITALS, "allergies": ("textbox", "Allergies")})])


def openemr_login(context: BrowserContext) -> None:
    page = context.new_page()
    page.goto(f"{os.environ['OPENEMR_URL']}/interface/login/login.php?site=default")
    page.fill("#authUser", os.environ["OPENEMR_USER"])
    page.fill("#clearPass", os.environ["OPENEMR_PASSWORD"])
    page.click("#login-button")
    page.wait_for_url("**/interface/main/**")
    page.close()


def open_contact(page) -> None:
    """OpenEMR collapses every section after Who; the address is under Contact."""
    page.get_by_role("button", name="Contact", exact=True).click()
    page.get_by_role("textbox", name="Street and Number", exact=True).wait_for()


OPENEMR_PATIENT = {"first_name": ("textbox", "First Name"), "last_name": ("textbox", "Last Name"),
                   "dob": ("textbox", "Date of Birth"), "sex": ("combobox", "Birth Sex"),
                   "street": ("textbox", "Street and Number"), "city": ("textbox", "City Name"),
                   "state": ("combobox", "State/Locality"), "postal_code": ("textbox", "Postal Code")}


def openemr() -> Target:
    """OpenEMR's new-patient screen, with Contact open. The vitals screen needs a patient and an encounter
    to exist first, so it is added once those are set up."""
    base = os.environ["OPENEMR_URL"].rstrip("/")
    return Target("openemr", [Screen(f"{base}/interface/new/new.php", OPENEMR_PATIENT, open_contact)],
                  login=openemr_login,
                  saves=[f"{base}/interface/new/new_comprehensive_save.php*", f"{base}/interface/forms/*/save.php*"])


TARGETS = {"local": lambda: LOCAL, "openemr": openemr}
