"""Every decision the browser and routing demos make, run offline through the demos' own client code paths
for each of the three models, against correct answers.

Field labels are the ones the four live sites showed in past runs. ParaBank's street "Address" field is
expected to receive the street address (GLiNER2.5-Decide put the password there; the sandbox accepted it).

Usage: python browser_decisions.py [omni 3b multi]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import intake  # noqa: E402
import run  # noqa: E402
from decision_clients import make_client  # noqa: E402

FIELDS = [  # (description set, label shown on the page, correct source field)
    *[(run.RPA_DESCRIPTIONS, label, key) for label, key in [
        ("First Name", "first_name"), ("Last Name", "last_name"), ("Company Name", "company_name"),
        ("Role in Company", "role_in_company"), ("Address", "address"), ("Email", "email"), ("Phone Number", "phone_number")]],
    *[(run.REGISTRATION_DESCRIPTIONS, label, key) for label, key in [
        ("First Name", "first_name"), ("Last Name", "last_name"), ("Address", "street_address"), ("City", "city"),
        ("State", "state"), ("Zip Code", "zip_code"), ("Phone #", "phone_number"), ("SSN", "ssn"),
        ("Username", "username"), ("Password", "password"), ("Confirm", "password")]],
    *[(run.PAYMENT_DESCRIPTIONS, label, key) for label, key in [
        ("Payee Name", "payee_name"), ("Address", "street_address"), ("City", "city"), ("State", "state"),
        ("Zip Code", "zip_code"), ("Phone #", "phone_number"), ("Account #", "claim_account"),
        ("Verify Account #", "claim_account"), ("Amount: $", "settlement_amount")]],
    *[(run.PIKO_DESCRIPTIONS, label, key) for label, key in [
        ("First Name", "first_name"), ("Last Name", "last_name"), ("Date of Birth", "date_of_birth"), ("Gender", "gender"),
        ("Street", "street"), ("House / Apt Number", "house_number"), ("City", "city"), ("Postal Code", "postal_code"),
        ("Country", "country"), ("Email", "email"), ("Phone", "phone"), ("Contact Preference", "contact_preference")]],
    *[(run.PIKO_CHALLENGE_DESCRIPTIONS, label, key) for label, key in [
        ("First Name", "first_name"), ("Last Name", "last_name"), ("Email", "email"), ("Phone", "phone"),
        ("Company", "company"), ("Job Title", "job_title"), ("Experience Level", "experience_level"),
        ("City", "city"), ("Country", "country")]],
]
CLAIM = {
    "issue_payment": "Approved claim with complete documents and clear fraud review",
    "request_documents": "Required claim documents are missing",
    "manual_review": "An adjuster must resolve ambiguity before payment",
    "refer_fraud": "Fraud indicators require investigation",
    "stop": "No browser action is safe",
}
CLAIMS = [
    ("Insurance claim CLM-2026-1042 is approved. Fraud review is clear. All required documents are complete. "
     "Settlement amount is $25.00.", "issue_payment"),  # the demo's own case
    ("Claim CLM-2026-1043 is approved pending paperwork: the police report and repair invoice have not been uploaded.",
     "request_documents"),
    ("Claim CLM-2026-1044: the claimant filed three similar claims this month from different addresses using the "
     "same bank account.", "refer_fraud"),
    ("Claim CLM-2026-1045: the policy covers water damage but the adjuster notes the cause may be gradual leakage, "
     "which is excluded. Documents complete.", "manual_review"),
]
ROUTING = {  # main route, urgent?, staff review?
    "CL-1041": ("book_appointment", False, None), "CL-1042": ("refill_prescription", False, None),
    "CL-1043": ("test_results", False, None), "CL-1044": ("billing_question", False, None),
    "CL-1045": ("symptom_question", True, "yes"),
    "HD-2081": ("benefits", False, None), "HD-2082": ("it_access", False, None),
    "HD-2083": ("facilities", False, None), "HD-2084": ("expense_reimbursement", False, None),
}


def main() -> None:
    models = sys.argv[1:] or ["omni", "3b", "multi"]
    report = {}
    for model in models:
        client = make_client(model)
        out = {"fields": [], "claims": [], "routing": []}
        t0 = time.perf_counter()
        for descriptions, label, want in FIELDS:
            client._field_cache.clear()
            got = client.classify_field(label, descriptions)
            out["fields"].append({"label": label, "want": want, "got": got.label, "confidence": got.confidence})
        for text, want in CLAIMS:
            got = client.classify(text=text, task="claim_action", candidates=CLAIM,
                                  prompt="Choose the permitted next insurance claim action.")
            out["claims"].append({"want": want, "got": got.label})
        for clinic in (True, False):
            schema = intake.intake_schema(clinic)
            head = "request" if clinic else "queue"
            for ticket in intake.load_tickets(clinic):
                decisions = client.classify_many(f"{ticket['subject']}\n{ticket['message']}", schema)
                want_route, urgent, review = ROUTING[ticket["id"]]
                out["routing"].append({
                    # Clinic requests are judged by the team they reach: "other" and "symptom_question" both
                    # go to clinical review.
                    "id": ticket["id"],
                    "want": intake.CLINIC_QUEUES[want_route] if clinic else want_route,
                    "got": intake.CLINIC_QUEUES[decisions[head].label] if clinic else decisions[head].label,
                    "urgent_ok": (decisions["priority"].label == "urgent") == urgent,
                    "review_ok": review is None or decisions["needs_human"].label == review,
                })
        seconds = time.perf_counter() - t0
        f_ok = sum(x["want"] == x["got"] for x in out["fields"])
        c_ok = sum(x["want"] == x["got"] for x in out["claims"])
        r_ok = sum(x["want"] == x["got"] and x["urgent_ok"] and x["review_ok"] for x in out["routing"])
        print(f"\n{model}: fields {f_ok}/{len(out['fields'])} · claims {c_ok}/{len(out['claims'])} · "
              f"routing {r_ok}/{len(out['routing'])} · {seconds:.1f} s")
        for x in out["fields"]:
            if x["want"] != x["got"]:
                print(f"   field {x['label']!r}: want {x['want']} got {x['got']} ({x['confidence']:.2f})")
        for x in out["claims"]:
            if x["want"] != x["got"]:
                print(f"   claim: want {x['want']} got {x['got']}")
        for x in out["routing"]:
            if not (x["want"] == x["got"] and x["urgent_ok"] and x["review_ok"]):
                print(f"   route {x['id']}: want {x['want']} got {x['got']} urgent_ok {x['urgent_ok']} review_ok {x['review_ok']}")
        report[model] = out
    (ROOT / "experiments/d1/results/browser_decisions.json").write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
