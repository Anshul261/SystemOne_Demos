"""Plans for a harder messy note set: the notes_v2 plans, plus distractors that compete with the real values.

    uv run python notes_v3/plan.py                  # writes notes_v3/plans.jsonl and notes_v3/batches/plan-*.json
    uv run python notes_v3/plan.py notes_v4 2028    # another set like it, drawn with another seed

The development notes of notes_v2 no longer separate extractors (they get every field right), so this set adds,
per note and at random: other people's full names (the signing clinician, a relative or interpreter, taken from
other Synthea patients), a previous address, the date symptoms began and the date of the last visit, a pulse on
arrival before the charted repeat, a temperature taken at home, a goal or previous weight, and a pain score
written like a blood pressure ("pain 6/10"). A distractor never changes the answer key; when the real value is
absent, it tests that the extractor leaves the field blank. Writers follow notes_v2/WRITER.md; check.py checks
the notes. The whole set is for measuring only ("fresh"); tune on notes_v2's development notes.
"""

import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "notes_v2"))
from cases import age, allergies_by_patient, latest_vitals, rows  # noqa: E402
from plan import STYLES, medications_by_patient, plan, record, us_date  # noqa: E402

HERE = ROOT / (sys.argv[1] if len(sys.argv) > 1 else "notes_v3")
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 2027
TAG = "h" if HERE.name == "notes_v3" else HERE.name.rsplit("_v", 1)[-1]  # note ids: <patient>-h0, <patient>-40, ...
RELATIONS = ["daughter", "son", "wife", "husband", "neighbour", "interpreter", "friend"]


def harden(p: dict, r: dict, rng: random.Random, others: list[dict]) -> None:
    """Add distractor facts to a plan in place. others are other patients' records, for real-looking names."""
    facts, features = p["facts"], set(p["features"])

    def fact(field, text, how, feature):
        facts.append({"field": field, "text": text, "how": how})
        features.add(feature)

    def person():
        other = rng.choice([o for o in others if o["family"] != r["family"] and o["given"] != r["given"]])
        return f"{other['given']} {other['family']}"

    if rng.random() < 0.7:
        fact("clinician", f"Dr. {person()}", "the clinician who wrote and signed the note", "distractor_clinician")
    if rng.random() < 0.5:
        fact("companion", f"{rng.choice(RELATIONS)} {person()}", "someone who came with the patient; give their full "
             "name", "distractor_full_name")
    if rng.random() < 0.35:
        other = rng.choice([o for o in others if o["city"] != r["city"]])
        fact("previous_address", f"{other['street']}, {other['city']}", "where the patient lived BEFORE moving last "
             "year; not their address now", "distractor_previous_address")
    style = rng.choice(["us", "us0", "long", "dmy", "iso"])
    if rng.random() < 0.4:
        onset = date(2026, 10, 20) - timedelta(days=rng.randint(3, 40))
        fact("onset_date", us_date(onset.isoformat(), style), "the date the symptoms began", "distractor_onset_date")
    if rng.random() < 0.3:
        last = date(2026, 10, 20) - timedelta(days=rng.randint(60, 400))
        fact("last_visit", us_date(last.isoformat(), style), "the date of the patient's last visit",
             "distractor_last_visit")
    pulse = next((f for f in facts if f["field"] == "pulse"), None)
    if pulse and rng.random() < 0.3:
        pulse["how"] = "the repeat pulse after resting; this is the one charted"
        fact("pulse_first", f"HR {int(r['pulse']) + rng.randint(15, 30)} on arrival", "the pulse on arrival, before "
             "resting", "pulse_repeat")
    if "temperature" in r and rng.random() < 0.3:
        fact("home_temperature", f"{r['temperature'] + rng.choice([1.4, 2.1, 2.8]):.1f} F at home", "the highest "
             "temperature the patient measured at home yesterday; not taken at this visit", "distractor_home_temp")
    if "weight" in r and rng.random() < 0.35:
        if rng.random() < 0.5:
            fact("goal_weight", f"goal weight {round(r['weight'] - rng.randint(15, 35))} lb", "the weight the "
                 "patient is aiming for", "distractor_goal_weight")
        else:
            fact("previous_weight", f"{r['weight'] + rng.randint(6, 14):.1f} lb at the last visit", "the weight "
                 "at the previous visit, clearly labelled so", "distractor_previous_weight")
    if rng.random() < 0.3:
        fact("pain", f"pain {rng.randint(2, 8)}/10", "a pain score out of ten", "distractor_pain_score")
    present = {f["field"] for f in facts}
    for field, feature in (("temperature", "distractor_home_temp"), ("weight", "distractor_goal_weight"),
                           ("weight", "distractor_previous_weight"), ("street", "distractor_previous_address")):
        if feature in features and field not in present:
            features.add("distractor_without_value")
    p["features"] = sorted(features)


def main():
    vitals = latest_vitals(rows("observations.csv"))
    allergies = allergies_by_patient(rows("allergies.csv"))
    meds = medications_by_patient()
    rng = random.Random(SEED)
    adults = [p for p in rows("patients.csv")
              if not p["DEATHDATE"] and p["Id"] in vitals and age(p["BIRTHDATE"]) >= 18]
    records = [(patient, record(patient, dict(vitals[patient["Id"]]), allergies.get(patient["Id"], []),
                                meds.get(patient["Id"], []))) for patient in adults]
    plans = []
    for patient, r in records:
        others = [o for _, o in records if o is not r]
        for k, style in enumerate(rng.sample(list(STYLES), 2)):
            p = plan(r, rng, f"{patient['Id'][:8]}-{TAG}{k}", patient["BIRTHDATE"])
            harden(p, r, rng, others)
            p["style"], p["split"] = style, "fresh"
            plans.append(p)
    (HERE / "plans.jsonl").write_text("".join(json.dumps(p) + "\n" for p in plans))
    batches = HERE / "batches"
    batches.mkdir(exist_ok=True)
    size = 20
    for b in range(0, len(plans), size):
        (batches / f"plan-{b // size}.json").write_text(json.dumps(plans[b:b + size], indent=1))
    features = {}
    for p in plans:
        for f in p["features"]:
            features[f] = features.get(f, 0) + 1
    print(f"{len(plans)} plans in {len(range(0, len(plans), size))} batches")
    print("features:", dict(sorted(features.items(), key=lambda kv: -kv[1])))


if __name__ == "__main__":
    main()
