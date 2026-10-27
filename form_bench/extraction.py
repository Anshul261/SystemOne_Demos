"""Workflow 1, extraction: turn a visit note into checked values for one EHR screen's fields.

The screen's fields come from the page itself (forms.read_form), so the same code serves any screen. The LLM
proposes every value it finds in the note. Jev then gives each field a confidence, its probability that the
field is right as proposed, and a field below the threshold is flagged for a person:

- a typed value: Jev's probability that the note states it,
- a dropdown: the probability Jev's own reading puts on the option the LLM chose,
- an empty field: Jev's probability that the note does not state it.

A dropdown value only Jev wanted stays blank and unflagged: Jev reads more into a note than is written (an Abn
flag of "Normal" for a plain number). No value is entered here: entry.py does that once a person approves.
"""

from concurrent.futures import ThreadPoolExecutor

from forms import label, option_index
from models import ask_all, llm_json

# Flag a value Jev gives less than this. On 340 tuning fields it raised 7 false alarms (0.9 would raise 40);
# the current notes produce no errors, so how much it catches still needs notes that do (bench.py calibrate).
VALUE_BELOW = 0.5
# Flag an empty field when Jev is this unsure it is empty. Stated fields scored 0.87-0.99 that they are
# stated, look-alikes such as Birth First Name 0.60-0.79, absent ones 0.01 (12 notes, outside the held-out set).
BLANK_BELOW = 0.15
PROMPT = ("You fill a clinical form from a note. Return a JSON object with one key per field id. Each value is "
          "the text to enter, copied from the note, or null when the note does not state it. For a field with "
          "options, the value is one of its options, exactly as written. Never infer, estimate or invent a "
          "value. Dates are YYYY-MM-DD. Numbers have no units. "
          # Without this, Qwen read "Pt Kris, Franklin" as first name Kris on every try; with it, 18 of 18 right.
          "Names: clinical notes often write the family name first with a comma, as in 'Pt Smith, Anna' or "
          "'Smith, Anna': there the last name is Smith and the first name is Anna. Without a comma, as in "
          "'Anna Smith', the first name comes first. Two names without a middle initial are first and last name.")


def extract(note: str, snapshot: str, fields: list[dict]
            ) -> tuple[dict[int, str | int], dict[int, str], dict[int, float]]:
    """The values to enter by field index (option indexes for dropdowns), the fields a person should review
    with the reason, and Jev's confidence in every field it judged. snapshot and fields come from
    forms.read_form."""
    dropdowns = [i for i, f in enumerate(fields) if f["role"] == "combobox" and len(f["options"]) > 1]
    inputs = [i for i, f in enumerate(fields) if f["role"] != "combobox"]
    with ThreadPoolExecutor(2) as pool:
        readings = pool.submit(choose_options, note, fields, dropdowns)
        proposed = pool.submit(write_values, note, snapshot, fields, inputs + dropdowns)
        readings, proposed = readings.result(), proposed.result()
    values: dict[int, str | int] = {i: proposed[i] for i in inputs if i in proposed}
    values |= {i: k for i in dropdowns if (k := option_index(fields[i], proposed.get(i))) is not None}
    stated = how_stated(note, fields, {i: values[i] for i in inputs if i in values}, [i for i in inputs if i not in values])

    confidence: dict[int, float] = {}
    flags: dict[int, str] = {}
    for i in inputs:
        if i in values:
            confidence[i] = stated[i]
            if stated[i] < VALUE_BELOW:
                flags[i] = f"Jev is {stated[i]:.0%} sure the note says this"
        else:
            confidence[i] = 1 - stated[i]
            if confidence[i] < BLANK_BELOW:
                flags[i] = f"Left empty, but Jev is {stated[i]:.0%} sure the note states it"
    for i in dropdowns:
        if i not in values:
            continue
        confidence[i] = readings[i][values[i]]
        if confidence[i] < VALUE_BELOW:
            leaning = max(readings[i], key=readings[i].get)
            flags[i] = (f"Jev agrees, but is only {confidence[i]:.0%} sure" if leaning == values[i] else
                        f"Jev is {confidence[i]:.0%} sure of this; it leans to “{fields[i]['options'][leaning] or 'blank'}”")
    return values, flags, confidence


def choose_options(note: str, fields: list[dict], dropdowns: list[int]) -> dict[int, dict[int, float]]:
    """Jev's own reading of every dropdown: {field: {option index: probability}}. Option 0 is the form's
    blank, meaning the note does not say."""
    questions = {}
    for i in dropdowns:
        criteria = {str(k): text or "(blank)" for k, text in enumerate(fields[i]["options"])}
        questions[f"f{i}"] = {"type": "choice", "criteria": criteria,
                              "instructions": f"Which option should the form field '{label(fields[i])}' be set to, "
                                              f"according to the note? Choose '{criteria['0']}' if the note does "
                                              "not say."}
    answers = ask_all(note, questions) if questions else {}
    return {i: {int(k): p for k, p in answers[f"f{i}"]["probabilities"].items()} for i in dropdowns}


def write_values(note: str, snapshot: str, fields: list[dict], indexes: list[int]) -> dict[int, str]:
    """One LLM call writes every value it finds in the note, dropdowns as option text; nulls are dropped.
    The form comes first in the prompt, so it is cached across notes."""
    spec = {f"f{i}": {"field": label(fields[i]), "options": [o for o in fields[i]["options"] if o]}
            if fields[i]["role"] == "combobox" else label(fields[i]) for i in indexes}
    output = llm_json(PROMPT, {"form": snapshot, "fields": spec, "note": note})
    ids = {f"f{i}": i for i in indexes}
    return {ids[k]: str(v).strip() for k, v in output.items() if k in ids and v not in (None, "")}


def how_stated(note: str, fields: list[dict], written: dict[int, str], empty: list[int]) -> dict[int, float]:
    """Jev's probability, for each written value, that the note states it, and for each empty field, that the
    note states that field at all. One batch of questions."""
    questions = {f"v{i}": {"type": "noul", "instructions": f"Does the note state that the patient's "
                                                            f"{label(fields[i])} is {value}?"}
                 for i, value in written.items()}
    questions |= {f"e{i}": {"type": "noul", "instructions": f"Does the note state the patient's {label(fields[i])}?"}
                  for i in empty}
    answers = ask_all(note, questions) if questions else {}
    return {i: answers[f"v{i}"]["noul"] for i in written} | {i: answers[f"e{i}"]["noul"] for i in empty}
