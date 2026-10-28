"""The review console's locks: no entry with an open flag, no save before entry. A stand-in session, no browser."""

from fastapi.testclient import TestClient

import app

client = TestClient(app.app)


class Page:
    closed = False

    def close(self):
        self.closed = True


def session(flags: dict, entered: bool, skip: bool = False) -> dict:
    saved = []
    fields = [{"role": "textbox", "name": "First Name", "row": None, "nth": 0, "options": []}]
    screen = {"k": 0, "title": "Test", "section": "patient", "page": Page(), "fields": fields, "values": {0: "Ann"},
              "flags": flags, "confidence": {0: 0.4}, "resolved": set(), "skip": skip, "entered": entered,
              "saved": False, "save": lambda page: saved.append(page) or "Saved"}
    data = {"id": "s1", "target": "local", "name": "the local intake form", "screens": [screen], "log": saved}
    app.sessions.clear()
    app.sessions["s1"] = data
    return data


def test_entry_is_refused_while_a_flag_is_open():
    session({0: "Jev is 40% sure of this"}, entered=False)
    response = client.post("/api/enter", json={"session": "s1"})
    assert response.status_code == 409 and "Review 1 more field" in response.json()["detail"]


def test_a_left_out_screen_does_not_hold_up_entry():
    session({0: "Jev is 40% sure of this"}, entered=False, skip=True)
    response = client.post("/api/enter", json={"session": "s1"})
    assert response.status_code == 409 and "Nothing is left to enter" in response.json()["detail"]


def test_save_is_refused_before_entry():
    data = session({}, entered=False)
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 409 and data["log"] == []


def test_save_is_refused_while_a_flag_is_open():
    data = session({0: "The page did not keep this value"}, entered=True)
    assert client.post("/api/save", json={"session": "s1"}).status_code == 409
    assert data["log"] == [] and not data["screens"][0]["page"].closed


def test_save_goes_through_once_entered_and_reviewed():
    data = session({0: "Jev is 40% sure of this"}, entered=True)
    assert client.post("/api/confirm", json={"session": "s1", "screen": 0, "index": 0}).json()["row"]["resolved"]
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 200 and response.json() == {"saved": ["Saved"]}
    assert data["log"] and data["screens"][0]["page"].closed and "s1" not in app.sessions


def test_a_correction_before_entry_changes_only_the_value():
    data = session({0: "Jev is 40% sure of this"}, entered=False)
    row = client.post("/api/edit", json={"session": "s1", "screen": 0, "index": 0, "value": "Anna"}).json()["row"]
    assert row["value"] == "Anna" and row["resolved"] and data["screens"][0]["values"][0] == "Anna"


def test_an_ended_review_cannot_be_saved():
    app.sessions.clear()
    assert client.post("/api/save", json={"session": "gone"}).status_code == 404
