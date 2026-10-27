"""The review console's save lock: no model or EHR calls, a stand-in session instead."""

from fastapi.testclient import TestClient

import app
from targets import Screen, named


class Page:
    closed = False

    def close(self):
        self.closed = True


def session(flags: dict) -> dict:
    saved = []
    screen = Screen("about:blank", {}, title="Test", save=lambda page: saved.append(page) or "Saved")
    fields = [{"role": "textbox", "name": "First Name", "row": None, "nth": 0, "options": []}]
    data = {"id": "s1", "target": "local", "screen": screen, "page": Page(), "fields": fields,
            "values": {0: "Ann"}, "flags": flags, "resolved": set(), "saved": saved}
    app.sessions["s1"] = data
    return data


def test_save_is_refused_while_a_flag_is_open():
    client = TestClient(app.app)
    data = session({0: "The checker could not find this in the note"})
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 409 and "Review 1 more field" in response.json()["detail"]
    assert data["saved"] == [] and not data["page"].closed
    app.sessions.clear()


def test_save_goes_through_once_every_flag_is_reviewed(monkeypatch):
    client = TestClient(app.app)
    monkeypatch.setattr(app, "screenshot", lambda s: "")
    data = session({0: "The checker could not find this in the note"})
    assert client.post("/api/confirm", json={"session": "s1", "index": 0}).json()["row"]["resolved"]
    response = client.post("/api/save", json={"session": "s1"})
    assert response.status_code == 200 and response.json() == {"saved": "Saved"}
    assert data["saved"] and data["page"].closed and "s1" not in app.sessions


def test_an_ended_review_cannot_be_saved():
    response = TestClient(app.app).post("/api/save", json={"session": "gone"})
    assert response.status_code == 404


def test_named_fields_are_listed_by_their_label():
    data = session({})
    assert app.rows(data)[0] | {"flag": None} == {"index": 0, "label": "First Name", "kind": "text", "options": [],
                                                 "value": "Ann", "flag": None, "resolved": False}
    app.sessions.clear()
    assert named("textbox", "x")["name"] == "x"
