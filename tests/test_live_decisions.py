import os

import pytest

from decision_clients import make_client
from run import RPA_DESCRIPTIONS

MODEL = os.getenv("LIVE_DECISION_MODEL", "omni")
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_LIVE_DECISION_TESTS") != "1",
    reason="set RUN_LIVE_DECISION_TESTS=1 (and LIVE_DECISION_MODEL=omni|3b|multi) to call a local model",
)


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("First Name", "first_name"),
        ("Company Name", "company_name"),
        ("Role in Company", "role_in_company"),
        ("Phone Number", "phone_number"),
    ],
)
def test_rpa_field_decisions(label, expected):
    decision = make_client(MODEL).classify_field(label, RPA_DESCRIPTIONS)
    assert decision.label == expected


def test_approved_claim_decision():
    decision = make_client(MODEL).classify(
        "Claim is approved. Fraud review is clear. Documents are complete.",
        "claim_action",
        {
            "issue_payment": "Approved claim with complete documents and clear fraud review",
            "request_documents": "Documents are missing",
            "manual_review": "An adjuster must review ambiguity",
            "refer_fraud": "Fraud indicators require investigation",
            "stop": "No action is safe",
        },
        "Choose the permitted next insurance claim action.",
    )
    assert decision.label == "issue_payment"
