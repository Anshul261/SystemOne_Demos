# Form-fill bench

How fast, cheap and accurate can an agent type a clinician's note into an EHR form? This bench runs agents
on the same synthetic patients and the same form, blocks every save, then reads the form back and scores
each field.

| Agent | How it fills a form |
|---|---|
| `jev` | [jev-ultrafast](https://github.com/browser-use/jev-ultrafast), unchanged, with the note as its goal. System One picks every action; a small LLM writes each typed value. |
| `mapfill` | Reads the form once from Playwright's ARIA snapshot. One LLM call proposes every value, and System One checks it (below). Playwright fills the form with no model in the loop, reads every value back, and refills anything the page changed. |

mapfill is built so that a wrong value is never entered silently. System Two (the LLM) proposes; System One
(Jev) checks, in parallel and in one more request:

- A dropdown is set only when the LLM's reading is one of its options. It is flagged unless Jev
  confidently chose the same option. A value only Jev wanted stays blank, since Jev reads more into a
  note than is written (an Abn flag of "Normal" for a plain number).
- A typed value is flagged unless Jev agrees the note states it.
- An empty field is flagged when Jev is confident the note states it (above 0.85).
- A value that does not stick after a refill is flagged.

System One is TypeSafe's Jev when `TYPESAFE_API_KEY` is set, and otherwise d1-3B on the local service.
jev-ultrafast's own requests are redirected there, since d1 takes the same request shape. System Two is
Qwen3.8-27B on Cerebras, at temperature 0 with reasoning "low" for both agents. Override with
`LLM_REASONING`, `LLM_TEMPERATURE`, `TEXT_MODEL`, and `LLM_VOTES` (several extractions; disagreements are
flagged).

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

The console puts mapfill in front of a person. Paste a visit note, or use a sample patient, choose an EHR
screen and fill it. Every value it entered is listed next to the words in the note it came from, which are
highlighted when you point at the field. Flagged fields say why. Keep the value or type a correction, which
is entered on the page and read back. Save stays locked until every flag is reviewed, and the server
refuses it too. Saving writes to the EHR: a new patient, or vitals on the Bench Fixture visit.

[app.py](app.py) runs the browser on one worker thread and streams each step to the page.
[static/](static) holds the page. One browser serves the console, so it is for one reviewer at a time.

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
