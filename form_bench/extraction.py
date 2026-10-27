"""Workflow 1, extraction: turn a visit note into checked values for one EHR screen's fields.

The screen's fields come from the page itself (forms.read_form), so the same code serves any screen. The LLM
proposes every value it finds in the note; Jev checks each one, and a person reviews whatever is flagged.
No value is entered here: entry.py does that once the values are approved.

- A dropdown is set only when the LLM's reading is one of its options. It is flagged unless Jev confidently
  chose the same option. A value only Jev wanted stays blank: Jev reads more into a note than is written
  (an Abn flag of "Normal" for a plain number).
- A typed value is flagged unless Jev agrees the note states it.
- An empty field is flagged when Jev is confident the note states it.
"""

from concurrent.futures import ThreadPoolExecutor

from forms import label, option_index
from models import ask_all, llm_json

FLAG_BELOW = 0.5  # Jev's probability that the note states a value; below this, the value is flagged
# Empty fields are flagged only above this: stated fields scored 0.87-0.99, look-alikes such as Birth First
# Name 0.60-0.79, absent ones 0.01 (12 notes, outside the held-out cases).
STATED_ABOVE = 0.85
PROMPT = ("You fill a clinical form from a note. Return a JSON object with one key per field id. Each value is "
          "the text to enter, copied from the note, or null when the note does not state it. For a field with "
          "options, the value is one of its options, exactly as written. Never infer, estimate or invent a "
          "value. Dates are YYYY-MM-DD. Numbers have no units. "
          # Without this, Qwen read "Pt Kris, Franklin" as first name Kris on every try; with it, 18 of 18 right.
          "Names: clinical notes often write the family name first with a comma, as in 'Pt Smith, Anna' or "
          "'Smith, Anna': there the last name is Smith and the first name is Anna. Without a comma, as in "
          "'Anna Smith', the first name comes first. Two names without a middle initial are first and last name.")


def extract(note: str, snapshot: str, fields: list[dict]) -> tuple[dict[int, str | int], dict[int, str]]:
    """The values to enter, by field index (option indexes for dropdowns), and the fields a person should
    review, with the reason. snapshot and fields come from forms.read_form."""
    dropdowns = [i for i, f in enumerate(fields) if f["role"] == "combobox" and len(f["options"]) > 1]
    inputs = [i for i, f in enumerate(fields) if f["role"] != "combobox"]
    with ThreadPoolExecutor(2) as pool:
        chosen = pool.submit(choose_options, note, fields, dropdowns)
        proposed = pool.submit(write_values, note, snapshot, fields, inputs + dropdowns)
        chosen, proposed = chosen.result(), proposed.result()
    values: dict[int, str | int] = {i: proposed[i] for i in inputs if i in proposed}
    flags: dict[int, str] = {}
    for i in dropdowns:
        mine, (theirs, confidence) = option_index(fields[i], proposed.get(i)), chosen[i]
        if mine is None:
            continue
        values[i] = mine
        if mine != theirs:
            flags[i] = f"The checker read this as “{fields[i]['options'][theirs] or 'blank'}”"
        elif confidence < FLAG_BELOW:
            flags[i] = "The checker agreed, but with low confidence"
    flags |= check(note, fields, {i: v for i, v in values.items() if i in inputs}, [i for i in inputs if i not in values])
    return values, flags


def choose_options(note: str, fields: list[dict], dropdowns: list[int]) -> dict[int, tuple[int, float]]:
    """Jev's own reading of every dropdown: {field: (option index, confidence)}. Option 0 is the form's blank,
    meaning the note does not say."""
    questions = {}
    for i in dropdowns:
        criteria = {str(k): text or "(blank)" for k, text in enumerate(fields[i]["options"])}
        questions[f"f{i}"] = {"type": "choice", "criteria": criteria,
                              "instructions": f"Which option should the form field '{label(fields[i])}' be set to, "
                                              f"according to the note? Choose '{criteria['0']}' if the note does "
                                              "not say."}
    answers = ask_all(note, questions) if questions else {}
    return {i: (int(answers[f"f{i}"]["choice"]), answers[f"f{i}"]["confidence"]) for i in dropdowns}


def write_values(note: str, snapshot: str, fields: list[dict], indexes: list[int]) -> dict[int, str]:
    """One LLM call writes every value it finds in the note, dropdowns as option text; nulls are dropped.
    The form comes first in the prompt, so it is cached across notes."""
    spec = {f"f{i}": {"field": label(fields[i]), "options": [o for o in fields[i]["options"] if o]}
            if fields[i]["role"] == "combobox" else label(fields[i]) for i in indexes}
    output = llm_json(PROMPT, {"form": snapshot, "fields": spec, "note": note})
    ids = {f"f{i}": i for i in indexes}
    return {ids[k]: str(v).strip() for k, v in output.items() if k in ids and v not in (None, "")}


def check(note: str, fields: list[dict], written: dict[int, str], empty: list[int]) -> dict[int, str]:
    """Jev checks the LLM both ways: each written value must be stated in the note, and each field left empty
    must not be. Returns the fields that fail, with the reason."""
    questions = {f"v{i}": {"type": "noul", "instructions": f"Does the note state that the patient's "
                                                            f"{label(fields[i])} is {value}?"}
                 for i, value in written.items()}
    questions |= {f"e{i}": {"type": "noul", "instructions": f"Does the note state the patient's {label(fields[i])}?"}
                  for i in empty}
    if not questions:
        return {}
    answers = ask_all(note, questions)
    return ({i: "The checker could not find this in the note" for i in written
             if answers[f"v{i}"]["noul"] < FLAG_BELOW}
            | {i: "The note seems to state this, but nothing was entered" for i in empty
               if answers[f"e{i}"]["noul"] >= STATED_ABOVE})
