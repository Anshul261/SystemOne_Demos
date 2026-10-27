"""Benchmark cases from Synthea's synthetic patients: an intake note to type from, and the values it implies.

Each case pairs a clinician-style note with the expected form values (the scoring answer key). Notes come
from a few seeded templates, so runs are repeatable. A value the note leaves out is expected to stay empty,
which checks that an agent does not invent data. All patients are synthetic; never point this at real PHI.

Data: Synthea's published sample CSVs, unpacked into data/synthea/ (see README).
"""

import csv
import random
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DATA = Path(__file__).parent / "data" / "synthea"
# LOINC codes Synthea uses for vital signs, and the form field each fills.
VITALS = {"8480-6": "bp_systolic", "8462-4": "bp_diastolic", "8867-4": "pulse", "9279-1": "respiration",
          "8310-5": "temperature", "29463-7": "weight", "8302-2": "height", "2708-6": "oxygen_saturation"}
REQUIRED = {"bp_systolic", "bp_diastolic", "pulse", "respiration", "weight", "height"}
TEMP_METHODS = ["Oral", "Tympanic Membrane", "Axillary", "Temporal Artery"]


@dataclass(frozen=True)
class Case:
    id: str
    note: str
    expected: dict[str, str]  # field key -> value; "" means the field should stay empty


def clean(name: str) -> str:
    return re.sub(r"\d+", "", name).strip()


def rows(name: str) -> list[dict]:
    with (DATA / name).open(newline="") as handle:
        return list(csv.DictReader(handle))


def latest_vitals(observations: list[dict]) -> dict[str, dict[str, str]]:
    """Each patient's most recent encounter with every required vital, plus their latest temperature and SpO2, in US units."""
    encounters: dict[tuple[str, str], dict] = {}
    for row in observations:
        if row["CODE"] in VITALS:
            visit = encounters.setdefault((row["PATIENT"], row["ENCOUNTER"]), {"date": row["DATE"]})
            visit[VITALS[row["CODE"]]] = float(row["VALUE"])
    latest: dict[str, dict] = {}
    optional: dict[str, dict] = {}  # temperature and SpO2 are recorded at other visits: keep the latest of each
    for (patient, _), visit in sorted(encounters.items(), key=lambda item: item[1]["date"]):
        if REQUIRED <= visit.keys():
            latest[patient] = visit
        optional.setdefault(patient, {}).update({k: visit[k] for k in ("temperature", "oxygen_saturation") if k in visit})
    return {patient: us_units({**visit, **optional[patient]}) for patient, visit in latest.items()}


def us_units(visit: dict) -> dict[str, str]:
    values = {"bp_systolic": round(visit["bp_systolic"]), "bp_diastolic": round(visit["bp_diastolic"]),
              "pulse": round(visit["pulse"]), "respiration": round(visit["respiration"]),
              "weight": round(visit["weight"] * 2.20462, 1), "height": round(visit["height"] / 2.54, 1)}
    if "temperature" in visit:
        values["temperature"] = round(visit["temperature"] * 9 / 5 + 32, 1)
    if "oxygen_saturation" in visit:
        values["oxygen_saturation"] = round(visit["oxygen_saturation"])
    return {key: str(value) for key, value in values.items()}


def allergies_by_patient(allergies: list[dict]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for row in allergies:
        if row["STOP"] or "disposition" in row["DESCRIPTION"]:
            continue
        name = re.sub(r"\s*\((substance|product|organism)\)", "", row["DESCRIPTION"]).replace("Allergy to ", "")
        found.setdefault(row["PATIENT"], []).append(name.strip().lower())
    return found


def age(birthdate: str, on: date = date(2026, 10, 1)) -> int:
    born = date.fromisoformat(birthdate)
    return on.year - born.year - ((on.month, on.day) < (born.month, born.day))


def note(expected: dict[str, str], rng: random.Random) -> str:
    """A short intake note in one of three styles: dictation, shorthand, or a labelled list."""
    e = expected
    sex = {"Male": "male", "Female": "female"}[e["sex"]]
    vitals = [f"BP {e['bp_systolic']}/{e['bp_diastolic']}", f"HR {e['pulse']}", f"RR {e['respiration']}",
              f"weight {e['weight']} lb", f"height {e['height']} in"]
    if e["temperature"]:
        vitals.append(f"temp {e['temperature']} F ({e['temp_method'].lower()})")
    if e["oxygen_saturation"]:
        vitals.append(f"SpO2 {e['oxygen_saturation']}%")
    rng.shuffle(vitals)
    allergies = e["allergies"] or "no known allergies"
    style = rng.randrange(3)
    if style == 0:
        return (f"{e['first_name']} {e['last_name']} is a {age(e['dob'])}-year-old {sex}, born {e['dob']}. "
                f"Lives at {e['street']}, {e['city']}, {e['state']} {e['postal_code']}. "
                f"Vitals today: {', '.join(vitals)}. Allergies: {allergies}.")
    if style == 1:
        return (f"Pt {e['last_name']}, {e['first_name']}, {age(e['dob'])}{sex[0].upper()}, DOB {e['dob']}. "
                f"Addr {e['street']}, {e['city']} {e['state']} {e['postal_code']}. "
                f"{'; '.join(vitals)}. All: {allergies}.")
    lines = [f"Name: {e['first_name']} {e['last_name']}", f"Sex: {sex}", f"DOB: {e['dob']}",
             f"Address: {e['street']}, {e['city']}, {e['state']} {e['postal_code']}", *vitals,
             f"Allergies: {allergies}"]
    return "\n".join(lines)


def load_cases(limit: int = 20, seed: int = 7) -> list[Case]:
    rng = random.Random(seed)
    vitals = latest_vitals(rows("observations.csv"))
    allergies = allergies_by_patient(rows("allergies.csv"))
    cases = []
    for patient in rows("patients.csv"):
        if patient["DEATHDATE"] or patient["Id"] not in vitals or age(patient["BIRTHDATE"]) < 18:
            continue
        measured = vitals[patient["Id"]]
        expected = {
            "first_name": clean(patient["FIRST"]), "last_name": clean(patient["LAST"]), "dob": patient["BIRTHDATE"],
            "sex": "Male" if patient["GENDER"] == "M" else "Female", "street": patient["ADDRESS"],
            "city": patient["CITY"], "state": patient["STATE"], "postal_code": patient["ZIP"],
            "temperature": "", "temp_method": "", "oxygen_saturation": "", **measured,
            "allergies": ", ".join(sorted(set(allergies.get(patient["Id"], [])))),
        }
        if expected["temperature"]:
            expected["temp_method"] = rng.choice(TEMP_METHODS)
        cases.append(Case(patient["Id"][:8], note(expected, rng), expected))
        if len(cases) == limit:
            break
    return cases


if __name__ == "__main__":
    for case in load_cases(3):
        print(case.id, case.note, case.expected, sep="\n", end="\n\n")
