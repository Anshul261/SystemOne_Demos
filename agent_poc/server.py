"""Demo console: sign in as different users, chat, and watch every layer decide in a live trace.

    ./demo.sh    (or: uv run --env-file .env uvicorn server:app --port 8040)

Turns and scenarios stream newline-delimited JSON trace events from main.turn_events.
"""

import json
import os
import queue
import threading
from pathlib import Path

import httpx
import jwt
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import calendar_db
import eval_poc
import graph
import servicenow
from auth import ACCESS, AuthError, authenticate, issue_token
from eval_poc import LAYERS, forged
from main import turn_events

PERSONAS = {  # who the console can sign in as; the last four are bad sessions to show the token check
    "abel.tuter": ("Abel Tuter", "Premium Support member", lambda: issue_token("abel.tuter")),
    "beth.anglin": ("Beth Anglin", "Standard plan, no engineer calls", lambda: issue_token("beth.anglin")),
    "david.loo": ("David Loo", "Premium Support member", lambda: issue_token("david.loo")),
    "mallory": ("Mallory", "Valid signature, no ServiceNow account", lambda: issue_token("mallory")),
    "forged": ("Forged token", "Claims David Loo, signed with the wrong key", lambda: forged("david.loo")),
    "expired": ("Expired token", "David Loo, expired a minute ago", lambda: issue_token("david.loo", ttl=-60)),
    "unsigned": ("Unsigned token", "Claims David Loo with alg: none", lambda: forged("david.loo", "", "none")),
}

app = FastAPI(title="Routed support agent demo")


class SessionRequest(BaseModel):
    persona: str


class TurnRequest(BaseModel):
    token: str
    session: str
    text: str


class ScenarioRequest(BaseModel):
    name: str
    run_id: str


def ndjson(events):
    return StreamingResponse((json.dumps(e, default=str) + "\n" for e in events), media_type="application/x-ndjson")


@app.get("/api/status")
def status():
    """Everything the demo needs, checked live: the pre-demo checklist."""
    checks = {}
    try:
        url, model = graph.D1
        health = httpx.get(url.rsplit("/", 1)[0] + "/health", timeout=5).json() if not graph.LIQUID_API_KEY else {}
        checks["d1"] = {"ok": graph.LIQUID_API_KEY is not None or model in health.get("loaded", []),
                        "detail": f"{model} ({'Liquid API' if graph.LIQUID_API_KEY else 'local'})"}
    except httpx.HTTPError as error:
        checks["d1"] = {"ok": False, "detail": f"unreachable: {error}"}
    try:
        servicenow.account("abel.tuter")
        checks["servicenow"] = {"ok": True, "detail": f"{os.environ['SN_INSTANCE']} as {os.environ['SN_USER']}"}
    except Exception as error:  # noqa: BLE001 - any failure means the instance isn't usable
        checks["servicenow"] = {"ok": False, "detail": str(error)[:120]}
    checks["llm"] = {"ok": bool(os.getenv("HF_TOKEN")), "detail": os.getenv("LLM_MODEL", "Qwen/Qwen3.8-27B")}
    return {"checks": checks, "access": ACCESS, "layers": LAYERS,
            "thresholds": {"start": graph.MIN_CONFIDENCE, "switch": graph.SWITCH_CONFIDENCE,
                           "screen": graph.SCREEN_CONFIDENCE}}


@app.get("/api/personas")
def personas():
    return [{"id": key, "name": name, "note": note} for key, (name, note, _) in PERSONAS.items()]


@app.post("/api/session")
def session(request: SessionRequest):
    """Sign in as a persona: the token, what it claims, and whether the server accepts it."""
    if request.persona not in PERSONAS:
        raise HTTPException(404, "Unknown persona")
    token = PERSONAS[request.persona][2]()
    result = {"token": token, "header": jwt.get_unverified_header(token),
              "claims": jwt.decode(token, options={"verify_signature": False})}
    try:
        user = authenticate(token)
        result["auth"] = {"ok": True, "name": user.name, "permissions": sorted(user.permissions),
                          "groups": sorted(user.groups)}
    except AuthError as error:
        result["auth"] = {"ok": False, "error": str(error)}
    return result


@app.post("/api/turn")
def turn(request: TurnRequest):
    def events():
        try:
            yield from turn_events(request.token, request.text, request.session)
        except AuthError:
            pass  # the auth event already carried the reason
        except Exception as error:  # noqa: BLE001 - show the failure in the console instead of dropping the stream
            yield {"type": "error", "error": str(error)[:300]}
    return ndjson(events())


@app.get("/api/scenarios")
def scenarios():
    return [{"name": c.name, "kind": c.kind, "user": c.user, "turns": c.turns, "bad_token": c.token is not None,
             "stops": c.stops} for c in eval_poc.cases()]


@app.post("/api/scenario")
def scenario(request: ScenarioRequest):
    """Replay one eval case live, then its verdict from the same checks the eval uses."""
    case = next((c for c in eval_poc.cases() if c.name == request.name), None)
    if case is None:
        raise HTTPException(404, "Unknown scenario")
    events = queue.Queue()

    def streamed_ask(token, text, session):
        events.put({"type": "user", "text": text})
        for event in turn_events(token, text, session):
            events.put(event)
        return event

    def work():
        try:
            result = eval_poc.run(case, request.run_id, ask=streamed_ask)
            events.put({"type": "verdict", "passed": not result["failures"], "failures": result["failures"],
                        "stopped_by": result.get("stopped_by")})
        except Exception as error:  # noqa: BLE001
            events.put({"type": "error", "error": str(error)[:300]})
        events.put(None)

    threading.Thread(target=work, daemon=True).start()
    return ndjson(iter(events.get, None))


@app.post("/api/reset")
def reset():
    """Empty the calendar before a demo run. ServiceNow incidents are left as they are."""
    calendar_db.reset()
    return {"ok": True}


app.mount("/", StaticFiles(directory=Path(__file__).with_name("static"), html=True), name="static")
