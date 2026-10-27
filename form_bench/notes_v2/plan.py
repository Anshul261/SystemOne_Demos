"""Plans for the messy note set: what each note must say, and exactly how, so the answer key is right by
construction.

    uv run python notes_v2/plan.py        # writes notes_v2/plans.jsonl and notes_v2/batches/plan-*.json

For every adult Synthea patient, two plans are drawn with a fixed seed. A plan holds the patient's canonical
record (the answer key, in the EHR's units), a writing style, and a list of facts. Each fact gives the exact
text the note must contain, such as "82.0 kg" or "Kris, Franklin". Hard features are tagged per note
(name order, date formats, units to convert, repeated readings, corrections, distractors, negations,
omissions) so results can be broken down by feature. A writer (see WRITER.md) turns a plan into a note
without inventing or changing any value; check.py then confirms every planned text appears.

Patients are split once: the first 20 (by Synthea order) are development, the rest are the test set, which
is never used for tuning.
"""

import json
import random
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cases import age, allergies_by_patient, clean, latest_vitals, rows  # noqa: E402

HERE = Path(__file__).parent
SEED = 2026
DEV_PATIENTS = 20
STYLES = {
    "dictation": "a dictated clinic note transcribed by speech recognition: full sentences, a few harmless "
                 "transcription slips in ordinary words (never in the planned texts)",
    "soap": "a SOAP note with Subjective, Objective, Assessment and Plan sections",
    "triage": "a terse emergency triage note: abbreviations, semicolons, no full sentences",
    "intake": "a nurse intake form typed as labelled lines, some labels abbreviated",
    "referral": "a short referral letter from one clinic to another, in prose",
    "handoff": "a quick shift-handoff message, informal, as one paragraph",
}
STREET_SHORT = {"Street": "St", "Avenue": "Ave", "Road": "Rd", "Boulevard": "Blvd", "Drive": "Dr", "Lane": "Ln",
                "Court": "Ct", "Place": "Pl", "Terrace": "Ter", "Parkway": "Pkwy"}
UNIT = re.compile(r"^(.*?)\s+((?:Apt|Unit|Suite)\s+\S+)$")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
RELATIVES = [("daughter", "Maria Lopez"), ("son", "David Chen"), ("wife", "Linda Park"), ("husband", "Omar Haddad"),
             ("neighbour", "Ruth Adler")]


def medication(description: str) -> dict:
    """What a clinician would write for a Synthea (RxNorm) description: a pack or branded product by its
    brand, otherwise the first ingredient and its strength. Devices and packs carry no dose."""
    if brand := re.search(r"\[([^\]]+)\]", description):
        return {"name": re.sub(r"\s+\d+\s+Day$", "", brand[1]).lower(), "dose": ""}
    text = re.sub(r"^(NDA\d+\s+)?(\d+(\.\d+)?\s+(HR|ACTUAT|DAY|ML)\s+)?", "", description)
    match = re.match(r"([A-Za-z][\w ,-]*?)\s+([\d.]+)\s*(MG|MCG|UNT)(/)?", text)
    if not match:
        return {"name": text.split(" ")[0].lower(), "dose": ""}
    return {"name": match[1].strip().lower(), "dose": "" if match[4] else f"{match[2]} {match[3].lower()}"}


def medications_by_patient() -> dict[str, list[dict]]:
    """Each patient's active medications, as a clinician would write them."""
    found: dict[str, list[dict]] = {}
    for row in rows("medications.csv"):
        entry = medication(row["DESCRIPTION"])
        if not row["STOP"] and entry not in found.setdefault(row["PATIENT"], []):
            found[row["PATIENT"]].append(entry)
    return found


def record(patient: dict, vitals: dict, allergies: list[str], meds: list[dict]) -> dict:
    """The canonical record: what the EHR should hold. Vitals are in the EHR's units (lb, in, F)."""
    street, unit = patient["ADDRESS"], ""
    if match := UNIT.match(street):
        street, unit = match[1], match[2]
    return {
        "given": clean(patient["FIRST"]), "middle": clean(patient["MIDDLE"]), "family": clean(patient["LAST"]),
        "birth_date": patient["BIRTHDATE"], "sex": "male" if patient["GENDER"] == "M" else "female",
        "street": street, "unit": unit, "city": patient["CITY"], "state": patient["STATE"],
        "postal_code": patient["ZIP"], **{k: float(v) for k, v in vitals.items()},
        "allergies": sorted(set(allergies)), "medications": meds[:4],
    }


def us_date(iso: str, style: str) -> str:
    d = date.fromisoformat(iso)
    return {"iso": iso, "us": f"{d.month}/{d.day}/{d.year}", "us0": f"{d.month:02d}/{d.day:02d}/{d.year}",
            "long": f"{MONTHS[d.month - 1]} {d.day}, {d.year}", "dmy": f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"}[style]


def plan(r: dict, rng: random.Random, note_id: str, birthdate: str) -> dict:
    """One note's plan: the expected record (what the note states, in EHR units) and the facts to write."""
    facts, features, expected, acceptable = [], [], {}, {}

    def fact(field, text, how="", value=None):
        facts.append({"field": field, "text": text, **({"how": how} if how else {})})
        if value is not None:
            expected[field] = value

    # Name
    style = rng.choice(["first last", "last, first", "first middle last", "LAST, First M.", "title last", "last, first"])
    sex_word = r["sex"]
    if style == "first last":
        fact("name", f"{r['given']} {r['family']}", value=None)
        expected |= {"given": r["given"], "family": r["family"]}
    elif style == "last, first":
        fact("name", f"{r['family']}, {r['given']}", "family name first, then a comma")
        expected |= {"given": r["given"], "family": r["family"]}
        features.append("name_last_first")
    elif style == "first middle last" and r["middle"]:
        fact("name", f"{r['given']} {r['middle']} {r['family']}")
        expected |= {"given": r["given"], "middle": r["middle"], "family": r["family"]}
        features.append("name_middle")
    elif style == "LAST, First M." and r["middle"]:
        fact("name", f"{r['family'].upper()}, {r['given']} {r['middle'][0]}.", "as written, capitals and all")
        expected |= {"given": r["given"], "middle": f"{r['middle'][0]}.", "family": r["family"]}
        features.append("name_last_first")
    else:
        title = "Mr." if sex_word == "male" else "Ms."
        fact("name", f"{title} {r['family']}", "the only time the patient is named")
        expected |= {"family": r["family"]}
        features.append("name_family_only")

    # Date of birth, or age only
    dob_style = rng.choice(["iso", "us", "us0", "long", "dmy", "age_only"])
    if dob_style == "age_only":
        fact("age", f"{age(birthdate)}-year-old", "age only; give no date of birth anywhere")
        features.append("dob_absent")
    else:
        fact("birth_date", us_date(birthdate, dob_style), "as the date of birth", value=birthdate)
        if dob_style != "iso":
            features.append("dob_" + dob_style)

    # Sex
    sex_style = rng.choice(["word", "shorthand", "word", "absent"])
    if sex_style == "word":
        fact("sex", sex_word, value=sex_word)
    elif sex_style == "shorthand":
        fact("sex", f"{age(birthdate)}{'M' if sex_word == 'male' else 'F'}", "age and sex shorthand", value=sex_word)
        features.append("sex_shorthand")
    else:
        facts.append({"field": "sex", "text": "", "how": "do not state sex, and use no gendered words or pronouns"})
        features.append("sex_absent")
        if style == "title last" or (style not in ("first last", "last, first") and not r["middle"]):
            acceptable["sex"] = [None, sex_word]  # "Mr." or "Ms." implies it: blank or that sex are both right

    # Address
    addr_style = rng.choice(["full", "abbreviated", "unit_variant", "city_only", "absent", "full"])
    street = r["street"]
    if addr_style == "abbreviated":
        last = street.split()[-1]
        if last in STREET_SHORT:
            street = street[: -len(last)] + STREET_SHORT[last]
            features.append("street_abbreviated")
    unit = r["unit"]
    if unit and addr_style == "unit_variant":
        number = unit.split()[-1]
        unit = rng.choice([f"#{number}", f"Apt. {number}", f"{unit.split()[0]} {number}".lower()])
        features.append("unit_variant")
    if addr_style in ("full", "abbreviated", "unit_variant"):
        fact("street", street, "street address", value=r["street"])
        if r["unit"]:
            fact("unit", unit, "the apartment or unit, right after the street", value=r["unit"])
        fact("city", r["city"], value=r["city"])
        fact("state", r["state"], value=r["state"])
        fact("postal_code", r["postal_code"], value=r["postal_code"])
    elif addr_style == "city_only":
        fact("city", r["city"], "only the town the patient lives in; no street, state or ZIP", value=r["city"])
        features.append("address_partial")
    else:
        features.append("address_absent")

    # Vitals: each present or not, in a chosen form
    if "bp_systolic" in r and rng.random() < 0.9:
        s, d_ = int(r["bp_systolic"]), int(r["bp_diastolic"])
        form = rng.choice(["BP {s}/{d}", "BP {s}/{d} mmHg", "{s} over {d}", "BP {s}/{d}"])
        if rng.random() < 0.2:  # a first reading, then the repeat to chart
            first = f"{s + rng.randint(12, 25)}/{d_ + rng.randint(6, 12)}"
            fact("bp_first", first, "the first blood pressure reading, taken on arrival")
            fact("bp", form.format(s=s, d=d_), "the repeat blood pressure after rest; this is the one charted")
            features.append("bp_repeat")
        else:
            fact("bp", form.format(s=s, d=d_), "blood pressure")
            if "over" in form:
                features.append("bp_spoken")
        expected |= {"bp_systolic": s, "bp_diastolic": d_}
        if rng.random() < 0.15:
            fact("bp_previous", f"{s + 18}/{d_ + 9}", "the blood pressure at the PREVIOUS visit, clearly labelled so")
            features.append("distractor_previous_vitals")
    for key, words in (("pulse", ["HR {v}", "pulse {v}", "P {v}", "heart rate {v}"]),
                       ("respiration", ["RR {v}", "resp {v}", "respirations {v}"]),
                       ("oxygen_saturation", ["SpO2 {v}%", "O2 sat {v}% on room air", "sat {v}% RA"])):
        if key in r and rng.random() < 0.8:
            fact(key, rng.choice(words).format(v=int(r[key])), value=int(r[key]))
    if "temperature" in r and rng.random() < 0.8:
        site = rng.choice(["oral", "tympanic", "axillary", "temporal"])
        if rng.random() < 0.3:
            fact("temperature", f"{(r['temperature'] - 32) * 5 / 9:.1f} C", f"temperature, taken {site}")
            features.append("temp_celsius")
        else:
            fact("temperature", f"{r['temperature']} F", f"temperature, taken {site}")
        expected |= {"temperature": r["temperature"], "temp_site": site}
    if "weight" in r and rng.random() < 0.85:
        if rng.random() < 0.3:
            fact("weight", f"{r['weight'] / 2.20462:.1f} kg", "body weight")
            features.append("weight_kg")
        elif rng.random() < 0.15:
            wrong = round(r["weight"] + rng.choice([-1, 1]) * rng.randint(20, 60), 1)
            fact("weight_wrong", f"{wrong} lb", "a weight that is then corrected")
            fact("weight", f"{r['weight']} lb", "the corrected weight, introduced as a correction")
            features.append("correction")
        else:
            fact("weight", f"{r['weight']} lb", "body weight")
        expected["weight"] = r["weight"]
    if "height" in r and rng.random() < 0.7:
        if rng.random() < 0.3:
            fact("height", f"{r['height'] * 2.54:.0f} cm", "height")
            features.append("height_cm")
            expected["height"] = round(round(r["height"] * 2.54) / 2.54, 1)
        else:
            fact("height", f"{r['height']} in", "height")
            expected["height"] = r["height"]

    # Allergies and medications
    if r["allergies"]:
        fact("allergies", ", ".join(r["allergies"][:4]), "the patient's allergies", value=r["allergies"][:4])
    else:
        phrase = rng.choice(["NKDA", "no known allergies", "denies any allergies"])
        fact("allergies", phrase, "the patient has no allergies", value=[])
        features.append("allergy_negation")
    meds = r["medications"]
    if meds:
        current = meds[:3]
        fact("medications", "; ".join(f"{m['name']} {m['dose']}".strip() for m in current), "current medications",
             value=[m["name"] for m in current])
        if rng.random() < 0.3:
            fact("stopped_medication", "atorvastatin 40 mg", "a medication the patient STOPPED last month")
            features.append("stopped_medication")

    # Distractors: a relative's name, another date
    if rng.random() < 0.3:
        relation, name = rng.choice(RELATIVES)
        fact("relative", f"{relation} {name}", "a relative who came along; mention their name")
        features.append("distractor_person")
    if rng.random() < 0.3:
        fact("visit_date", "10/20/2026", "the date of the visit")
        features.append("distractor_date")

    return {"id": note_id, "style": None, "features": sorted(set(features)), "facts": facts, "expected": expected,
            "acceptable": acceptable}


def main():
    vitals = latest_vitals(rows("observations.csv"))
    allergies = allergies_by_patient(rows("allergies.csv"))
    meds = medications_by_patient()
    rng = random.Random(SEED)
    plans = []
    adults = [p for p in rows("patients.csv")
              if not p["DEATHDATE"] and p["Id"] in vitals and age(p["BIRTHDATE"]) >= 18]
    for n, patient in enumerate(adults):
        r = record(patient, {k: v for k, v in vitals[patient["Id"]].items()}, allergies.get(patient["Id"], []),
                   meds.get(patient["Id"], []))
        styles = rng.sample(list(STYLES), 2)
        for k, style in enumerate(styles):
            p = plan(r, rng, f"{patient['Id'][:8]}-{k}", patient["BIRTHDATE"])
            p["style"] = style
            p["split"] = "dev" if n < DEV_PATIENTS else "test"
            plans.append(p)
    (HERE / "plans.jsonl").write_text("".join(json.dumps(p) + "\n" for p in plans))
    batches = HERE / "batches"
    batches.mkdir(exist_ok=True)
    size = 20
    for b in range(0, len(plans), size):
        (batches / f"plan-{b // size}.json").write_text(json.dumps(plans[b:b + size], indent=1))
    features = {}
    for p in plans:
        for f in p["features"]:
            features[f] = features.get(f, 0) + 1
    print(f"{len(plans)} plans ({sum(p['split'] == 'dev' for p in plans)} dev, "
          f"{sum(p['split'] == 'test' for p in plans)} test) in {len(range(0, len(plans), size))} batches")
    print("features:", dict(sorted(features.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
