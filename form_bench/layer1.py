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

import re
from datetime import date

import usaddress

from models import ask_all

# Flag a value, or a blank, Jev gives less than this. Set on the development notes (notes_v2 dev and notes_v3):
# 0.6 catches every error a threshold can (3 of 7; the others had 0.85 or more) with 6 false alarms in 2,679
# right fields; 0.7 adds 14 false alarms and catches no more.
FLAG_BELOW = 0.6
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                          "nov", "dec"])}
DATES = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b|\b[A-Z][a-z]{2,8}\.? \d{1,2},? \d{4}\b"
                   r"|\b\d{1,2} [A-Z][a-z]{2,8}\.? \d{4}\b")
PAIR = re.compile(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b|\b(\d{2,3}) over (\d{2,3})\b")
QUANTITY = re.compile(r"(?<![\w./-])(\d{1,3}(?:\.\d)?)\s*(kg|lbs?|cm|in(?:ches)?|°?\s?[CF]\b|%|bpm|/min)?(?![\w/])", re.I)
WORD = r"[A-Z][a-z'-]+(?:[A-Z][a-z'-]+)?"  # a capitalised name word, also "McClure" and "DeShawn"
NAMES = [  # matched at every start, so "Dictation, Ullrich, Noriko" also gives "Ullrich, Noriko"
    re.compile(rf"(?=\b((?:Mr|Ms|Mrs|Miss|Dr)\.? (?:{WORD}|[A-Z]+)\b))"),  # title and family name
    re.compile(rf"(?=\b((?:{WORD}|[A-Z]{{2,}}), {WORD}(?: [A-Z]\.)?))"),  # family name first, with a comma
    re.compile(rf"(?=\b({WORD}(?: {WORD}){{1,2}}\b))"),  # given (middle) family
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
# The charting rule for readings, from what a reading is, not from any one note's wording.
READING = ("Which of these is {what} measured at this visit? If it was measured or stated more than once for "
           "this visit, the latest or corrected value is the one to chart.")
NOT_TODAY = "None of these was measured at this visit."
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
    """Address-shaped text: from any house number to its ZIP code, or else the end of its sentence or a
    bracket ("Apt. 18" does not end it),
    parsed into parts by usaddress, trimmed at commas to the longest part that parses as a street address (so
    "..., Massachusetts 02131, in with cough" still parses). Every start is tried, so a date just before an
    address cannot hide it."""
    found = []
    dates = [(m.start(), m.end()) for m in DATES.finditer(note)]
    for start in re.finditer(r"(?<![\w/.-])(?=\d{1,6} [A-Z])", note):
        if any(a <= start.start() < b for a, b in dates):  # "21 Mar 1985" is a date, not a house number
            continue
        end = re.search(r"\.\s+(?=[A-Z])|[;\n()]|(?<=\b\d{5})(?![\d-])|$", note[start.start():])
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


def mentions(note: str, matches) -> dict:
    """Each distinct mention, in note order, with the words around its first appearance."""
    found = {}
    for text, a, b in matches:
        found.setdefault(text, {"context": context(note, a, b)})
    return found


def pick(questions: dict, qid: str, instructions: str, candidates: dict, none: str) -> None:
    """One Choice: which mention is the one asked for, or none of them. A Choice, not a Noul per mention: judged
    alone, a mention like "Patient" can look like a town (notes_v3/README.md)."""
    questions[qid] = {"type": "choice", "instructions": instructions, "criteria": candidates | {"none": none}}


def record(note: str) -> tuple[dict, dict, dict]:
    """The note's patient record in EHR units, Jev's confidence per key, and the keys to review (with why)."""
    questions: dict[str, dict] = {}
    names = mentions(note, sorted(((m[1], m.start(1), m.end(1)) for p in NAMES for m in p.finditer(note)),
                                  key=lambda t: t[1]))
    if names:
        pick(questions, "name", "Which of these is the patient's own name?", names, "None of these is the patient's name.")
    dates = mentions(note, ((m[0], m.start(), m.end()) for m in DATES.finditer(note)))
    if dates:
        pick(questions, "dob", "Which of these is the patient's date of birth?", dates,
             "The note does not give a date of birth.")
    pick(questions, "sex", "What does the note state as the patient's sex?", {"male": None, "female": None},
         "The note does not state the patient's sex.")
    questions["site"] = {"type": "choice", "instructions": "Where was the patient's temperature taken?",
                         "criteria": SITES}
    places = addresses(note)
    if places:
        pick(questions, "address", "Which of these is the patient's home address now?",
             mentions(note, ((t, note.index(t), note.index(t) + len(t)) for t, _ in places)),
             "None is the patient's home address now.")
    towns = {t: c for t, c in mentions(note, ((m[0], m.start(), m.end())
                                              for m in re.finditer(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)?\b", note))).items()
             if t not in STATES}
    if towns:  # used only when the note gives no full address
        pick(questions, "town", "Which of these is the town or city the patient lives in now?", towns,
             "The note does not say where the patient lives now.")
    pairs = list(PAIR.finditer(note))
    bps = mentions(note, ((m[0], m.start(), m.end()) for m in pairs))
    if pairs:
        pick(questions, "bp", READING.format(what="the patient's blood pressure"), bps, NOT_TODAY)
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

    def picked(qid, replies=answers):
        a = replies.get(qid)
        return (a["choice"], a["probabilities"][a["choice"]]) if a else ("none", 1.0)

    name, p = picked("name")
    if name != "none":
        take(parse_name(name), p)
    elif names:
        blank(["given", "family"], p)
    dob, p = picked("dob")
    if dob != "none" and (iso := iso_date(dob)):
        take({"birth_date": iso}, p)
    elif dates and dob == "none":
        blank(["birth_date"], p)
    sex, p = picked("sex")
    if sex != "none":
        take({"sex": sex}, p)
    else:
        blank(["sex"], p)
    address, p = picked("address")
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
        town, p = picked("town")
        if town != "none":
            take({"city": town}, p)
        elif towns:
            blank(["city"], p)
    bp, p = picked("bp")
    if bp != "none":
        s_, d_ = [int(x) for x in re.findall(r"\d{2,3}", bp)]
        take({"bp_systolic": s_, "bp_diastolic": d_}, p)
    elif pairs:
        blank(["bp_systolic", "bp_diastolic"], p)

    # Quantities: what each number measures, then, per kind, which reading was taken at this visit. A lone
    # reading is asked about too: it may be from home, the last visit, or a goal.
    by_kind: dict[str, list] = {}
    for k, m in enumerate(quantities):
        kind, p = picked(f"q{k}")
        if kind != "other":
            by_kind.setdefault(kind, []).append((m, p))
    readings = {kind: {f"{m[0]} ({i + 1})": {"context": context(note, m.start(), m.end())}
                       for i, (m, _) in enumerate(found)} for kind, found in by_kind.items()}
    second: dict[str, dict] = {}
    for kind, candidates in readings.items():
        pick(second, f"cur_{kind}", READING.format(what=KINDS[kind]), candidates, NOT_TODAY)
    current = ask_all(note, second) if second else {}
    for kind, found in by_kind.items():
        label, p = picked(f"cur_{kind}", current)
        if label == "none":
            blank([kind], p)
            continue
        m, p0 = found[int(label.rsplit("(", 1)[1].rstrip(")")) - 1]
        p = min(p0, p)
        value, unit = float(m[1]), (m[2] or "").lower().replace("°", "").strip()
        if unit in TO_EHR and TO_EHR[unit][0] == kind:
            value = TO_EHR[unit][1](value)
        take({kind: round(value, 1) if kind in ("weight", "height", "temperature") else round(value)}, p)
    if "temperature" in out:
        site, p = picked("site")
        if site != "none":
            take({"temp_site": site}, p)
    return out, conf, flags
