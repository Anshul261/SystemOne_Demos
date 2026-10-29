# Architecture, and why it is this way

This is the record of how the chart entry system came to be built the way it is. It covers what each part
does, the rules every part keeps, and each decision with what we tried, what happened and what we chose.
The numbers come from runs on synthetic patients and a throwaway OpenEMR 8.4.1. When this file and the code
disagree, the code is right and this file needs fixing.

## The flow

A doctor writes what happened in plain words. The system turns that into saved records in an EMR, and a
person approves every value before anything is typed.

```
note ──read──▶ patient record ──plan──▶ values per screen ──review──▶ approved values
                (layer1.py)               (pipeline.py)                (app.py, a person)
                                                                              │
                     for each screen, in order ◀──────────────────────────────┘
                     reach it through the EMR's menus   (emr.py → steps.py)
                     type each value, read it back      (entry.py, forms.py)
                     check it shows the right patient   (steps.for_patient)
                     save with the EMR's own button     (steps.py)
```

| Module | Job |
|---|---|
| [layer1.py](layer1.py) | Note to patient record. Code finds candidate mentions; Jev picks among them. |
| [schema.py](schema.py) | The patient record, the same for every EMR: sections, items, attributes, units. |
| [mapping.py](mapping.py) | Which record value each screen field holds, judged by Jev from screenshots. |
| [pipeline.py](pipeline.py) | Record to the value each field takes: units, dropdown options, flags. Caches mappings. |
| [emr.py](emr.py) | Working in the EMR, screen by screen: layouts, reaching, matching, entering, saving. |
| [steps.py](steps.py) | Getting around any EMR. Jev picks steps; routes that worked are kept and replayed. |
| [entry.py](entry.py), [forms.py](forms.py) | Reading a screen's fields, typing values and reading them back. |
| [targets.py](targets.py) | Each EMR's screens, written as plain-language goals, plus sign-in and test setup. |
| [app.py](app.py), [static/](static) | The review console. |

Three files under `data/` (gitignored) hold what the system learns about an EMR. Delete any of them to make
it learn again.

| File | What it holds | Learned by |
|---|---|---|
| `data/mappings/<emr>-<k>.json` | What each field of screen k holds | Jev looking at screenshots, once per field |
| `data/layouts/<emr>-<k>.json` | Screen k's fields and their mapping, for review | Opening the screen once with the test patient |
| `data/procedures/<emr>.json` | Per goal, the steps that last reached it | Jev, the first time a goal is pursued |

## Rules every part keeps

These rules decide most questions about how a new part should behave.

1. **Jev judges, code does.** Every judgment goes to Jev: which mention, which field, which step, whether a
   goal is reached. Code does conversions, typing, reading back and bookkeeping. No local model runs, and no
   GPU.
2. **Jev never writes a value.** It picks among candidates that code found in the note, or among values the
   caller supplied. That makes every typed value traceable to the note.
3. **A person approves before entry.** The server refuses entry while any flag is open.
4. **Every value is read back.** A value the page did not keep stops the run before that screen is saved.
5. **Saving needs the right patient.** Before saving a screen in a chart, Jev must be at least 80% sure the
   page shows the note's patient (name and birth date).
6. **Logic does not depend on the EMR.** What is specific to an EMR lives in its target as goals in plain
   language. Nothing outside [targets.py](targets.py) and [labs.py](labs.py) knows OpenEMR. The UI will
   change; the logic should not.
7. **No popup is answered blindly.**
8. **Credentials never go to a model.** Sign-in is code reading `.env`.

## Decisions and what we learned

### Reading the note: pick among mentions, don't generate

Code finds every candidate (names, dates, addresses, numbers with units), then one Jev Choice per field
picks among them, with a "none of these" option. Code converts units afterwards. An LLM writing values
could invent one; a Choice can only pick something that is in the note.

We tested the TypeSafe team's suggestion of a Noul per mention ("is this the patient's weight?") against a
single Choice on 2,926 fields of hard notes. The Choice scored 97.2% with 72 unflagged errors. The per-mention
Noul scored 96.5% with 104, because judged one at a time, distractors look plausible. A "find" variant and a
reversed candidate order did no better. We kept the Choice.

Three structural changes then took the hard development set from 97.2% to 99.8%:

- Each candidate goes to Jev with the words around it, so "home temperature" can be told from today's.
- Every reading gets a "measured at this visit" question with a "none" option, so a goal weight or last
  visit's pulse is left out instead of charted.
- When a reading appears twice, the instruction says to chart the latest or corrected one (the rested
  blood pressure).

On 154 new hard notes, measured once, the result was 99.8% right with no wrong values and 3 unflagged
misses. The old design scored 98.5%, with 11 wrong values and 32 unflagged errors.

### One record, the same for every EMR

The record follows FHIR resources: patient, vitals, lab results, allergies, conditions, medications. Each
section has items (weight, glucose) that share attributes (value, unit, range, abnormal flag). A screen
field holds one attribute of one item, in one unit, or nothing. That gives the mapper a small, fixed set of
answers for any EMR. The same records go to OpenEMR or anything else.

### Mapping screens by looking at them

Jev's image input maps each field. Per screen, one question about a screenshot asks which section the
screen is for. Per field, Jev sees a crop with the field outlined in red and, inside a table, the table's
header row stacked above it. It answers three questions, about the item, the attribute and the unit. The
answer is saved, so each field is mapped once.

The first version scored 35/38, 22/33 and 98/152 on OpenEMR's new-patient, vitals and lab screens. The
items-and-attributes schema, the header strip and precise item descriptions raised that to 221/223. Both
misses were below the review threshold, so a person would have seen them.

Moving inside OpenEMR's frames surfaced a cache bug. The lab screen's per-result Code cells all looked the
same, so all 17 shared one cache key and one mapping. Fields that look alike are now told apart by order
(`pipeline.field_keys`). The score inside the frames is 263/263: 38 new patient, 33 vitals, 192 lab results.

### Dropdowns: a Choice, then a check per option

A record value ("female", "on the forehead") has to become one of a dropdown's options. A Choice over the
option texts picks one. A Noul per option then asks whether choosing it records the value. Judged alone,
that Noul is cautious on real matches ("on the forehead" as Temporal Artery scored 0.29) but near zero on
clear mismatches ("female" as Other scored 0.03). So below 0.15 the system enters no option, and below 0.5
it flags the field. Both thresholds come from six cases.

### The flow runs screen by screen, after review

The first console opened every screen up front for review. In a real EMR that cannot work, because later
screens need earlier saves: vitals belong to a visit, and a visit needs the registered patient. So review
now happens against each screen's saved layout. Entry then goes screen by screen. On the live screen each
reviewed field is found again (`emr.match`), by the same control or, failing that, by the field that holds
the same record value. A lab row's label carries its order date, so labels alone would not match.

### Getting around: Jev's steps, kept as routes

The first version opened screens by URL and saved them with scripts written for OpenEMR. Some saves called
the form's submit directly, and one re-selected the patient before saving vitals. Those scripts broke the
rule that logic should not depend on the EMR, and they were the part that would break when the UI changed.

The fix is assembled from two projects that already worked; we did not write a new agent.

- **jev-ultrafast** (browser-use) supplies the page snapshot and Jev's two step questions, which operation
  and which control. It acts only on the element it observed, after checking that element has not changed.
  `steps.py` runs it over every frame of the page, because OpenEMR is a tabbed shell of iframes.
- **browser-harness** (browser-use) keeps what an agent learns about a site as "domain skills". Ours are
  routes: per goal, the steps that reached it, saved as data. A replay follows the route without asking Jev
  while each step's control is on the page. A step that is not there, such as a different patient's search
  result, goes back to Jev. A route is rewritten whenever a run takes different steps.

Finding a patient and opening their visit's vitals form took 8 steps and 7 s with Jev choosing each one.
Replaying the route took 4.3 s with one Jev call (to confirm the goal was reached), on three fresh logins.

Several smaller rules came from watching Jev fail:

- **Jev never types free text.** When it chooses to type, a Choice picks which of the caller's values goes
  in the field (`steps.value_for`).
- **A goal is offered only the values it names.** Given every patient detail, Jev typed the test patient's
  name into a form it was only asked to open, and that typing was saved into the route.
- **Failed attempts are retried from the start, told what failed.** Jev once chose "Review" (read-only)
  instead of the visit date. The next attempt hears which paths were dead ends.
- **Three steps that change nothing end an attempt.** That rule comes from jev-ultrafast. Our version
  judges change by what the page shows, not by its address, because OpenEMR's address stays the same.

### Making Jev see what a person sees

Each of these failed on OpenEMR, and each fix is general rather than written for one screen:

| What went wrong | Fix |
|---|---|
| OpenEMR's menus are plain `div`s with a Knockout click binding, and jev-ultrafast lists only links, buttons and form controls. | Mark elements with a click binding (onclick, Knockout, Angular, Vue), focusable ones, and ones with a pointer cursor, the way browser-use's own reader does. They rank below real buttons. |
| `<button value="true">Confirm Create New Patient</button>` read as "true". | Name a button by its text, as browsers do. |
| Save buttons below the fold were invisible, so Jev clicked "Add" for previous names instead. | Show Jev the whole page, as a person filling a form sees it by scrolling. A click scrolls to its control. |
| A whole lab screen broke two request limits: 255 options per Choice (494 controls) and tokens (about 600). | Keep at most 150 controls: buttons and links first, then empty fields, then filled ones. |
| Date pickers left open after typing buried the Save button among hundreds of calendar cells. | Leave each field after typing it, as a person tabs out. |

The snapshot changes are patches applied to jev-ultrafast's source when `steps.py` loads, each with its
reason (`SNAPSHOT_PATCHES`). An assertion stops everything if jev-ultrafast changes underneath them.

### Knowing when to stop

jev-ultrafast's step question leans toward acting. Asked only to open the new-patient form, it pressed
Create New Patient on the blank form. So before every step Jev chooses, a separate Noul asks whether the
goal is reached (`steps.reached`). It sees the page and the steps already taken. The steps matter: after
Sign Results, OpenEMR's review list just empties, which shows nothing alone. Even with the steps, Jev
scored that page 0.06. So a save also counts as done once the filled form has left the screen
(`emr.submitted`): none of the controls marked at entry is still there.

### Popups

Playwright dismisses dialogs silently, and a first handler of ours accepted them all. Replaying "create a
visit" for a patient who already had one today, OpenEMR asked "A visit already exists for this patient
today. Continue?". The handler said yes and created a duplicate. Now alerts are closed and leaving a page
is allowed, but a confirm is answered by Jev against the goal, and OK only at 0.8 or above. Every answer is
logged where the step question can see it.

That handler alone cannot prevent duplicates. When the goal was literally "create a new visit", Jev
reasonably said yes. Duplicates are prevented by the flow: it creates a visit once per note, only for the
patient it just registered.

### The patient check

Before saving a screen in a chart, a Noul asks whether the page shows the note's patient. The first
wording, "is the form being filled in for this patient", gave 0.65 for the right patient. "Does the page
show this patient (same name and date of birth) as the patient it is for" gave 0.90 for the right patient
and 0.02 for a wrong name or a wrong birth date. The bar is 0.8.

### Typing and reading back

- **Each control is marked when the screen is read** (`forms.pin`). A table row's name includes its
  inputs' values, so after typing, entry could no longer find the row.
- **A value entered in the field's own unit goes in as written.** 165 cm round-tripped through inches came
  back as 165.1.
- **A date shown at midnight counts as the date typed.** OpenEMR's date-time picker rewrites `2026-10-29`
  as `2026-10-29 00:00`. The read-back accepts exactly that and nothing looser.
- **A value the page refuses stops the run before that screen is saved.** The reviewer fixes it or keeps
  it, then carries on.

### What we chose not to use

**Lightpanda** has no graphics renderer, so it cannot take the screenshots the mapper relies on. It also
passes about 80% of Chrome's web platform tests, which is risky for OpenEMR's old JavaScript. It might
speed up replays later.

**browser-harness as the browser driver** is worth adopting for deployment, because it attaches to a Chrome
the clinician is already signed into, so we would hold no EMR credentials. For test runs, Playwright does
the same job.

**Training on Jev's outputs** is out. TypeSafe's agreement (MCA section 2.3(b)) forbids using them to train
or distil another model. Where training data is needed, it comes from our own answer keys.

## What stays specific to an EMR, and why

Goals are written in plain language and never say where to click. Some do use the EMR's own words, because
without them Jev could not know which screen is meant. It could not find OpenEMR's lab results screen until
the goal named "Pending Review". It looked for "Save" on a screen whose button is "Sign Results", and on
that screen an empty list means saved.

Two things are code, and neither is entry:

- **Signing in**, so credentials never go to a model.
- **Placing lab orders** ([labs.py](labs.py)). Results only arrive for orders, and ordering is test setup,
  not part of entering a note.

## Thresholds

Every threshold is provisional. Each needs checking on screens and EMRs it was not chosen on.

| Name | Value | Where | Chosen from |
|---|---|---|---|
| `FLAG_BELOW` | 0.6 | [layer1.py](layer1.py) | Development notes: catches 3 of 7 errors, with 6 false alarms in 2,679 right fields |
| `REVIEW_BELOW` | 0.6 | [mapping.py](mapping.py) | 223 fields on 3 screens: caught both wrong mappings and one right one |
| `NO_OPTION`, `CHECK_OPTION` | 0.15, 0.5 | [pipeline.py](pipeline.py) | 6 dropdown cases |
| `REACHED` | 0.8 | [steps.py](steps.py) | A handful of OpenEMR runs |
| `POPUP_YES` | 0.8 | [steps.py](steps.py) | One popup, in a few runs |
| `PATIENT_SURE` | 0.8 | [emr.py](emr.py) | 4 cases: 0.90 right, 0.02 for each wrong one |
| `MOST` | 150 controls | [steps.py](steps.py) | The lab screen's request limits |

## Known gaps

- **Lab-note accuracy is unmeasured.** Lab reading has been tried on a few notes, not on a held-out set with
  answer keys. That set is the next thing to build.
- **One name was misread without a flag.** "Quintero-Hale, Milagros Ines" came out as the patient "Milagros
  Ines". Other versions of the same name read correctly. Name parsing needs a proper look, not a patch.
- **Duplicate patients defeat the patient check.** Two patients with the same name and birth date both pass.
  A third identifier (an MRN, or the EMR's own patient id once registered) would close this.
- **Routes keep whatever steps last worked, including needless ones.** A route recorded while a popup was
  showing kept an extra "Edit" click. The visit route later picked up a patient search it did not need and a
  doubled New Encounter click. Replays rewrite a route only when a run takes different steps, so harmless
  extra steps stay. Pruning a route (dropping a step and checking the goal is still reached) would fix this.
- **Only one EMR so far.** All the screens covered are OpenEMR's: new patient, vitals and lab results.
  Allergies, problems, medications and immunizations are next.
- **The read-back after saving is rough** (`emr.shown_after`). The real check is opening the saved record, as
  the demo video does at the end. That should become part of the flow.
