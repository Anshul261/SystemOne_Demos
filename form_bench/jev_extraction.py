"""Workflow 1 with Jev alone: extract checked values for one EHR screen's fields with no LLM.

Same inputs and outputs as extraction.extract, so the bench and console can swap them. Follows TypeSafe's
value-extraction guidance (docs.typesafe.ai: pre-parsed value extraction, Choice, Noul, confidence):

- Code lists candidate spans from the note (every short run of words between punctuation; numbers alone for a
  field with a unit; dates for a date field). Each candidate carries the phrase it came from, so "72" reads as
  "72, from 'HR 72'". Jev picks one per field; code copies it and normalizes it. Nothing is generated.
- A separate Noul per field asks whether the note states that field at all: the docs advise this over relying
  on a "none of these" option.
- Jev leans toward the first option, so each field is asked twice with the candidates in opposite orders.
  Disagreement is flagged.
- Dropdowns are a Choice over the field's own options, kept only when the note states the field.
- A second request checks each picked value is stated, as in extraction.py. Anything unsure is flagged for
  the reviewer.
"""

import re

from forms import label
from models import ask_all

SEGMENT = re.compile(r"[,;\n()]|\.\s|:\s")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
COMMA_PAIR = re.compile(r"\b([A-Z][\w'-]+), ([A-Z][\w'-]+)\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b")
MEASURED = re.compile(r"(?<![\w/])(?:lbs?|in|cm|kg|mmHg|per min|%|F|C)(?![\w/])")
NONE = "none"
PRESENT = 0.7  # the docs' starting point for a confident yes
ABSENT = 0.3  # and for a confident no; between the two, a person decides
STATED = 0.5  # a picked value Jev cannot confirm is flagged
# Field-specific guidance, matched on the field's label. Only the relevant entry goes to each question.
GUIDES = [
    (r"\b(first|last|middle) name\b|^name\b",
     "Clinical notes often write the family name first with a comma: in 'Pt Smith, Anna' or 'Smith, Anna' the "
     "last name is Smith, written before the comma, and the first name is Anna, written after it. Without a comma, "
     "as in 'Anna Smith is ...', the first name comes first."),
    (r"\bbirth (first|last) name\b|\bbirth name\b",
     "Only a name the note gives as the name at birth, such as 'born Anna Lee' or 'nee Lee'."),
    (r"preferred name|commonly known",
     "Only a name the note says the patient goes by or prefers, such as 'goes by Gracie'. Not their legal name."),
    (r"address line 2", "Only an apartment, unit, suite or floor, such as 'Apt 4', when the note gives one."),
    (r"street", "The house number and street name only, without an apartment or unit."),
    (r"\bsex\b", "Shorthand such as '47F' or '73M' gives the age and sex: female or male."),
    (r"\(abn\)", "An abnormal-result flag. Only a flag the note states in words, such as 'high' or 'abnormal'."),
]


def segments(note: str) -> list[str]:
    return [s.strip() for s in SEGMENT.split(note) if s.strip()]


def candidates(note: str, kind: str) -> dict[str, str]:
    """Candidate spans for one kind of field, each described by the phrase it came from."""
    found: dict[str, list[str]] = {}
    for segment in segments(note):
        if kind == "number":
            spans = NUMBER.findall(segment)
        elif kind == "date":
            spans = DATE.findall(segment)
        else:
            words = [w for w in (w.strip(".:%") for w in segment.split()) if w]
            spans = [" ".join(words[k:k + n]) for n in range(1, 7) for k in range(len(words) - n + 1)]
        for span in spans:
            found.setdefault(span, [])
            if segment not in found[span]:
                found[span].append(segment)
    described = {span: "from: " + "; ".join(f"'{s}'" for s in where) for span, where in list(found.items())[:250]}
    if kind == "text":  # mark "Word, Word" pairs: in 'Kris, Franklin' the order is the clue to which name is which
        for pair in COMMA_PAIR.finditer(note):
            for word, side in ((pair[1], "before"), (pair[2], "after")):
                if word in described:
                    described[word] += f"; written {side} the comma in '{pair[0]}'"
    return described


def kind_of(field: dict) -> str:
    text = label(field)
    if field["role"] == "spinbutton" or MEASURED.search(text) or re.search(r"postal|zip", text, re.I):
        return "number"
    if re.search(r"\bdate\b|\bdob\b", text, re.I):
        return "date"
    return "text"


def normalize(field: dict, span: str) -> str:
    """Fit a picked span to its field: US dates become YYYY-MM-DD."""
    if us := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", span):
        return f"{us[3]}-{int(us[1]):02d}-{int(us[2]):02d}"
    return span


def guidance(field: dict) -> list[str]:
    text = label(field)
    return [guide for pattern, guide in GUIDES if re.search(pattern, text, re.I)]


def extract(note: str, snapshot: str, fields: list[dict]
            ) -> tuple[dict[int, str | int], dict[int, str], dict[int, float]]:
    """The values to enter (option indexes for dropdowns), the fields to review with the reason, and a
    confidence per field. snapshot is unused: Jev sees only the note and each field's own description."""
    dropdowns = [i for i, f in enumerate(fields) if f["role"] == "combobox" and len(f["options"]) > 1]
    inputs = [i for i, f in enumerate(fields) if f["role"] != "combobox"]
    pools = {kind: candidates(note, kind) for kind in ("text", "number", "date")}
    questions = {}
    for i in inputs + dropdowns:
        field = {"name": label(fields[i])} | ({"guidance": g} if (g := guidance(fields[i])) else {})
        questions[f"p{i}"] = {"type": "noul", "instructions": {
            "task": "Does the note state this field's value for the patient?", "field": field}}
    for i in inputs:
        pool = pools[kind_of(fields[i])]
        task = "Which text from `note` is this field's value? Choose none if the note does not give it."
        field = {"name": label(fields[i])} | ({"guidance": g} if (g := guidance(fields[i])) else {})
        forward = pool | {NONE: "The note does not give this field's value."}
        backward = dict(reversed(list(pool.items()))) | {NONE: forward[NONE]}
        for tag, criteria in (("a", forward), ("b", backward)):
            questions[f"{tag}{i}"] = {"type": "choice", "instructions": {"task": task, "field": field},
                                      "criteria": criteria}
    for i in dropdowns:
        field = {"name": label(fields[i])} | ({"guidance": g} if (g := guidance(fields[i])) else {})
        criteria = {str(k): text or "(blank: the note does not say)" for k, text in enumerate(fields[i]["options"])}
        questions[f"d{i}"] = {"type": "choice", "criteria": criteria, "instructions": {
            "task": "Which option should this form field be set to, according to `note`?", "field": field}}
    answers = ask_all(note, questions)

    values: dict[int, str | int] = {}
    flags: dict[int, str] = {}
    confidence: dict[int, float] = {}
    for i in inputs:
        present = answers[f"p{i}"]["noul"]
        first, second = answers[f"a{i}"], answers[f"b{i}"]
        picked = first["choice"]
        if picked == NONE or present < ABSENT:
            confidence[i] = 1 - present
            if picked != NONE and present >= ABSENT:
                flags[i] = f"Left empty, but Jev leans to “{picked}”"
            elif picked == NONE and present >= PRESENT:
                flags[i] = f"Left empty, but Jev is {present:.0%} sure the note states it"
            continue
        values[i] = normalize(fields[i], picked)
        confidence[i] = min(present, first["probabilities"][picked], second["probabilities"].get(picked, 0))
        if second["choice"] != picked:
            flags[i] = f"Jev read this two ways: “{picked}” or “{second['choice']}”"
        elif present < PRESENT:
            flags[i] = f"Jev is only {present:.0%} sure the note gives this"
    for i in dropdowns:
        present, reading = answers[f"p{i}"]["noul"], answers[f"d{i}"]
        option = int(reading["choice"])
        if option == 0 or present < ABSENT:
            continue  # blank, or a value Jev reads into the note rather than finds there
        values[i] = option
        confidence[i] = min(present, reading["probabilities"][reading["choice"]])
        if present < PRESENT or confidence[i] < STATED:
            flags[i] = f"Jev is unsure the note says “{fields[i]['options'][option]}”"

    # One stretch of the note fills one field. When two text fields picked the same text (Franklin as both first
    # and last name), the more confident keeps it; the other is left empty and flagged with Jev's other reading.
    # And text inside another field's value ("Apt 4" inside the street) is not also its own field.
    texts = [i for i in values if i in inputs]
    for i in texts:
        rival = next((j for j in texts if j != i and j in values and i in values
                      and str(values[i]).casefold() == str(values[j]).casefold()), None)
        if rival is not None:
            loser = min(i, rival, key=lambda k: confidence[k])
            other = answers[f"b{loser}"]["choice"]
            hint = f"; Jev's other reading is “{other}”" if other not in (NONE, values[loser]) else ""
            flags[loser] = f"Left empty: Jev picked the same text for {label(fields[max(i, rival, key=lambda k: confidence[k])])}{hint}"
            values.pop(loser)
    for i in [i for i in values if i in inputs]:
        holder = next((j for j in values if j in inputs and j != i and i in values
                       and str(values[i]).casefold() in str(values[j]).casefold()), None)
        if holder is not None:
            flags[i] = f"Left empty: the note's text is already in {label(fields[holder])}"
            values.pop(i)

    # Second request: is each picked value stated for its field? (The same check as extraction.py.)
    checks = {f"v{i}": {"type": "noul", "instructions": {
        "task": "Does the note state that the patient's field has this value?",
        "field": label(fields[i]), "value": fields[i]["options"][v] if fields[i]["role"] == "combobox" else v}}
        for i, v in values.items() if i not in flags}
    for key, answer in (ask_all(note, checks) if checks else {}).items():
        i = int(key[1:])
        confidence[i] = min(confidence[i], answer["noul"])
        if answer["noul"] < STATED:
            flags[i] = f"Jev is {answer['noul']:.0%} sure the note says this"
    return values, flags, confidence
