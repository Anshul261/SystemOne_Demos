# Hard note set

154 notes for the same 77 Synthea patients as [notes_v2](../notes_v2/README.md), with distractors that compete
with the real values: other people's full names (a signing clinician, a companion), a previous address, onset
and last-visit dates, a pulse on arrival before the charted repeat, a temperature taken at home, a goal or
previous weight, and a pain score written like a blood pressure. When the real value is absent, the distractor
tests that the field is left blank. Plans: [plan.py](plan.py); written by Sonnet subagents to
[../notes_v2/WRITER.md](../notes_v2/WRITER.md); checked by `../notes_v2/check.py notes_v3` (154 of 154 pass).
Made because layer 1 got every field of notes_v2's development notes right, so they no longer separate designs.

## How Jev picks a mention (layer 1, 2,926 fields, flag thresholds set on notes_v2 development notes)

| `LAYER1_PICK` | Right | Wrong | Missed | Invented | Unflagged errors | Jev tokens per screen |
|---|---|---|---|---|---|---|
| choice: one Choice listing the mentions, with "none" | 97.2% | 13 | 35 | 35 | 72 | 3,058 |
| reversed: the same, mentions listed last first | 97.2% | 13 | 36 | 34 | 72 | 3,064 |
| noul: one Noul per mention, confidence lowered by a second yes | 96.5% | 38 | 36 | 30 | 104 | 4,096 |
| noul-top: the same, confidence from the top yes alone | 96.3% | 42 | 36 | 30 | 100 | 4,084 |
| find: Choice without "none", plus a Noul for whether the note states it | 96.7% | 19 | 40 | 37 | 80 | 3,614 |

Judged alone, Nouls say yes to junk ("Patient" 0.90 as the town, a date fragment "10/20" 0.90 as a blood
pressure) and to the arrival pulse (24 to 28 pulse errors against 9), though they resist a previous address
better (4 street errors against 11). Reversing the order changed almost nothing, so the first-option lean is
small here. Most errors are the same in every mode and are not about question shape: a single reading of a
kind (a home temperature, a goal weight) is charted without asking whether it is today's, and an address
followed by a parenthesis loses its state and ZIP. This set has now been looked at; judge fixes on a new one.

## After the fixes

Choice stayed, with the other modes removed. Changes, each to what Jev is asked or what code hands it:
- Every reading is asked "was this measured at this visit?" with a "none" answer, even when it is the only one of
  its kind, under one charting rule: when a reading was taken or stated more than once, chart the latest or
  corrected value.
- Every mention goes to Jev with the words around it (names, dates, addresses and towns, not only blood
  pressures), and address and town questions ask where the patient lives now.
- Name candidates allow "McClure" and overlapping matches ("Dictation, Ullrich, Noriko"); an address ends at
  its ZIP code, a bracket or a sentence.
- A blank is flagged when Jev is unsure the note gives no value. Flags below 0.6, set on these notes and the
  notes_v2 development notes.

This set became development data. Measured once on [notes_v4](../notes_v4/README.md), new to both designs:

| notes_v4, 2,926 fields | Right | Wrong | Missed | Invented | Unflagged errors | Flags | Jev tokens per screen |
|---|---|---|---|---|---|---|---|
| Before | 98.5% | 11 | 14 | 19 | 32 | 45 | 3,040 |
| After | 99.8% | 0 | 3 | 3 | 3 | 8 | 4,762 |

The three unflagged errors are weights from the last visit, read at 0.66 to 0.84. Context costs about 55% more
tokens (still about $0.0002 a screen) and the second request makes a screen about 0.75 s.
