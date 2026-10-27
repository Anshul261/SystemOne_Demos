# Messy note set

154 synthetic visit notes for 77 Synthea patients, each with an exact answer key, for judging extraction on
notes that are harder than the bench's three templates.

| Step | File |
|---|---|
| Plan each note: the answer key, a style, and the exact text the note must contain | [plan.py](plan.py) |
| Turn plans into notes (written by Sonnet subagents following these rules) | [WRITER.md](WRITER.md), `batches/` |
| Check every planned text appears and no stray values were added; assemble the set | [check.py](check.py) |
| Score an extractor per field and per hard feature, offline | [evaluate.py](evaluate.py) |

Hard features, tagged per note: names written family-first or as "Mr./Ms. Family", middle names and
initials, dates of birth in five formats or age only, sex as "73M" or not stated, addresses with unit
variants, town only or none, blood pressure spoken or measured twice, a previous visit's vitals, kg, cm and
°C to convert, a corrected value, a relative's name, the visit date, a stopped medication, and "NKDA".

A Sonnet auditor checked 40 notes against their keys. Its six key errors (a middle initial the key missed)
are fixed; "Mr./Ms." with no stated sex accepts blank or that sex.

Patients split once: the first 20 are development (40 notes), the other 57 are the test set (114 notes),
which is only for measuring.

## Results on the test notes (2,166 fields)

| | Qwen + Jev (extraction.py) | Layer 1: Jev and code, no LLM (../layer1.py) |
|---|---|---|
| Fields right | 92.9% (96.7% without 13 screens lost to service failures) | 99.3% |
| Wrong values | 1 | 0 |
| Unflagged errors | 152 | 15 at the time; flags at 0.7, set on development notes, would have caught the 5 inferred values |
| Median per screen | 1.7 s | 0.34 s |

Limits: writers sometimes signpost the answer ("the one to chart"), relatives' names come from a small pool,
and numeric dates assume US month/day order. The test notes have now been looked at, so a fix for the
blanks layer 1 left must be judged on a new set.
