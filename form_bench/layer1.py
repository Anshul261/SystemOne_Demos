"""Layer 1: a visit note to a standard patient record, with no LLM.

Each kind of problem has one owner:
- Code finds mentions: quantities with their unit and the phrase around them, dates, name-shaped text and
  address-shaped text.
- Jev classifies and selects among them, always from a closed set: what a number measures, which date is the
  date of birth, which name is the patient's, which of two blood pressures is current.
- Code does what has a fixed answer: unit conversion to the EHR's units, name order conventions ("Smith, Anna";
  "Mr. Smith"), address parts (usaddress) and date formats.

record() returns the record in the EHR's units (lb, in, F), Jev's confidence in each value, and the values a
person should review. Keys: given, middle, family, birth_date, sex, street, unit, city, state, postal_code,
bp_systolic, bp_diastolic, pulse, respiration, temperature, temp_site, oxygen_saturation, weight, height.
"""

import os
import re
from datetime import date

import usaddress

from models import ask_all

# Flag a value Jev gives less than this: the highest threshold with no false alarm on the development notes
# (lowest right value 0.70). On the test notes it flagged all 5 inferred values and 6 of 1,442 right ones.
FLAG_BELOW = float(os.getenv("LAYER1_FLAG_BELOW", "0.7"))
# How Jev picks one mention from several (a name, a date, an address, a reading): "choice" lists them in one
# question, "reversed" lists them last first (to measure order bias), "noul" asks one yes/no per mention
# ("noul-top": the same, with confidence from the top yes alone, not lowered by a second yes), and
# "find" lists them in a Choice with no "none" option and asks one Noul whether the note states the value at all
# (TypeSafe's semantic-find pattern: a Choice always ranks something first, a Noul can fall near zero).
PICK = os.getenv("LAYER1_PICK", "choice")
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"])}
DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|\b[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}\b"
                   r"|\b\d{1,2} [A-Z][a-z]{2,8}\.? \d{4}\b")
PAIR = re.compile(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b|\b(\d{2,3}) over (\d{2,3})\b")
QUANTITY = re.compile(r"(?<![\w./-])(\d{1,3}(?:\.\d)?)\s*(kg|lbs?|cm|in(?:ches)?|°?\s?[CF]\b|%|bpm|/min)?(?![\w/])", re.I)
NAMES = [
    re.compile(r"\b(?:Mr|Ms|Mrs|Miss|Dr)\.? [A-Z][A-Za-z'-]+\b"),  # title and family name
    re.compile(r"\b[A-Z][A-Za-z'-]+, [A-Z][a-z'-]+(?: [A-Z]\.)?"),  # family name first, with a comma
    re.compile(r"\b[A-Z][a-z'-]+(?: [A-Z][a-z'-]+){1,2}\b"),  # given (middle) family
]
KINDS = {"weight": "the patient's body weight", "height": "the patient's height",
         "temperature": "the patient's body temperature", "pulse": "the patient's heart rate or pulse",
         "respiration": "the patient's respiratory rate", "oxygen_saturation": "the patient's oxygen saturation",
         "other": "anything else: an age, a dose, a duration, a count, part of an address or date, or a value "
                  "that is not about the patient"}
SITES = {"oral": "by mouth", "tympanic": "in the ear", "axillary": "under the arm", "temporal": "on the forehead",
         "rectal": "rectal", "none": "the note does not say where the temperature was taken"}
STATES = dict(zip(
    "Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Hawaii Idaho Illinois "
    "Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota Mississippi Missouri "
    "Montana Nebraska Nevada New_Hampshire New_Jersey New_Mexico New_York North_Carolina North_Dakota Ohio Oklahoma "
    "Oregon Pennsylvania Rhode_Island South_Carolina South_Dakota Tennessee Texas Utah Vermont Virginia Washington "
    "West_Virginia Wisconsin Wyoming".replace("_", "~").split(),
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA "
    "RI SC SD TN TX UT VT VA WA WV WI WY".split()))
STATES = {k.replace("~", " "): v for k, v in STATES.items()}
TO_EHR = {"kg": ("weight", lambda v: v * 2.20462), "cm": ("height", lambda v: v / 2.54),
          "c": ("temperature", lambda v: v * 9 / 5 + 32)}


def context(note: str, start: int, end: int, width: int = 45) -> str:
    """The words around a mention, so Jev can tell a current weight from last month's."""
    return note[max(0, start - width):end + width].replace("\n", " ").strip()


def iso_date(text: str) -> str | None:
    text = text.replace(".", "").replace(",", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text):
        return date(int(m[3]), int(m[1]), int(m[2])).isoformat()
    parts = text.split()
    if len(parts) == 3:
        if parts[0][:3].lower() in MONTHS:
            month, day, year = parts
        else:
            day, month, year = parts
        if month[:3].lower() in MONTHS:
            return date(int(year), MONTHS[month[:3].lower()], int(day)).isoformat()
    return None


def parse_name(text: str) -> dict:
    """Name order by convention: "Smith, Anna B." and "Mr. Smith" put the family name first or alone."""
    text = re.sub(r"^(Pt|Patient)\.?\s+", "", text.strip())
    if m := re.fullmatch(r"(?:Mr|Ms|Mrs|Miss|Dr)\.? ([A-Za-z'-]+)", text):
        return {"family": m[1].title() if m[1].isupper() else m[1]}
    if "," in text:
        family, rest = [p.strip() for p in text.split(",", 1)]
        words = rest.split()
        out = {"family": family.title() if family.isupper() else family, "given": words[0]}
        if len(words) > 1:
            out["middle"] = words[1]
        return out
    words = text.split()
    if len(words) == 2:
        return {"given": words[0], "family": words[1]}
    return {"given": words[0], "middle": " ".join(words[1:-1]), "family": words[-1]}


def addresses(note: str) -> list[tuple[str, dict]]:
    """Address-shaped text: from any house number to the end of its sentence ("Apt. 18" does not end it),
    parsed into parts by usaddress, trimmed at commas to the longest part that parses as a street address (so
    "..., Massachusetts 02131, in with cough" still parses). Every start is tried, so a date just before an
    address cannot hide it."""
    found = []
    dates = [(m.start(), m.end()) for m in DATES.finditer(note)]
    for start in re.finditer(r"(?<![\w/.-])(?=\d{1,6} [A-Z])", note):
        if any(a <= start.start() < b for a, b in dates):  # "21 Mar 1985" is a date, not a house number
            continue
        end = re.search(r"\.\s+(?=[A-Z][a-z])|[;\n]|$", note[start.start():])
        pieces = note[start.start():start.start() + end.start()].strip(" ,.")[:160].split(",")
        for k in range(len(pieces), 0, -1):  # the longest run of comma-separated pieces that is an address
            text = ",".join(pieces[:k]).strip(" ,.")
            try:
                tags, kind = usaddress.tag(text)
            except usaddress.RepeatedLabelError:
                continue
            if kind == "Street Address" and "StreetName" in tags and text.startswith(tags.get("AddressNumber", "#")):
                found.append((text, dict(tags)))
                break
    return found


def state_name(text: str) -> str | None:
    """A US state from a closed list: "MA" and "Massachusetts, zip" both give Massachusetts."""
    words = re.sub(r"[^A-Za-z ]", " ", text).split()
    for n in (2, 1):
        candidate = " ".join(words[:n])
        for name, abbreviation in STATES.items():
            if candidate.casefold() in (name.casefold(), abbreviation.casefold()):
                return name
    return None


def ask_pick(questions: dict, qid: str, what: str, candidates: dict, none: str | None) -> None:
    """Questions that pick which candidate is `what`. candidates maps each label to its context (or None);
    none, when given, is what it means that no candidate is."""
    if PICK.startswith("noul"):
        for i, (label, around) in enumerate(candidates.items()):
            questions[f"{qid}_{i}"] = {"type": "noul", "instructions": {
                "task": f"Is this {what}?", "mention": label} | ({"context": around["context"]} if around else {})}
        return
    labels = list(candidates)[::-1] if PICK == "reversed" else list(candidates)
    if PICK == "find" and none:
        questions[f"{qid}_exists"] = {"type": "noul", "instructions": f"Does the note state {what}?",
                                      "criteria": {"true": f"The note states {what}.", "false": none}}
        none = None
    questions[qid] = {"type": "choice", "instructions": f"Which of these is {what}?",
                      "criteria": {c: candidates[c] for c in labels} | ({"none": none} if none else {})}


def resolve(answers: dict, qid: str, candidates: dict) -> tuple[str, float]:
    """The picked candidate's label, or "none", and Jev's confidence in it. With nouls, a candidate is picked
    when Jev says yes to it, and the confidence also falls when Jev says yes to a second one."""
    if PICK.startswith("noul"):
        yes = sorted(((answers[f"{qid}_{i}"]["noul"], label) for i, label in enumerate(candidates)
                      if f"{qid}_{i}" in answers), reverse=True)
        if not yes or yes[0][0] < 0.5:
            return "none", 1 - yes[0][0] if yes else 1.0
        if PICK == "noul-top" or len(yes) == 1:
            return yes[0][1], yes[0][0]
        return yes[0][1], min(yes[0][0], 1 - yes[1][0])
    a = answers.get(qid)
    if not a:
        return "none", 1.0
    if f"{qid}_exists" in answers:
        exists = answers[f"{qid}_exists"]["noul"]
        return ("none", 1 - exists) if exists < 0.5 else (a["choice"], min(a["probabilities"][a["choice"]], exists))
    return a["choice"], a["probabilities"][a["choice"]]


def record(note: str) -> tuple[dict, dict, dict]:
    """The note's patient record in EHR units, Jev's confidence per key, and the keys to review (with why)."""
    questions: dict[str, dict] = {}
    names = {n: None for n in sorted({m[0] for p in NAMES for m in p.finditer(note)}, key=note.index)}
    if names:
        ask_pick(questions, "name", "the patient's own name", names, "None of these is the patient's name.")
    dates = {m[0]: None for m in DATES.finditer(note)}
    if dates:
        ask_pick(questions, "dob", "the patient's date of birth", dates, "The note does not give a date of birth.")
    questions["sex"] = {"type": "choice", "instructions": "What does the note state as the patient's sex?",
                        "criteria": {"male": None, "female": None}
                        | ({} if PICK == "find" else {"none": "The note does not state the patient's sex."})}
    if PICK == "find":
        questions["sex_exists"] = {"type": "noul", "instructions": "Does the note state the patient's sex?",
                                   "criteria": {"true": "The note states the patient's sex.",
                                                "false": "The note does not state the patient's sex."}}
    questions["site"] = {"type": "choice", "instructions": "Where was the patient's temperature taken?",
                         "criteria": SITES}
    places = addresses(note)
    if places:
        ask_pick(questions, "address", "the patient's home address", {t: None for t, _ in places},
                 "None is the patient's home address.")
    towns = {t: None for t in sorted({m[0] for m in re.finditer(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)?\b", note)} - set(STATES),
                                     key=note.index)}
    if towns:  # used only when the note gives no full address
        ask_pick(questions, "town", "the town or city the patient lives in", towns,
                 "The note does not say where the patient lives.")
    pairs = list(PAIR.finditer(note))
    bps = {m[0]: {"context": context(note, m.start(), m.end())} for m in pairs}
    if pairs:
        ask_pick(questions, "bp", "the blood pressure to chart as the patient's blood pressure today", bps,
                 "None of these is today's blood pressure.")
    taken = [(m.start(), m.end()) for m in pairs] + [(m.start(), m.end()) for m in DATES.finditer(note)]
    quantities = [m for m in QUANTITY.finditer(note)
                  if not any(a <= m.start() < b for a, b in taken) and not re.match(r"\d+\s*(mg|mcg|ml)", note[m.start():], re.I)]
    for k, m in enumerate(quantities):
        questions[f"q{k}"] = {"type": "choice", "criteria": KINDS, "instructions": {
            "task": "What does this number in the note measure?", "number": m[0],
            "context": context(note, m.start(), m.end())}}
    answers = ask_all(note, questions)

    out, conf, flags = {}, {}, {}

    def take(keys: dict, confidence: float, why: str = ""):
        out.update(keys)
        conf.update({k: confidence for k in keys})
        if confidence < FLAG_BELOW:
            flags.update({k: why or f"Jev is {confidence:.0%} sure of this" for k in keys})

    def blank(keys: list, confidence: float):  # left blank because Jev found no value: flag it if unsure
        conf.update({k: confidence for k in keys})
        if confidence < FLAG_BELOW:
            flags.update({k: f"Jev is {confidence:.0%} sure the note does not give this" for k in keys})

    def picked(qid):
        a = answers.get(qid)
        return (a["choice"], a["probabilities"][a["choice"]]) if a else ("none", 1.0)

    name, p = resolve(answers, "name", names)
    if name != "none":
        take(parse_name(name), p)
    elif names:
        blank(["given", "family"], p)
    dob, p = resolve(answers, "dob", dates)
    if dob != "none" and (iso := iso_date(dob)):
        take({"birth_date": iso}, p)
    elif dates and dob == "none":
        blank(["birth_date"], p)
    sex, p = resolve(answers, "sex", {"male": None, "female": None}) if PICK == "find" else picked("sex")
    if sex != "none":
        take({"sex": sex}, p)
    else:
        blank(["sex"], p)
    address, p = resolve(answers, "address", {t: None for t, _ in places})
    if address != "none":
        tags = dict(places)[address]
        street = " ".join(tags.get(k, "") for k in ("AddressNumber", "StreetNamePreDirectional", "StreetName",
                                                    "StreetNamePostType")).split()
        parts = {"street": " ".join(street)}
        if "OccupancyIdentifier" in tags:
            parts["unit"] = " ".join(filter(None, [tags.get("OccupancyType"), tags["OccupancyIdentifier"]])).replace("# ", "#")
        parts |= {k: tags[t] for k, t in (("city", "PlaceName"), ("postal_code", "ZipCode")) if t in tags}
        if "StateName" in tags and (state := state_name(tags["StateName"])):
            parts["state"] = state
        take(parts, p)
    else:
        if places:
            blank(["street"], p)
        town, p = resolve(answers, "town", towns)
        if town != "none":
            take({"city": town}, p)
        elif towns:
            blank(["city"], p)
    bp, p = resolve(answers, "bp", bps)
    if bp != "none":
        s_, d_ = [int(x) for x in re.findall(r"\d{2,3}", bp)]
        take({"bp_systolic": s_, "bp_diastolic": d_}, p)
    elif pairs:
        blank(["bp_systolic", "bp_diastolic"], p)

    # Quantities: one value per kind. Several of one kind (a weight, then its correction; this visit and the
    # last) go to a second request that asks which is current.
    by_kind: dict[str, list] = {}
    for k, m in enumerate(quantities):
        kind, p = picked(f"q{k}")
        if kind != "other":
            by_kind.setdefault(kind, []).append((m, p))
    readings = {kind: {f"{m[0]} ({i + 1})": {"context": context(note, m.start(), m.end())}
                       for i, (m, _) in enumerate(found)} for kind, found in by_kind.items() if len(found) > 1}
    second: dict[str, dict] = {}
    for kind, candidates in readings.items():
        ask_pick(second, f"cur_{kind}", f"{KINDS[kind]} to chart today", candidates, None)
    current = ask_all(note, second) if second else {}
    for kind, found in by_kind.items():
        if len(found) > 1:
            label, p = resolve(current, f"cur_{kind}", readings[kind])
            if label == "none":  # with nouls, Jev may say no to every reading: chart none
                continue
            index = int(label.rsplit("(", 1)[1].rstrip(")")) - 1
            m, p = found[index][0], min(found[index][1], p)
        else:
            m, p = found[0]
        value, unit = float(m[1]), (m[2] or "").lower().replace("°", "").strip()
        if unit in TO_EHR and TO_EHR[unit][0] == kind:
            value = TO_EHR[unit][1](value)
        take({kind: round(value, 1) if kind in ("weight", "height", "temperature") else round(value)}, p)
    if "temperature" in out:
        site, p = picked("site")
        if site != "none":
            take({"temp_site": site}, p)
    return out, conf, flags
