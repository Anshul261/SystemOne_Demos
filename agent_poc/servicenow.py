"""Incidents in ServiceNow through the Table API.

One integration account talks to the instance. Every call filters on the signed-in user's caller,
so a user can only see or add to their own incidents.
"""

import os
import re
from functools import cache

import httpx

FIELDS = "number,short_description,state,priority,sys_created_on,comments"
NUMBER = re.compile(r"INC\d{7}")  # anything else could smuggle "^OR..." into the encoded query


@cache
def client() -> httpx.Client:
    return httpx.Client(base_url=f"https://{os.environ['SN_INSTANCE']}.service-now.com/api/now/table/",
                        auth=(os.environ["SN_USER"], os.environ["SN_PASSWORD"]),
                        headers={"Accept": "application/json"}, timeout=30)


def _get(table: str, query: str, fields: str, limit: int = 10) -> list[dict]:
    response = client().get(table, params={"sysparm_query": query, "sysparm_fields": fields,
                                           "sysparm_display_value": "true", "sysparm_limit": limit})
    response.raise_for_status()
    if "json" not in response.headers.get("content-type", ""):  # a hibernating instance serves an HTML page
        raise RuntimeError("ServiceNow instance is hibernating; wake it at developer.servicenow.com")
    return response.json()["result"]


def account(user_name: str) -> dict | None:
    """The user's live account: name, active flag and groups. Read on every request, never cached."""
    users = _get("sys_user", f"user_name={user_name}", "sys_id,name,active", limit=1)
    if not users:
        return None
    groups = _get("sys_user_grmember", f"user={users[0]['sys_id']}", "group", limit=200)
    return {"name": users[0]["name"], "active": users[0]["active"] == "true",
            "groups": {g["group"]["display_value"] for g in groups}}


@cache
def caller_id(user_name: str) -> str:
    """sys_id of a ServiceNow user, looked up once."""
    (user,) = _get("sys_user", f"user_name={user_name}", "sys_id", limit=1)
    return user["sys_id"]


def incidents(user_name: str, number: str | None = None) -> list[dict]:
    """The caller's incidents, newest first; a number narrows it to one of theirs."""
    if number and not NUMBER.fullmatch(number):
        return []
    query = f"caller_id={caller_id(user_name)}" + (f"^number={number}" if number else "")
    return _get("incident", query + "^ORDERBYDESCsys_created_on", FIELDS)


def open_incident(user_name: str, short_description: str, description: str) -> str:
    response = client().post("incident", json={"caller_id": caller_id(user_name), "contact_type": "virtual_agent",
                                               "short_description": short_description, "description": description})
    response.raise_for_status()
    return response.json()["result"]["number"]


def add_comment(user_name: str, number: str, comment: str) -> bool:
    """Add a customer-visible comment, only to one of the caller's own incidents."""
    if not NUMBER.fullmatch(number):
        return False
    found = _get("incident", f"caller_id={caller_id(user_name)}^number={number}", "sys_id", limit=1)
    if not found:
        return False
    client().patch(f"incident/{found[0]['sys_id']}", json={"comments": comment}).raise_for_status()
    return True
