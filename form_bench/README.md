# Form-fill bench

How fast, cheap and accurate can an agent type a clinician's note into an EHR form? This bench runs agents
on the same synthetic patients and the same form, blocks every save, then reads the form back and scores
each field.

| Agent | How it fills a form |
|---|---|
| `jev` | [jev-ultrafast](https://github.com/browser-use/jev-ultrafast), unchanged, with the note as its goal. System One picks every action; a small LLM writes each typed value. |
| `mapfill` | Reads the form once from Playwright's ARIA snapshot. System One sets every dropdown while one LLM call writes every text value, then System One checks each value against the note and flags any it cannot confirm. Playwright fills the form with no model in the loop. |

System One is TypeSafe's Jev when `TYPESAFE_API_KEY` is set, and otherwise d1-3B on the local service.
jev-ultrafast's own requests are redirected there, since d1 takes the same request shape. System Two is
Qwen3.8-27B on Cerebras by default, with reasoning off for both agents.

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
| `invented` | Fields the note left out that the agent filled anyway |
| `missed` | Fields the note stated that the agent left empty |
| `flagged`, `flagged_that_were_wrong` | Fields mapfill marked for review, and how many of those were really wrong |
| `save_attempts` | Times an agent pressed Save. Saves are blocked, but an agent should not try. |
| `system_one_calls`, `llm_calls`, `llm_tokens` | Mean per case |
| `usd_per_case` | Estimated from list prices in `bench.py`; local d1 is counted as free |

## Targets

`local` is [fixtures/intake.html](fixtures/intake.html), one page modelled on OpenEMR's new-patient and
vitals screens, so the bench runs without an EHR. Its Save button only records that it was pressed.

`openemr` logs in with `OPENEMR_URL`, `OPENEMR_USER` and `OPENEMR_PASSWORD`, blocks OpenEMR's save
endpoints, and accepts a self-signed certificate. It covers the new-patient screen with the Contact section
opened, scoring name, DOB, birth sex and address. The vitals screen needs an existing patient and encounter,
so it comes next. `bench.py map --target openemr` lists every control a screen offers, which is how the
answer keys were written.

## Limits

- All patients are synthetic. Do not point the bench at real patient data.
- jev-ultrafast handles common HTML and ARIA controls, not frames or shadow roots. Screens inside
  OpenEMR's frames need their direct URL.
- With d1-3B standing in for Jev, the `jev` baseline measures jev-ultrafast's loop with an open model.
  It is much weaker that way: on the local form it fell from 100% to 79% of fields, and stopped early on
  half the cases.
- On OpenEMR's long new-patient page, jev-ultrafast can exceed TypeSafe's request limit
  (`max_tokens_exceeded`), because every dropdown option becomes its own action in the page state.
