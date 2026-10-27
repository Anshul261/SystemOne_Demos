# Chart entry: extraction and entry

Two workflows turn a clinician's visit note into an EHR record, with a person between them:

1. **Extraction** ([extraction.py](extraction.py)). The EHR screen's fields are read from the page itself, so
   any screen works. One LLM call proposes a value for each field from the note; Jev checks every one.
   - A dropdown is set only when the LLM's reading is one of its options. It is flagged unless Jev
     confidently chose the same. A value only Jev wanted stays blank: Jev reads more into a note than is
     written (an Abn flag of "Normal" for a plain number).
   - A typed value is flagged unless Jev agrees the note states it.
   - An empty field is flagged when Jev is confident the note states it (above 0.85).
2. **Entry** ([entry.py](entry.py)). The approved values are typed into the screen field by field, each one
   read back, then all of them read back again, since some pages change fields while their scripts run.
   Each field produces a frame, so a person can watch the entry happen.

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
19 fields for review, all of which were in fact right. One caveat: the name-order rule came from a mistake
seen in these same patients, so a fresh set of patients is the next fair test.

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

The console runs the two workflows with a person between them. Paste a visit note, or use a sample patient,
and choose an EHR screen.

1. **Extract values.** Each value is listed next to the words in the note it came from, highlighted when you
   point at the field. Flagged values say why.
2. **Review.** Keep each flagged value or type a correction.
3. **Enter into the EHR.** Locked until every flag is resolved. The console shows the EHR screen as each value
   is typed and read back.
4. **Save.** Locked until entry has finished and anything the page refused is reviewed.

The server enforces the same order. One browser serves the console, so it is for one reviewer at a time.

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
- jev-ultrafast handles common HTML and ARIA controls, not frames or shadow roots. Screens inside
  OpenEMR's frames need their direct URL.
- With d1-3B standing in for Jev, the `jev` baseline measures jev-ultrafast's loop with an open model.
  It is much weaker that way: on the local form it fell from 100% to 79% of fields, and stopped early on
  half the cases.
- On OpenEMR's long new-patient page, jev-ultrafast can exceed TypeSafe's request limit
  (`max_tokens_exceeded`), because every dropdown option becomes its own action in the page state.
