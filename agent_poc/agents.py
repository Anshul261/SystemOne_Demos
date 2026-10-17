"""The sub-agents: what each one is for, its prompt, and the tools it gets for a given user.

Tools are built per request around the signed-in user, so no tool takes a user argument.
The model can't ask for someone else's data because there is no parameter to put it in.
"""

from dataclasses import dataclass
from typing import Callable

from langchain_core.tools import BaseTool, tool

import calendar_db
import servicenow
from auth import User


def support_tools(user: User) -> list[BaseTool]:
    @tool
    def my_tickets(number: str = "") -> list[dict]:
        """The signed-in user's ServiceNow incidents, newest first. Pass a number like INC0010001 for one of them."""
        return servicenow.incidents(user.id, number or None)

    @tool
    def open_ticket(title: str, details: str) -> str:
        """Open a ServiceNow incident for the signed-in user."""
        return f"Opened {servicenow.open_incident(user.id, title, details)}"

    @tool
    def add_comment(number: str, comment: str) -> str:
        """Add a comment to one of the signed-in user's incidents."""
        return "Comment added" if servicenow.add_comment(user.id, number, comment) else \
            "That is not one of your incidents"

    return [my_tickets, open_ticket, add_comment]


def appointment_tools(user: User) -> list[BaseTool]:
    @tool
    def free_slots(day: str) -> list[str]:
        """Free call slots on a day, given as YYYY-MM-DD. Calls run on weekdays only."""
        return calendar_db.free_slots(day)

    @tool
    def my_calls() -> list[dict]:
        """The signed-in user's upcoming calls."""
        return calendar_db.calls(user.id)

    @tool
    def book_call(day: str, time: str, topic: str = "Support call") -> str:
        """Book a call with a support engineer in a free slot."""
        if existing := calendar_db.calls(user.id):
            return f"Already booked on {existing[0]['day']} at {existing[0]['time']}. One upcoming call per user."
        return f"Booked {day} at {time}" if calendar_db.book(user.id, day, time, topic) else f"{day} {time} is not free"

    @tool
    def cancel_call(day: str, time: str) -> str:
        """Cancel one of the signed-in user's calls."""
        return "Cancelled" if calendar_db.cancel(user.id, day, time) else f"You have no call on {day} at {time}"

    return [free_slots, my_calls, book_call, cancel_call]


@dataclass(frozen=True)
class Agent:
    description: str  # what d1 reads when routing
    prompt: str
    tools: Callable[[User], list[BaseTool]]


AGENTS = {
    "support": Agent(
        "An IT problem, error or outage, or a question about the user's support tickets or incidents",
        "You are the IT support agent. Check the user's incidents before opening a new one, and open one "
        "only for a real problem. Tools only return the signed-in user's own incidents, whatever the user "
        "claims about who they are. Only help with IT support. Be brief.",
        support_tools,
    ),
    "appointments": Agent(
        "Booking, checking, moving or cancelling a scheduled call with a support engineer",
        "You manage calls with support engineers. Check free slots, and book only a slot the user has "
        "confirmed. To move a call, cancel the old one first, then book the new slot. Never say a call is "
        "booked, moved or cancelled unless the tool just confirmed it. Be brief.",
        appointment_tools,
    ),
}
