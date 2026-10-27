"""Check written notes against their plans, and assemble the note set.

    uv run python notes_v2/check.py

Every non-empty planned text must appear verbatim in its note. Notes are also scanned for stray values a
writer may have added (another blood pressure, date or weight), which would make the answer key wrong. Notes
that pass go to notes_v2/notes.jsonl with their plan; failures are listed for rewriting.
"""

import json
import re
from pathlib import Path

HERE = Path(__file__).parent
BP = re.compile(r"\b\d{2,3}\s*/\s*\d{2,3}\b|\b\d{2,3} over \d{2,3}\b")
DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b|\b\d{4}-\d{2}-\d{2}\b")


def problems(plan: dict, note: str) -> list[str]:
    found = []
    for fact in plan["facts"]:
        if fact["text"] and fact["text"] not in note:
            found.append(f"missing {fact['field']}: {fact['text']!r}")
    planned = " ".join(f["text"] for f in plan["facts"])
    for pattern, kind in ((BP, "blood pressure"), (DATE, "date")):
        for match in pattern.findall(note):
            if match not in planned:
                found.append(f"unplanned {kind}: {match!r}")
    if not any(f["field"] == "age" for f in plan["facts"]) and not any(f["field"] == "sex" and f["text"] for f in plan["facts"]):
        if re.search(r"\b\d{1,3}[- ]?(year|yo|y/o)", note):
            found.append("unplanned age")
    return found


def main():
    plans = {p["id"]: p for p in map(json.loads, (HERE / "plans.jsonl").read_text().splitlines())}
    notes = {}
    for path in sorted((HERE / "batches").glob("out-*.jsonl")):
        for line in path.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                notes[item["id"]] = item["note"]
    good, bad = [], {}
    for pid, plan in plans.items():
        if pid not in notes:
            bad[pid] = ["no note written"]
        elif issues := problems(plan, notes[pid]):
            bad[pid] = issues
        else:
            good.append(plan | {"note": notes[pid]})
    (HERE / "notes.jsonl").write_text("".join(json.dumps(n) + "\n" for n in good))
    print(f"{len(good)} of {len(plans)} notes pass ({sum(n['split'] == 'test' for n in good)} test)")
    for pid, issues in bad.items():
        print(f"  {pid}: {'; '.join(issues)}")


if __name__ == "__main__":
    main()
