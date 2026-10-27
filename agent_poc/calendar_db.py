"""Engineer call slots in SQLite. Every query is parameterised and scoped to one user id."""

import sqlite3
from datetime import date, timedelta
from pathlib import Path

DB = Path(__file__).with_name("appointments.db")
TIMES = ["09:00", "10:00", "14:30", "16:00"]


def connect() -> sqlite3.Connection:
    fresh = not DB.exists()
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    if fresh:
        reset(db)
    return db


def reset(db: sqlite3.Connection | None = None, days: int = 10) -> None:
    """Empty calendar: four slots on each of the next working days."""
    db = db or connect()
    db.executescript("DROP TABLE IF EXISTS slots;"
                     "CREATE TABLE slots (day TEXT, time TEXT, user TEXT, topic TEXT, PRIMARY KEY (day, time));")
    upcoming = [date.today() + timedelta(n) for n in range(1, days + 1)]
    db.executemany("INSERT INTO slots (day, time) VALUES (?, ?)",
                   [(d.isoformat(), t) for d in upcoming if d.weekday() < 5 for t in TIMES])
    db.commit()


def free_slots(day: str) -> list[str]:
    with connect() as db:
        return [r["time"] for r in db.execute("SELECT time FROM slots WHERE day = ? AND user IS NULL", (day,))]


def calls(user: str) -> list[dict]:
    with connect() as db:
        rows = db.execute("SELECT day, time, topic FROM slots WHERE user = ? AND day >= ? ORDER BY day, time",
                          (user, date.today().isoformat()))
        return [dict(r) for r in rows]


def book(user: str, day: str, time: str, topic: str) -> bool:
    """Claim a free slot. False if it's taken or doesn't exist."""
    with connect() as db:
        return db.execute("UPDATE slots SET user = ?, topic = ? WHERE day = ? AND time = ? AND user IS NULL",
                          (user, topic, day, time)).rowcount == 1


def cancel(user: str, day: str, time: str) -> bool:
    """Free a slot, only if this user holds it."""
    with connect() as db:
        return db.execute("UPDATE slots SET user = NULL, topic = NULL WHERE day = ? AND time = ? AND user = ?",
                          (day, time, user)).rowcount == 1
