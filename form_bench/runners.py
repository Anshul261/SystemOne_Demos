"""The agents under test. Each fills one screen from a note in a page it opens, and the Meter records what it spent.

- jev: jev-ultrafast unchanged, given the note as its goal. System One picks every action; a small LLM writes
  each typed value.
- mapfill: read the form once from Playwright's ARIA snapshot, then
    1. System One chooses every dropdown option from the note, while one LLM call writes every text value,
    2. System One checks each written value against the note, flagging any it cannot confirm,
    3. Playwright fills the form. No model runs while filling.

System One is TypeSafe's Jev when TYPESAFE_API_KEY is set, and otherwise d1 on the local service: the request
shape is the same, so jev-ultrafast's own calls are redirected.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp
from jev_ultrafast import Agent, agent, browser, model
from playwright.sync_api import BrowserContext, Page
from playwright.sync_api import Error as PlaywrightError

TYPESAFE = "https://api.typesafe.ai/v1/systemone"
SYSTEM_ONE = "jev" if os.getenv("TYPESAFE_API_KEY") else "d1"
D1_URL = os.getenv("D1_URL", "http://127.0.0.1:8020")
D1_MODEL = os.getenv("D1_MODEL", "3b")
if SYSTEM_ONE == "d1":
    os.environ["TYPESAFE_API_KEY"] = "local-d1"  # jev-ultrafast reads it; the local service ignores it
LLM_URL = os.getenv("TEXT_MODEL_BASE_URL", "https://api.cerebras.ai/v1").rstrip("/")
LLM_MODEL = os.getenv("TEXT_MODEL", "qwen-3.8-27b")
os.environ.setdefault("TEXT_MODEL_BASE_URL", LLM_URL)
os.environ.setdefault("TEXT_MODEL", LLM_MODEL)
LLM_REASONING = os.getenv("LLM_REASONING", "low")  # "low" got every field right on 40 OpenEMR screens
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))
LLM_VOTES = int(os.getenv("LLM_VOTES", "1"))  # mapfill: independent extractions; any disagreement is flagged
FLAG_BELOW = 0.5  # System One's confidence that the note states a written value
# Empty fields are flagged only above this: stated fields scored 0.87-0.99, look-alikes such as Birth First
# Name 0.60-0.79, absent ones 0.01 (12 notes, outside the held-out cases).
STATED_ABOVE = 0.85
MAX_QUESTIONS = int(os.getenv("MAX_QUESTIONS", "16"))  # per System One request; smaller requests run in parallel
FIELD = re.compile(r"""^(\s*)- '?(textbox|spinbutton|combobox|searchbox)(?: "(.*?)")?""")
OPTION = re.compile(r'^\s*- option(?: "(.*?)")?')
ROW = re.compile(r'^(\s*)- row "(.*?)"')
GOAL = ("Enter this patient's details into the form exactly as the note states them. Leave any field the note "
        "does not mention empty. Do not press Save or submit the form. Stop when every stated detail is entered.\n"
        "Note:\n{note}")
EXTRACT = ("You fill a clinical form from a note. Return a JSON object with one key per field id. Each value is "
           "the text to enter, copied from the note, or null when the note does not state it. For a field with "
           "options, the value is one of its options, exactly as written. Never infer, estimate or invent a "
           "value. Dates are YYYY-MM-DD. Numbers have no units.")


@dataclass
class Meter:
    started: float = field(default_factory=time.perf_counter)
    system_one_calls: int = 0
    system_one_ms: float = 0
    system_one_tokens: int = 0
    llm_calls: int = 0
    llm_ms: float = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cached_tokens: int = 0
    actions: int = 0
    flagged: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)  # jevfill: fields Jev was unsure of, sent to the LLM
    error: str | None = None

    def report(self) -> dict:
        return {k: v for k, v in vars(self).items() if k != "started"} | {
            "wall_ms": round((time.perf_counter() - self.started) * 1000)}


meter: Meter | None = None
_post_json = model.post_json


def post_json(url: str, key: str, body: dict) -> dict:
    """jev-ultrafast's HTTP call, used by both agents: routes System One to local d1 if needed, and meters it."""
    if url == TYPESAFE and SYSTEM_ONE == "d1":
        url, body = f"{D1_URL}/system_one", {**body, "model": D1_MODEL}
    if "api.cerebras.ai" in url:  # Cerebras rejects jev-ultrafast's reasoning field; both agents share settings
        body = {k: v for k, v in body.items() if k != "reasoning"} | {"reasoning_effort": LLM_REASONING,
                                                                     "temperature": LLM_TEMPERATURE}
    started = time.perf_counter()
    try:
        result = _post_json(url, key, body)
    except RuntimeError as error:  # one retry for a dropped connection, never for a refused request
        if "connection failed" not in str(error):
            raise
        result = _post_json(url, key, body)
    elapsed = (time.perf_counter() - started) * 1000
    if meter and url.endswith("/chat/completions"):
        usage = result.get("usage", {})
        meter.llm_calls += 1
        meter.llm_ms += elapsed
        meter.llm_input_tokens += usage.get("prompt_tokens", 0)
        meter.llm_output_tokens += usage.get("completion_tokens", 0)
        meter.llm_cached_tokens += (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    elif meter:
        meter.system_one_calls += 1
        meter.system_one_ms += elapsed
        meter.system_one_tokens += result.get("usage", {}).get("input_tokens", 0)
    return result


model.post_json = post_json


@contextmanager
def metered():
    global meter
    meter = Meter()
    try:
        yield meter
    finally:
        meter = None


def system_one(note: str | dict, questions: dict) -> dict:
    url, key = (TYPESAFE, os.environ["TYPESAFE_API_KEY"])
    state = note if isinstance(note, dict) else {"note": note}
    body = {"model": os.getenv("TYPESAFE_MODEL", "jev-latest"), "state": state, "questions": questions}
    return post_json(url, key, body)["answers"]


def llm_json(system: str, user: dict) -> dict:
    body = {"model": LLM_MODEL, "max_tokens": 8192, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}]}
    result = post_json(f"{LLM_URL}/chat/completions", os.environ["TEXT_MODEL_API_KEY"], body)
    return json.loads(result["choices"][0]["message"]["content"])


def read_form(page: Page) -> tuple[str, list[dict]]:
    """The form's ARIA snapshot and its fields, read once per screen: [{role, name, row, nth, options}].
    A control inside a table row is found by its row, so unlabelled cells (OpenEMR's vitals) work; nth counts
    the same role within that row. Elsewhere nth tells apart controls sharing a name, like two Middle Names."""
    snapshot = page.locator("body").aria_snapshot()
    fields: list[dict] = []
    row, row_indent = None, -1
    for line in snapshot.splitlines():
        if match := ROW.match(line):
            row, row_indent = match[2], len(match[1])
        elif match := FIELD.match(line):
            if len(match[1]) <= row_indent:
                row, row_indent = None, -1
            role, name = match[2], match[3] or ""
            nth = sum(f["role"] == role and f["row"] == row and (row or f["name"] == name) for f in fields)
            fields.append({"role": role, "name": name, "row": row, "nth": nth, "options": []})
        elif (match := OPTION.match(line)) and fields and fields[-1]["role"] == "combobox":
            fields[-1]["options"].append(match[1] or "")
    return snapshot, [f for f in fields if f["name"] or f["row"]]


def label(field: dict) -> str:
    return " - ".join(part for part in (field.get("row"), field.get("name")) if part)


def locate(page: Page, field: dict):
    if field.get("row"):  # a row's name includes its selected options, so match its words in order, gaps allowed
        # Anchored at the start only: text typed into a row's box becomes part of the row's name.
        words = re.compile("^" + ".*".join(re.escape(w).replace("/", r"\/") for w in field["row"].split()))
        return page.get_by_role("row", name=words).get_by_role(field["role"]).nth(field.get("nth", 0))
    return page.get_by_role(field["role"], name=field["name"], exact=True).nth(field.get("nth", 0))


def field_id(field: dict) -> tuple:
    return field["role"], field.get("row") or "", "" if field.get("row") else field.get("name", ""), field.get("nth", 0)


def open_screen(context: BrowserContext, screen) -> Page:
    page = context.new_page()
    page.goto(screen.url)
    if screen.prepare:
        screen.prepare(page)
    return page


DROPDOWN_RULES = ["Choose an option only when the note states it. Do not infer title, gender identity, pronouns, "
                  "marital status, country, or an abnormal-result flag from other details.",
                  "Shorthand like '47F' or '73M' states sex: female or male."]


def choose_options(note: str, fields: list[dict], dropdowns: list[int], rules: list[str] | None = None
                   ) -> dict[int, tuple[int, float]]:
    """System One's own reading of every dropdown: {field: (option index, confidence)}. Option 0 is the form's
    blank, meaning the note does not say."""
    if not dropdowns:
        return {}
    questions = {}
    for i in dropdowns:
        criteria = {str(k): text or "(blank)" for k, text in enumerate(fields[i]["options"])}
        task = (f"Which option should the form field '{label(fields[i])}' be set to, according to the note? "
                f"Choose '{criteria['0']}' if the note does not say.")
        questions[f"f{i}"] = {"type": "choice", "criteria": criteria,
                              "instructions": {"task": task, "rules": rules} if rules else task}
    answers = ask_all(note, questions)
    return {i: (int(answers[f"f{i}"]["choice"]), answers[f"f{i}"]["confidence"]) for i in dropdowns}


def write_values(note: str, snapshot: str, fields: list[dict], indexes: list[int]) -> dict[int, str]:
    """One LLM call writes every value it finds in the note, dropdowns as option text; nulls are dropped."""
    spec = {f"f{i}": {"field": label(fields[i]), "options": [o for o in fields[i]["options"] if o]}
            if fields[i]["role"] == "combobox" else label(fields[i]) for i in indexes}
    output = llm_json(EXTRACT, {"form": snapshot, "fields": spec, "note": note})
    ids = {f"f{i}": i for i in indexes}
    return {ids[k]: str(v).strip() for k, v in output.items() if k in ids and v not in (None, "")}


def vote(note: str, snapshot: str, fields: list[dict], indexes: list[int]) -> tuple[dict[int, str], list[int]]:
    """LLM_VOTES extractions in parallel. A value needs every vote; a field the votes disagree on keeps the
    most common answer and is flagged."""
    with ThreadPoolExecutor(LLM_VOTES) as pool:
        ballots = list(pool.map(lambda _: write_values(note, snapshot, fields, indexes), range(LLM_VOTES)))
    values, split = {}, []
    for i in indexes:
        answers = [ballot.get(i, "").casefold() for ballot in ballots]
        if len(set(answers)) > 1:
            split.append(i)
        top = max(set(answers), key=answers.count)
        if top:
            values[i] = next(b[i] for b in ballots if b.get(i, "").casefold() == top)
    return values, split


def option_index(field: dict, text: str | None) -> int | None:
    matches = [k for k, option in enumerate(field["options"]) if k and text and option.casefold() == text.casefold()]
    return matches[0] if matches else None


def ask_all(note: str | dict, questions: dict) -> dict:
    """Many System One questions, sent in parallel requests of at most MAX_QUESTIONS."""
    keys = list(questions)
    chunks = [{k: questions[k] for k in keys[n:n + MAX_QUESTIONS]} for n in range(0, len(keys), MAX_QUESTIONS)]
    with ThreadPoolExecutor(max(len(chunks), 1)) as pool:
        return {k: v for answers in pool.map(lambda chunk: system_one(note, chunk), chunks) for k, v in answers.items()}


def stated(note: str, fields: list[dict], values: dict[int, str]) -> dict[int, float]:
    """Jev's probability that the note states each value for its field."""
    questions = {f"v{i}": {"type": "noul", "instructions": f"Does the note state that the patient's "
                                                            f"{label(fields[i])} is {value}?"}
                 for i, value in values.items()}
    answers = ask_all(note, questions) if questions else {}
    return {i: answers[f"v{i}"]["noul"] for i in values}


def check(note: str, fields: list[dict], written: dict[int, str], empty: list[int]) -> dict[int, str]:
    """System One checks the LLM both ways: each written value must be stated in the note, and each field
    left empty must not be. Returns the fields that fail, with the reason."""
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


def read_value(page: Page, field: dict, timeout: float = 5000) -> str:
    """What the control shows: the selected option's text, an input's value, or a widget's text."""
    return locate(page, field).evaluate(
        "e => e.tagName === 'SELECT' ? (e.value ? e.selectedOptions[0].text.trim() : '') :"
        " 'value' in e ? e.value : e.innerText.trim()", timeout=timeout)


def fill(page: Page, field: dict, value) -> bool:
    """Enter a value, read it back, and retry once; False if the page still shows something else."""
    control = locate(page, field)
    for _ in range(2):
        if field["role"] == "combobox":
            control.select_option(index=value)
            if control.evaluate("e => e.selectedIndex") == value:
                return True
        else:
            control.fill(value)
            if control.input_value().strip().casefold() == value.strip().casefold():
                return True
    return False


def propose(page: Page, note: str) -> tuple[list[dict], dict[int, str | int], dict[int, str]]:
    """System Two proposes every value; System One checks it. Returns the form's fields, the values to enter
    (option indexes for dropdowns) and the fields to review, with reasons.

    A dropdown is set only when the LLM's reading is one of its options, and flagged unless System One
    confidently chose the same. Anything only System One wanted is left blank: it reads more into a note
    (an Abn flag of "Normal" for a plain number) than is written. Text values must be stated in the note,
    and empty text fields must not be."""
    snapshot, fields = read_form(page)
    dropdowns = [i for i, f in enumerate(fields) if f["role"] == "combobox" and len(f["options"]) > 1]
    inputs = [i for i, f in enumerate(fields) if f["role"] != "combobox"]
    with ThreadPoolExecutor(2) as pool:
        chosen = pool.submit(choose_options, note, fields, dropdowns)
        proposed = pool.submit(vote, note, snapshot, fields, inputs + dropdowns)
        chosen, (proposed, split) = chosen.result(), proposed.result()
    flags = {i: "Two readings of the note disagreed" for i in split}
    values: dict[int, str | int] = {i: proposed[i] for i in inputs if i in proposed}
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
    return fields, values, flags


def shown(field: dict, value: str | int) -> str:
    """What a value looks like on the page: dropdowns hold option indexes."""
    return field["options"][value] if field["role"] == "combobox" else value


def enter(page: Page, fields: list[dict], values: dict[int, str | int], flags: dict[int, str]) -> int:
    """Fill every value, then read them all back: some pages change fields while their scripts run.
    Values the page will not keep are added to flags. Returns the number of fields entered."""
    for i, value in values.items():
        try:
            kept = fill(page, fields[i], value)
        except PlaywrightError:  # the control refused the value, e.g. text in a number field
            kept = False
        if not kept:
            flags[i] = "The page did not keep this value"
    for i, value in values.items():
        if read_value(page, fields[i]).strip().casefold() != shown(fields[i], value).strip().casefold() \
                and not fill(page, fields[i], value):
            flags[i] = "The page did not keep this value"
    return len(values)


def run_mapfill(context: BrowserContext, screen, note: str, spent: Meter) -> Page:
    page = open_screen(context, screen)
    fields, values, flags = propose(page, note)
    spent.actions = enter(page, fields, values, flags)
    spent.flagged = [field_id(fields[i]) for i in flags]
    return page


SEGMENT = re.compile(r"[,;\n()]|\.\s|:\s")
NOT_STATED = "(not stated)"
MEASURED = re.compile(r"(?<![\w/])(?:lbs?|in|cm|kg|mmHg|per min|%|F|C)(?![\w/])")
UNIT = re.compile(r"(-?\d+(?:\.\d+)?)\s*(?:lbs?|in|cm|kg|°?[FC]|%|mmHg|/min|bpm|per min)", re.I)
ESCALATE_BELOW = float(os.getenv("ESCALATE_BELOW", "0.8"))  # jevfill: below this, the LLM fills the field
JEV_CONTEXT = os.getenv("JEV_CONTEXT", "rules")  # jevfill: "none", or "rules" for the guidance below
RULES = ["Copy the value exactly as the note writes it; choose only text that is this field's value.",
         "Notes often write names family name first: in 'Pt Smith, Anna' the family (last) name is Smith and the "
         "first name is Anna. In 'Anna Smith is a 40-year-old' the first name is Anna.",
         "Shorthand like '47F' or '73M' means age 47 female or age 73 male.",
         "Birth name, middle name, preferred name and a second address line are separate fields: choose a value "
         "for them only when the note states it for that purpose.",
         "Vitals: 'BP 122/80' is systolic 122 and diastolic 80; HR is pulse; RR is respiration; SpO2 is oxygen "
         "saturation. Choose the number only, without its unit.",
         f"Choose '{NOT_STATED}' when the note does not give this field's value."]


def candidates(note: str, longest: int = 6) -> list[str]:
    """Every short run of words between punctuation, plus split-out BP pairs and age/sex shorthand: the spans
    Jev may choose from. Over-finding is cheap; a value missing here cannot be chosen."""
    found = []
    for segment in SEGMENT.split(note):
        words = [w for w in (w.strip(".:%") for w in segment.split()) if w]
        found += [" ".join(words[k:k + n]) for n in range(1, longest + 1) for k in range(len(words) - n + 1)]
    for match in re.finditer(r"(\d+)/(\d+)", note):
        found += [match[1], match[2]]
    for match in re.finditer(r"\b(\d+)([MF])\b", note):
        found += [match[1], match[2]]
    return list(dict.fromkeys(found))[:250]  # a Choice takes at most 255 options


def select_values(note: str, fields: list[dict], inputs: list[int]) -> dict[int, tuple[str | None, float]]:
    """Jev picks each text field's value from the note's own spans, all fields in parallel requests:
    {field: (value or None for not stated, probability)}. Nothing is generated, so nothing can be invented."""
    spans = candidates(note)
    numbers = list(dict.fromkeys(m[0] for m in re.finditer(r"\d+(?:\.\d+)?", note)))
    no_value = {NOT_STATED: "The note does not give this field's value."}
    state = {"note": note}
    if JEV_CONTEXT == "rules":
        state["form_fields"] = [label(fields[i]) for i in inputs]
    questions = {}
    for i in inputs:
        instructions = {"task": f"Which text from the note should be entered in the form field '{label(fields[i])}'?"}
        if JEV_CONTEXT == "rules":
            instructions["rules"] = RULES
        # A measurement (its label names a unit, or it is a number box) chooses among the note's numbers only.
        pool = numbers if fields[i]["role"] == "spinbutton" or MEASURED.search(label(fields[i])) else spans
        questions[f"s{i}"] = {"type": "choice", "instructions": instructions,
                              "criteria": {span: None for span in pool} | no_value}
    answers = ask_all(state, questions)
    selected = {}
    for i in inputs:
        # "70.2" and "70.2 in" are one value: add up spans that normalise alike, then take the likeliest value.
        totals: dict[str | None, float] = {}
        for span, probability in answers[f"s{i}"]["probabilities"].items():
            value = None if span == NOT_STATED else normalize(fields[i], span)
            totals[value] = totals.get(value, 0) + probability
        value = max(totals, key=totals.get)
        selected[i] = (value, totals[value])
    return selected


def normalize(field: dict, span: str) -> str:
    """Fit a chosen span to its field: number fields take the number ("56.4 in" -> "56.4"), date fields take
    YYYY-MM-DD ("03/21/1985" -> "1985-03-21"). The value itself is never changed."""
    if field["role"] == "spinbutton" and (number := re.search(r"-?\d+(?:\.\d+)?", span)):
        return number[0]
    if measured := UNIT.fullmatch(span):  # "70.2 in", "97%", "99.1 F" -> the number
        return measured[1]
    if re.search(r"\bdate\b|\bdob\b", label(field), re.I) and (us := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", span)):
        return f"{us[3]}-{int(us[1]):02d}-{int(us[2]):02d}"
    return span


def propose_jev(page: Page, note: str) -> tuple[list[dict], dict[int, str | int], dict[int, str]]:
    """jevfill: Jev selects every value (text from the note's spans, dropdowns from their options); only the
    fields it is unsure of go to the LLM, whose answers Jev then checks. Same result shape as propose()."""
    snapshot, fields = read_form(page)
    dropdowns = [i for i, f in enumerate(fields) if f["role"] == "combobox" and len(f["options"]) > 1]
    inputs = [i for i, f in enumerate(fields) if f["role"] != "combobox"]
    with ThreadPoolExecutor(2) as pool:
        chosen = pool.submit(choose_options, note, fields, dropdowns, DROPDOWN_RULES if JEV_CONTEXT == "rules" else None)
        selected = pool.submit(select_values, note, fields, inputs)
        chosen, selected = chosen.result(), selected.result()
    values: dict[int, str | int] = {i: value for i, (value, _) in selected.items() if value}
    values |= {i: option for i, (option, _) in chosen.items() if option}
    # Only a value Jev picked but is unsure of goes to the LLM. "Not stated" stays blank: an empty field is
    # visible to the reviewer, while a wrong value can slip past.
    unsure = [i for i, (value, confidence) in selected.items() if value and confidence < ESCALATE_BELOW]
    # Jev must also agree the note states each value. It reads more into a note than is written, mostly in
    # dropdowns (an Abn flag of "Normal" for a plain number): those it cannot confirm are left blank, and
    # borderline ones go to the LLM. A typed value is verbatim note text, so it needs only a plain yes.
    confirmed = stated(note, fields, {i: shown(fields[i], v) for i, v in values.items() if i not in unsure})
    suggested: dict[int, str] = {}
    for i, probability in confirmed.items():
        if i in inputs and probability < FLAG_BELOW:
            unsure.append(i)
        elif i in dropdowns and probability < STATED_ABOVE:
            option = shown(fields[i], values.pop(i))
            if probability >= FLAG_BELOW:  # borderline: the reviewer decides
                suggested[i] = f"Left blank: Jev suggests “{option}”, but the note does not clearly say so"
        elif i in dropdowns and chosen[i][1] < ESCALATE_BELOW:
            option = shown(fields[i], values.pop(i))
            suggested[i] = f"Left blank: Jev leans to “{option}”, but was unsure which option fits"
    unsure = list(dict.fromkeys(unsure))
    flags: dict[int, str] = dict(suggested)
    if meter:
        meter.escalated = [label(fields[i]) for i in unsure]
    from_llm: set[int] = set()
    if unsure:  # the LLM sees the note and just these fields, not the whole form
        llm = write_values(note, "\n".join(label(fields[i]) for i in unsure), fields, unsure)
        for i in unsure:
            if (proposal := llm.get(i)) is None:
                values.pop(i, None)
                continue
            values[i] = proposal
            from_llm.add(i)
            if proposal.casefold() != str(selected[i][0] or "").casefold():
                flags[i] = "Jev and the LLM read this differently"
        # An LLM value Jev cannot find in the note is left blank: a blank is visible, a wrong value may not be.
        for i, reason in check(note, fields, {i: values[i] for i in from_llm}, []).items():
            flags[i] = f"Left blank: the LLM suggested “{values.pop(i)[:60]}”, but Jev could not find it in the note"
            from_llm.discard(i)
    # One stretch of the note feeds one field. When several text fields hold the same text (a first name also
    # given as the birth name), the field Jev was surest of keeps it and the others stay blank; and "Apt 24"
    # inside the chosen street is not also Address Line 2. A value from the LLM never pushes out Jev's.
    for value in {str(v).casefold() for i, v in values.items() if i in inputs}:
        sharing = sorted((i for i in values if i in inputs and str(values[i]).casefold() == value),
                         key=lambda i: (i not in from_llm, selected[i][1] if selected[i][0] else 0), reverse=True)
        for i in sharing[1:]:
            values.pop(i)
            flags.pop(i, None)
    inside = lambda i, j: str(values[i]).casefold() in str(values[j]).casefold()  # noqa: E731
    for j in [j for j in from_llm if j in values and j in inputs]:  # the LLM pasted a stretch of the note
        if sum(inside(i, j) for i in values if i in inputs and i != j) >= 2:
            flags[j] = f"Left blank: the LLM suggested “{values.pop(j)[:60]}”, which holds several other fields"
    for i in [i for i in values if i in inputs]:
        holder = next((j for j in values if j in inputs and j != i and i in values and inside(i, j)), None)
        if holder is not None:
            values.pop(i)
            flags[i] = f"Left empty: the note's text is already in {label(fields[holder])}"
    return fields, values, flags


def run_jevfill(context: BrowserContext, screen, note: str, spent: Meter) -> Page:
    page = open_screen(context, screen)
    fields, values, flags = propose_jev(page, note)
    spent.actions = enter(page, fields, values, flags)
    spent.flagged = [field_id(fields[i]) for i in flags]
    return page


class PageBrowser(browser.Browser):
    """jev-ultrafast's browser, attached to a tab Playwright opened instead of a new one, so the scorer sees
    the same page. Playwright has already navigated it."""

    def __init__(self, target_id: str):
        ensure_daemon()
        self.target = None  # Playwright owns the tab and closes it
        self.session = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)


def run_jev(context: BrowserContext, screen, note: str, spent: Meter) -> Page:
    """jev-ultrafast as shipped, with the note as its goal."""
    page = open_screen(context, screen)
    target_id = context.new_cdp_session(page).send("Target.getTargetInfo")["targetInfo"]["targetId"]
    agent.Browser = lambda _url: PageBrowser(target_id)
    jev = Agent(screen.url, GOAL.format(note=note))
    try:
        for _ in jev.run():
            pass
    except (ValueError, RuntimeError) as error:  # its step budget, or a model error: score what it entered
        spent.error = str(error)
    spent.actions = len(jev.state["history"])
    return page


AGENTS = {"mapfill": run_mapfill, "jevfill": run_jevfill, "jev": run_jev}
