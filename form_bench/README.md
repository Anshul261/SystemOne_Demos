# Chart entry: from a doctor's note to the EMR

A doctor writes what happened, the way they would say it. Jev makes every judgment from there; code and
scripts do the rest ([pipeline.py](pipeline.py)):

1. **Read** ([layer1.py](layer1.py)). Code finds candidate mentions (names, dates, addresses, numbers with
   their units); Jev picks among them and says what each number measures, including which lab test a result
   belongs to and which reading is current. Code converts units and flags results against reference ranges.
   The result is one patient record ([schema.py](schema.py)), the same for any EMR.
2. **Plan** ([mapping.py](mapping.py)). Each EMR screen is mapped once by looking at it: Jev sees a picture
   of the screen and, per field, a crop with the field outlined, and says which record value it holds (item,
   attribute, unit). The mapping is saved; a screen is needed when the record has something for it.
3. **Review.** Every value, with how sure Jev is of it and of the field; flagged values must be kept or
   corrected, and a screen can be left out.
4. **Enter and save, screen by screen** ([steps.py](steps.py), [entry.py](entry.py)). Jev reaches each screen
   through the EMR's own menus, the way a person would. The kept values are typed in field by field and read
   back (dropdowns take the option Jev matches to the value, checked by a Noul per option). Jev checks that the
   screen shows the right patient, then the EMR's own button saves. Later screens need earlier saves (vitals
   need a visit, a visit needs the registered patient), so each screen is opened only when its turn comes.

How Jev gets around ([steps.py](steps.py)) is pieced together from jev-ultrafast and Browser Harness.
jev-ultrafast's page snapshot and its Jev questions ("which operation, on which control") run over every
frame of the page, and each action is taken only on the element that was observed. A route that reached a
goal is kept as data, the way Browser Harness keeps domain skills, and replayed without Jev while its
controls still match. Jev picks any step that no longer matches, such as a different patient's row, and a
separate Jev question confirms the goal is reached. Popups are answered by Jev against the goal, never
blindly. Jev never writes text: when it types, it picks which of the given values goes in the field.

An EMR's specifics live in its target ([targets.py](targets.py)), as plain-language goals rather than
scripts: "From the Procedures menu, open Pending Review". Two things there are not entry. Signing in uses
credentials from `.env`, which never go to a model. Lab orders are placed by a setup script, because results
only arrive for orders and ordering is test setup, not part of entering a note.

Supporting modules: [forms.py](forms.py) reads screens and fills or reads controls, and [models.py](models.py)
calls the models. The review console ([app.py](app.py)) runs both workflows for a person; the bench
([bench.py](bench.py)) runs them end to end with no person and scores the result.

The bench compares two agents on the same synthetic patients and screens:

| Agent | How it fills a form |
|---|---|
| `mapfill` | The two workflows above, with every value accepted as extracted. |
| `jev` | [jev-ultrafast](https://github.com/browser-use/jev-ultrafast), unchanged, with the note as its goal, as an outside reference. System One picks every action; a small LLM writes each typed value. |

### Accuracy

On 57 synthetic patients held out from tuning (114 OpenEMR screens: new patient and vitals), the final design
entered every field correctly: no wrong, missed or invented values, and no unscored field changed. It flagged
16 fields for review, all of which were in fact right.

Flags come from Jev's confidence in each field (see extraction.py), shown to the reviewer beside every value.
`bench.py calibrate results/<file>.json` shows, for each threshold, how many wrong fields would be flagged and
how many right ones would be flagged needlessly.

Two caveats. The name-order rule came from a mistake seen in these same patients. And every note comes from
three templates, so the notes no longer produce errors at all: the thresholds are set by their false-alarm cost,
and how much they catch needs messier notes, written independently, that do produce errors.

### What was tried and dropped

`jevfill` had Jev pick each value from the note's own word spans, after TypeSafe's "select instead of
generate" pattern, with the LLM only for fields Jev was unsure of. On the 57 held-out patients (114 OpenEMR
screens):

| | mapfill | jevfill, with rules | jevfill, no context |
|---|---|---|---|
| Fields right | 99.8% | 97.7% | 97.4% |
| Errors nothing flagged | 2 | 7 | 8 |
| Flags (real errors among them) | 24 (0) | 73 (15) | 79 (17) |
| Median per screen | 1.9 s | 2.3 s | 2.5 s |
| Estimated cost per screen | $0.0055 | $0.0015 | $0.0017 |

It was 3.7 times cheaper but less accurate, and accuracy comes first, so it was removed (see git history).
Also measured and dropped: three LLM votes at temperature 0.7 (less accurate, three times the cost), reasoning
"none" (less accurate) and "medium" or "high" (no better than "low"), and gpt-oss-120b (less accurate).

System One is TypeSafe's Jev when `TYPESAFE_API_KEY` is set, and otherwise d1-3B on the local service.
jev-ultrafast's own requests are redirected there, since d1 takes the same request shape. System Two is
Qwen3.8-27B on Cerebras, at temperature 0 with reasoning "low" for both agents. Its prompt states the
"Smith, Anna" name convention: without it, Qwen read "Pt Kris, Franklin" backwards every time, and with it,
18 of 18 hard name cases were right. Override with `LLM_REASONING`, `LLM_TEMPERATURE` and `TEXT_MODEL`.

## What it reuses

| Piece | From |
|---|---|
| Patients | [Synthea](https://github.com/synthetichealth/synthea) sample CSVs: names, addresses, vitals, allergies |
| Baseline agent, browser actions | jev-ultrafast and its [Browser Harness](https://github.com/browser-use/browser-harness) connection |
| Form reading, filling, read-back | Playwright's ARIA snapshot and role locators |
| System One | `services/d1` in this repo, or TypeSafe's API |
| HTTP calls and retries | jev-ultrafast's `post_json`, metered for both agents |

The bench itself is the glue: case notes ([cases.py](cases.py)), targets and answer keys
([targets.py](targets.py)), the two agents ([runners.py](runners.py)) and the scorer ([bench.py](bench.py)).

## Run it

```bash
cd form_bench
uv sync
cp .env.example .env          # add TEXT_MODEL_API_KEY
../start-d1.sh                # unless TYPESAFE_API_KEY is set
mkdir -p data/synthea && curl -sSLo data/sample.zip \
  https://synthetichealth.github.io/synthea-sample-data/downloads/latest/synthea_sample_data_csv_latest.zip \
  && unzip -o -q data/sample.zip patients.csv observations.csv allergies.csv -d data/synthea && rm data/sample.zip
uv run --env-file .env python bench.py run --target local --agents mapfill,jev --cases 10
uv run pytest -q
```

For fresh patients, run Synthea yourself
(`java -jar synthea-with-dependencies.jar -p 50 --exporter.csv.export=true`) and copy the three CSVs into
`data/synthea/`.

Each run prints one line per case and agent, then a summary, and saves everything to
`results/<target>-<time>.json`:

| Measure | Meaning |
|---|---|
| `field_accuracy` | Fields whose read-back value matches the case (numbers within 0.05, lists in any order) |
| `wrong` | Fields filled with the wrong value: the worst failure |
| `invented` | Fields the note left out that the agent filled anyway |
| `missed` | Fields the note stated that the agent left empty |
| `unflagged_errors` | Wrong, invented or missed fields that nothing flagged. For trust, this should be 0. |
| `flagged`, `flagged_that_were_wrong` | Fields mapfill marked for review, and how many were really wrong. Every flag costs a reviewer's time. |
| `extra_fields` | Unscored fields the agent changed, such as Pronouns or Abn flags the note never states. The page's own conversions (kg, cm, BMI) are excluded. |
| `save_attempts` | Times an agent pressed Save. Saves are blocked, but an agent should not try. |
| `system_one_calls`, `llm_calls`, `llm_tokens` | Mean per screen |
| `llm_cache_hit` | Share of LLM input tokens Cerebras served from its prompt cache. mapfill puts the form before the note, so the form is cached across patients. |
| `usd_per_case` | Estimated from list prices in `bench.py`, per screen. Cached tokens are billed in full: Cerebras is reported not to discount them. Local d1 counts as free. |

`--offset N` skips the first N patients. The flag thresholds were chosen on the first patients, so score
on later ones (`--cases 57 --offset 20`) to check them fairly.

## Review console

```bash
uv run --env-file .env uvicorn app:app --port 8050     # then open http://127.0.0.1:8050
```

Write or paste a note, or use a sample patient, and choose the EMR.

1. **Read the note.** Jev reads the note and fills the screens it needs from each screen's saved layout
   (for OpenEMR: new patient, vitals, lab results), each value next to the words in the note it came from.
   A screen never seen before is opened once, with the EMR's test patient, and mapped.
2. **Review.** Keep each flagged value or type a correction; leave out a screen that is not wanted.
   Correcting the patient's name or birth date changes the patient the EMR is searched for.
3. **Enter and save.** Locked until every flag is resolved. The console shows the EMR at every step and
   logs each one, marking which a saved route took and which Jev chose. A value the page refuses stops the
   run before that screen is saved; once it is fixed or kept, the run carries on.

On OpenEMR, one note with demographics, vitals and three lab panels reads in about 5 s (19 Jev requests,
layouts saved). Registering the patient, creating the visit, and entering and saving vitals and 45 lab
values took 53 s, with every step after the first run replayed from saved routes. OpenEMR's own record of the
visit matched the note, value for value. One browser serves the console, so it is for one reviewer at a time.

What it took to make OpenEMR's real UI work without OpenEMR code, each fixed in general rather than for one
screen:

- **Menus built of plain `div`s.** jev-ultrafast lists links, buttons and form controls. Elements with a
  click binding in their markup (onclick, Knockout, Angular, Vue), focusable ones, and ones with a pointer
  cursor are also read as clickable, as browser-use's own reader does.
- **A button named by its value.** `<button value="true">Confirm Create New Patient</button>` read as "true".
  Buttons are now named by their text, as browsers name them.
- **Only the viewport.** Jev now sees the whole page, as a person filling a form does by scrolling. A long
  page is cut to the 150 controls most useful for getting around, so a request fits.
- **Acting instead of stopping.** jev-ultrafast's step question pressed a blank form's Create button when
  only opening the form was asked. A separate Jev question, given the steps taken, now decides whether the
  goal is reached, and a save also counts as done once the filled form has left the screen.
- **Typed values reformatted.** A date picker shows `2026-10-29` as `2026-10-29 00:00`; read-back accepts a
  date shown at midnight as the date typed, and nothing looser.
- **Identical-looking fields.** OpenEMR's per-result Code cells shared one key in the mapping cache; fields
  that look alike are now told apart by order. Mapping inside OpenEMR's frames scores 263 of 263 fields
  against the answer key (38 new patient, 33 vitals, 192 lab results).

## Targets

`local` is [fixtures/intake.html](fixtures/intake.html), one page modelled on OpenEMR's new-patient and
vitals screens, so the bench runs without an EHR. Its Save button only records that it was pressed.

`openemr` logs in with `OPENEMR_URL`, `OPENEMR_USER` and `OPENEMR_PASSWORD`, blocks OpenEMR's save
endpoints, and accepts a self-signed certificate. It covers the new-patient screen with the Contact section
opened, scoring name, DOB, sex (Birth Sex or Sex, since a note's "female" fits either) and address,
including Address Line 2. It also covers the vitals screen of a fixture patient and encounter, which the
bench creates once and remembers in `data/openemr_fixture.json`. OpenEMR's vitals inputs have no labels,
so they are found by their table row. `bench.py map --target openemr` lists every control a screen offers,
which is how the answer keys were written.

## Limits

- All patients are synthetic. Do not point the bench at real patient data.
- The `jev` baseline is jev-ultrafast unchanged: common HTML and ARIA controls, not frames or shadow
  roots, so the bench opens OpenEMR's screens by their direct URL. The console's own steps ([steps.py](steps.py))
  read every frame.
- Saved routes and the provisional thresholds in [steps.py](steps.py) (goal reached, popup answers) and
  [pipeline.py](pipeline.py) (right patient) have been tried on one EMR and a handful of runs. A route recorded
  on an unusual path (a popup) can carry odd steps; it is rewritten whenever a run takes different steps.
- Lab reading has been tried on a few notes, not a held-out set with answer keys.
- With d1-3B standing in for Jev, the `jev` baseline measures jev-ultrafast's loop with an open model.
  It is much weaker that way: on the local form it fell from 100% to 79% of fields, and stopped early on
  half the cases.
- On OpenEMR's long new-patient page, jev-ultrafast can exceed TypeSafe's request limit
  (`max_tokens_exceeded`), because every dropdown option becomes its own action in the page state.
