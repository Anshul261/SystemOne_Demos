"""Who is calling, and what they may use.

Authentication: a signed, short-lived session token (an HS256 JWT) from the login step. In production
your SSO or identity provider issues it. The token only says who the user is.

Authorization: on every request the user's live ServiceNow account decides which sub-agents they get.
A deactivated account or a removed group takes effect on their next message, with no token to revoke.
"""

import os
import re
import time
from dataclasses import dataclass

import jwt

import servicenow

# Sub-agent -> the ServiceNow group a user must belong to. None means any active user.
ACCESS = {"support": None, "appointments": "Premium Support"}
USER_NAME = re.compile(r"[\w.@-]+")


@dataclass(frozen=True)
class User:
    id: str  # ServiceNow user_name; incidents and calls are filed under it
    name: str
    permissions: frozenset[str]
    groups: frozenset[str] = frozenset()  # their ServiceNow groups, for display


class AuthError(Exception):
    pass


def issue_token(user_name: str, ttl: int = 3600) -> str:
    """The login step. A real identity provider does this after checking the user's password or SSO."""
    now = int(time.time())
    return jwt.encode({"sub": user_name, "iat": now, "exp": now + ttl}, os.environ["SESSION_SECRET"], "HS256")


def authenticate(token: str) -> User:
    try:
        claims = jwt.decode(token, os.environ["SESSION_SECRET"], algorithms=["HS256"],
                            options={"require": ["sub", "exp"]})
    except jwt.InvalidTokenError as error:
        raise AuthError(f"Invalid session: {error}") from None
    if not USER_NAME.fullmatch(claims["sub"]):
        raise AuthError("Invalid session: bad subject")
    account = servicenow.account(claims["sub"])
    if not account or not account["active"]:
        raise AuthError("No active account")
    allowed = {agent for agent, group in ACCESS.items() if group is None or group in account["groups"]}
    return User(claims["sub"], account["name"], frozenset(allowed), frozenset(account["groups"]))
