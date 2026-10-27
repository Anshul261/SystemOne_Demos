"""The security rules, checked without d1, the LLM or ServiceNow."""

import time

import jwt
import pytest
from langgraph.checkpoint.memory import InMemorySaver

import calendar_db
import graph
import servicenow
from agents import AGENTS, appointment_tools, support_tools
from auth import AuthError, authenticate, issue_token

ACCOUNTS = {  # stand-ins for live ServiceNow accounts
    "abel.tuter": {"name": "Abel Tuter", "active": True, "groups": {"Premium Support"}},
    "beth.anglin": {"name": "Beth Anglin", "active": True, "groups": {"Service Desk"}},
    "left.company": {"name": "Left Company", "active": False, "groups": {"Premium Support"}},
}


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    monkeypatch.setenv("HF_TOKEN", "unused")
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setattr(calendar_db, "DB", tmp_path / "appointments.db")
    monkeypatch.setattr(servicenow, "caller_id", lambda user_name: f"id-{user_name}")
    monkeypatch.setattr(servicenow, "account", ACCOUNTS.get)


def login(user_name):
    return authenticate(issue_token(user_name))


@pytest.fixture
def run(monkeypatch):
    """Build a user's graph with d1 stubbed to a fixed route, and send one message."""
    def run(user_name, routed_to, intent="normal"):
        monkeypatch.setattr(graph, "decide", lambda message, previous=None: {
            "intent": {"choice": intent, "confidence": 0.9}, "agent": {"choice": routed_to, "confidence": 0.95}})
        app = graph.build_graph(login(user_name), InMemorySaver())
        return app, app.invoke({"messages": [("user", "hello")]}, {"configurable": {"thread_id": user_name}})
    return run


@pytest.mark.parametrize("token", [
    jwt.encode({"sub": "abel.tuter", "exp": time.time() + 60}, "wrong-key", "HS256"),  # forged
    jwt.encode({"sub": "abel.tuter", "exp": time.time() - 60}, "test-secret", "HS256"),  # expired
    jwt.encode({"sub": "abel.tuter", "exp": time.time() + 60}, "", "none"),  # unsigned
    jwt.encode({"sub": "abel.tuter"}, "test-secret", "HS256"),  # never expires
    "tok-abel",
])
def test_bad_sessions_are_rejected(token):
    with pytest.raises(AuthError, match="Invalid session"):
        authenticate(token)


@pytest.mark.parametrize("user_name", ["mallory", "left.company"])
def test_a_valid_token_needs_an_active_account(user_name):
    with pytest.raises(AuthError, match="No active account"):
        login(user_name)


def test_permissions_follow_servicenow_groups_on_every_request():
    assert login("abel.tuter").permissions == {"support", "appointments"}
    assert login("beth.anglin").permissions == {"support"}
    ACCOUNTS["abel.tuter"]["groups"].discard("Premium Support")  # removed in ServiceNow
    try:
        assert login("abel.tuter").permissions == {"support"}
    finally:
        ACCOUNTS["abel.tuter"]["groups"].add("Premium Support")


def test_agents_outside_the_users_plan_are_not_in_their_graph(run):
    app, state = run("beth.anglin", "appointments")
    assert "appointments" not in app.get_graph().nodes
    assert state["messages"][-1].content == graph.REPLIES["denied"]


def test_an_unclear_first_message_gets_a_question_back(run):
    _, state = run("abel.tuter", "unclear")
    assert state["messages"][-1].content == graph.REPLIES["clarify"]


def test_a_flagged_message_is_blocked_before_any_agent_runs(run):
    _, state = run("abel.tuter", "support", intent="override")
    assert state["trace"]["step"] == "blocked"
    assert state["messages"][-1].content == graph.REPLIES["blocked"]


def test_no_tool_takes_an_identity():
    for agent in AGENTS.values():
        for tool in agent.tools(login("abel.tuter")):
            assert not {"user", "user_id", "caller", "caller_id", "user_name"} & tool.args.keys()


def test_ticket_queries_are_pinned_to_the_signed_in_caller(monkeypatch):
    queries = []
    monkeypatch.setattr(servicenow, "_get", lambda table, query, fields, limit=10: queries.append(query) or [])
    my_tickets = next(t for t in support_tools(login("beth.anglin")) if t.name == "my_tickets")
    my_tickets.invoke({"number": "INC0010001"})
    my_tickets.invoke({"number": "INC0010001^ORcaller_id!=0"})  # never reaches ServiceNow
    assert queries == ["caller_id=id-beth.anglin^number=INC0010001^ORDERBYDESCsys_created_on"]


def test_users_can_only_cancel_their_own_calls_and_hold_one():
    abel, beth = ({t.name: t for t in appointment_tools(login(name))} for name in ("abel.tuter", "beth.anglin"))
    day = calendar_db.connect().execute("SELECT MIN(day) FROM slots").fetchone()[0]
    assert abel["book_call"].invoke({"day": day, "time": "09:00", "topic": "VPN"}).startswith("Booked")
    assert beth["cancel_call"].invoke({"day": day, "time": "09:00"}).startswith("You have no call")
    assert abel["book_call"].invoke({"day": day, "time": "10:00", "topic": "again"}).startswith("Already booked")
    assert calendar_db.calls("abel.tuter") == [{"day": day, "time": "09:00", "topic": "VPN"}]
