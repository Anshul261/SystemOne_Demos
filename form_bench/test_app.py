"""The review console's locks: no entry with an open flag, no save before entry. A stand-in session, no browser."""

from fastapi.testclient import TestClient

import app
from targets import Screen

client = TestClient(app.app)


class Page:
    closed = False

    def close(self):
        self.closed = True


def session(flags: dict, entered: bool) -> dict:
    saved = []
    screen = Screen("about:blank", {}, title="Test", save=lambda page: saved.append(page) or "Saved")
    fields = [{"role": "textbox", "name": "First Name", "row": None, "nth": 0, "options": []}]
    data = {"id": "s1", "target": "local", "name": "the local intake form", "screen": screen, "page": Page(),
            "fields": fields, "values": {0: "Ann"}, "flags": flags, "confidence": {0: 0.4}, "resolved": set(),
            "entered": entered,
            "saved": saved}
    app.sessions.clear()
    app.sessions["s1"] = data
    return data


def test_entry_is_refused_while_a_flag_is_open():
    session({0: "The checker could not find this in the note"}, entered=False)
    response = client.post("/api/enter", json={"session": "s1"})
    assert response.status_code == 409 and "Review 1 more field" in response.json()["detail"]


def test_save_is_refused_before_entry():
    data = session({}, entered=False)
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 409 and data["saved"] == []


def test_save_is_refused_while_a_flag_is_open():
    data = session({0: "The page did not keep this value"}, entered=True)
    assert client.post("/api/save", json={"session": "s1"}).status_code == 409
    assert data["saved"] == [] and not data["page"].closed


def test_save_goes_through_once_entered_and_reviewed():
    data = session({0: "The checker could not find this in the note"}, entered=True)
    assert client.post("/api/confirm", json={"session": "s1", "index": 0}).json()["row"]["resolved"]
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 200 and response.json() == {"saved": "Saved"}
    assert data["saved"] and data["page"].closed and "s1" not in app.sessions


def test_a_correction_before_entry_changes_only_the_value():
    data = session({0: "The checker could not find this in the note"}, entered=False)
    row = client.post("/api/edit", json={"session": "s1", "index": 0, "value": "Anna"}).json()["row"]
    assert row["value"] == "Anna" and row["resolved"] and data["values"][0] == "Anna"


def test_an_ended_review_cannot_be_saved():
    app.sessions.clear()
    assert client.post("/api/save", json={"session": "gone"}).status_code == 404
