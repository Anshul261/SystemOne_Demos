"""The review console's locks: no entry with an open flag, a saved screen stays as saved, and a corrected name
is the one the EMR is searched for. A stand-in session, no browser."""

from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


def session(flags: dict, skip: bool = False, saved: bool = False) -> dict:
    fields = [{"role": "textbox", "name": "First Name", "row": None, "nth": 0, "options": []}]
    screen = {"k": 0, "title": "Test", "section": "patient", "fields": fields, "values": {0: "Ann"},
              "mapping": [{"item": "given", "attribute": "value", "unit": None, "confidence": 0.4}],
              "flags": flags, "confidence": {0: 0.4}, "resolved": set(), "skip": skip, "entered": saved,
              "saved": saved}
    data = {"id": "s1", "target": "local", "name": "the local intake form", "screens": [screen],
            "record": {"given": "Ann"}, "patient": {"first name": "Ann", "last name": "Lee", "birth date": "1980-01-01"},
            "page": None, "visit": False}
    app.sessions.clear()
    app.sessions["s1"] = data
    return data


def test_entry_is_refused_while_a_flag_is_open():
    session({0: "Jev is 40% sure of this"})
    response = client.post("/api/enter", json={"session": "s1"})
    assert response.status_code == 409 and "Review 1 more field" in response.json()["detail"]


def test_a_left_out_screen_does_not_hold_up_entry():
    session({0: "Jev is 40% sure of this"}, skip=True)
    response = client.post("/api/enter", json={"session": "s1"})
    assert response.status_code == 409 and "Nothing is left to enter" in response.json()["detail"]


def test_a_saved_screen_is_not_entered_again_or_changed():
    session({}, saved=True)
    assert "Nothing is left to enter" in client.post("/api/enter", json={"session": "s1"}).json()["detail"]
    assert client.post("/api/edit", json={"session": "s1", "screen": 0, "index": 0, "value": "Anna"}).status_code == 409


def test_a_correction_before_entry_changes_only_the_value():
    data = session({0: "Jev is 40% sure of this"})
    row = client.post("/api/edit", json={"session": "s1", "screen": 0, "index": 0, "value": "Anna"}).json()["row"]
    assert row["value"] == "Anna" and row["resolved"] and data["screens"][0]["values"][0] == "Anna"


def test_a_corrected_name_is_the_patient_the_emr_is_searched_for():
    data = session({})
    client.post("/api/edit", json={"session": "s1", "screen": 0, "index": 0, "value": "Anna"})
    assert data["patient"]["first name"] == "Anna"


def test_confirming_a_flag_unlocks_entry():
    data = session({0: "Jev is 40% sure of this"})
    assert client.post("/api/confirm", json={"session": "s1", "screen": 0, "index": 0}).json()["row"]["resolved"]
    assert not app.open_flags(data["screens"][0])


def test_an_ended_review_cannot_be_entered():
    app.sessions.clear()
    assert client.post("/api/enter", json={"session": "gone"}).status_code == 404
