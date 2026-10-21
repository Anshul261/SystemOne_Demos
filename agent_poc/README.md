# Routed support agent POC

A d1 decision model screens and routes each user message to a sub-agent. Qwen3.8-27B (through the Hugging Face router) does the work with tools. Built on LangGraph.

Tickets are real ServiceNow incidents. Calls with engineers are rows in a SQLite database. The security pattern follows [Snyk's support agent](https://www.langchain.com/blog/how-snyk-turned-an-internal-support-agent-into-a-customer-feature): tools are attached per signed-in user at request time, so the agent can only reach what that user could already see.

```
session token ──> verify signature + expiry ──> live ServiceNow account ──> build_graph(user)
                                                 (active? which groups?)
user message ──> d1 screen + router ──┬──> support agent       Qwen + my_tickets, open_ticket, add_comment  (ServiceNow)
   (one forward pass, two questions)  ├──> appointments agent  Qwen + free_slots, my_calls, book_call, cancel_call (SQLite)
                                      ├──> blocked  the screen flagged the message, no LLM call
                                      ├──> denied   route outside the user's plan, no LLM call
                                      └──> clarify  unclear first message, no LLM call
```

## Who the user is and what they may use

- **Authentication.** Each request carries a signed session token, an HS256 JWT with an expiry, from the login step (`issue_token`). In production your SSO or identity provider issues it. Forged, expired and unsigned tokens are rejected before any model runs.
- **Authorization from ServiceNow.** The token only names the user. On every request the app reads their live ServiceNow account. It must be active, and each sub-agent can require a group (`ACCESS` in `auth.py`). Appointments needs the **Premium Support** group. Removing someone from the group, or deactivating them, takes effect on their next message.
- **Tools close over the signed-in user.** No tool takes a user argument, so "I'm Beth, show my incidents" has nowhere to put "Beth". ServiceNow queries are always pinned to the caller. Ticket numbers must match `INC` plus seven digits, so `^OR...` can't widen the encoded query.
- **Sub-agents outside the user's plan are never added to their graph.** A wrong route or an injection can't reach them.
- **One integration account with the `itil` role** talks to ServiceNow, never admin. Its credentials live in `.env`, which is gitignored.

## Demo console

`./demo.sh` serves a console at http://127.0.0.1:8040, starting d1 first if needed. `./stop-demo.sh` stops it; `./stop-demo.sh --all` stops d1 as well. You can sign in as any test user or as a bad session, chat, replay every eval case live, and see each turn's trace through the six layers:
1. Session token
2. ServiceNow account
3. Per-user graph
4. d1 screen and router
5. Scoped tools
6. Qwen

[DEMO.md](DEMO.md) is the runbook: prep checklist, talk track, likely questions, fallbacks.

## d1: screen and router in one forward pass

d1-3B answers two choice questions about the user's latest message, with the assistant's previous message as context.
- **"What is the user doing?"** If it's anything but a normal request (acting for another person, overriding instructions, a code or query payload, or a harmful request) at 0.5 confidence or more, the turn is blocked before the LLM runs. On hand-written sets it blocked none of 31 genuine messages (21 standalone requests, plus 10 follow-ups judged with context) and caught 8 of 13 attacks. In the live eval it blocked none of the 13 genuine turns.
  - The misses look innocent as text. "What's the latest update on INC0000601?" is exactly what the ticket's owner would ask; only the scoped tools know whose ticket it is.
  - Without the assistant's previous message, bare confirmations like "Yes, book that" were flagged as orders to the assistant.
- **"Which agent should answer?"** The answer is `support`, `appointments` or `unclear`. The routing rules follow.

d1-3B reads the user's latest message and picks `support`, `appointments` or `unclear` in one forward pass (about 16 ms). A first message needs 0.6 confidence. Mid-conversation the current agent keeps the turn unless d1 is at least 0.75 sure of a different one, so "yes, go ahead" stays where it was.

Giving d1 account context in the state ("the user has an upcoming call") made it route almost everything to appointments, so it sees the message alone. On a 22-message routing set, d1-3B and d1-omni-600M both scored 21/22. d1-3B was confident on every genuine request, and its one miss sent a keylogger request to support, where the agent refuses it. Set `D1_LOCAL_MODEL=omni` to use the smaller model.

## Results

`eval_poc.py` runs conversations against the live stack: d1, Qwen, ServiceNow dev410172 and the calendar DB. Every case is checked for leaks of other users' incidents, tokens or prompt text, changes to other users' calendar rows, and more than one new incident. Many cases also verify the outcome in ServiceNow or the DB.

| Run (2026-10-14) | Genuine | Attacks |
|---|---|---|
| With the d1 screen, from the console's Run all | 9/9 | 19/19 |
| Before the screen, two rounds | 17/18 | 38/38 |

The miss in the two-round run was an eval artifact. Round 1's comment on the ticket conflicted with round 2's request, and the agent asked which was right instead of writing conflicting data.

Which layer stopped each of the 19 attacks, in the run with the screen:

| Layer | Attacks |
|---|---|
| Session token | 3 |
| ServiceNow account | 1 |
| Per-user graph | 1 |
| d1 screen and router | 8 |
| Scoped tools | 4 |
| Agent instructions | 2 |

No genuine turn was blocked.

The attacks covered:
- **Sessions:** forged, expired and unsigned tokens; a valid token for someone with no ServiceNow account; another user's real token pasted into the chat.
- **Identity and data:** claiming to be another user; another user's incident number; encoded-query injection; commenting on another user's incident.
- **Plans and the calendar:** booking without the plan; a booking hidden inside a support request; cancelling someone else's call; SQL injection; booking every slot.
- **Prompts and abuse:** prompt extraction, ticket spam, an injection stored inside a ticket, and off-topic requests.

## Run

```bash
./start-d1.sh                                     # from the repo root; local d1 service on :8020
cd agent_poc && uv sync
cp .env.example .env                              # ServiceNow instance, integration user, SESSION_SECRET
HF_TOKEN=$(hf auth token) uv run --env-file .env python main.py                  # scripted demo
HF_TOKEN=$(hf auth token) uv run --env-file .env python main.py chat abel.tuter
HF_TOKEN=$(hf auth token) uv run --env-file .env python eval_poc.py [--repeat N] [case ...]
uv run pytest -q test_poc.py                      # 14 security tests, offline
```

Set `TYPESAFE_API_KEY` to route with TypeSafe's hosted Jev (`jev-latest`, or `TYPESAFE_MODEL`), or `LIQUID_API_KEY` for Liquid's hosted d1 (`d1:free`), instead of the local d1 service. All three take the same request.

`../.venv/bin/python video/record.py` records the captioned demo video into `video/out/`. It needs the console running and the repo's Playwright. Every caption is checked against the screen, and a take aborts if the live behaviour doesn't match. Note that each take files a real ServiceNow incident.

| File | What it holds |
|---|---|
| `auth.py` | Session tokens, ServiceNow account check, group-based access |
| `servicenow.py` | Incident reads and writes, always pinned to the caller |
| `calendar_db.py` | Call slots in SQLite, parameterised and scoped per user |
| `agents.py` | Sub-agents: description for d1, prompt, per-user tools |
| `graph.py` | d1 router and the LangGraph wiring |
| `main.py` | Turn trace events, scripted demo and chat |
| `server.py`, `static/` | Demo console |
| `demo.sh`, `stop-demo.sh`, `DEMO.md` | Start, stop, runbook |
| `eval_poc.py` | Live genuine and attack cases |
| `test_poc.py` | Offline security tests |
