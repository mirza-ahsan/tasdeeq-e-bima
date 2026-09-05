"""SQLite log for the "mark this prediction wrong" button.

This demonstrates the biller-in-the-loop idea: a correction from a human becomes a
labelled example. We deliberately only LOG it. No retraining happens, and the demo
does not claim otherwise.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "feedback.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS feedback (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,
    session_id        TEXT NOT NULL,
    verdict           TEXT NOT NULL,
    predicted_prob    REAL,
    predicted_carc    TEXT,
    n_answered        INTEGER,
    answers_json      TEXT,
    note              TEXT
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(_SCHEMA)
    return conn


def log_feedback(session_id: str, verdict: str, predicted_prob: float | None,
                 predicted_carc: str | None, n_answered: int,
                 answers: dict, note: str | None) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO feedback (created_at, session_id, verdict, predicted_prob, "
            "predicted_carc, n_answered, answers_json, note) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), session_id, verdict, predicted_prob,
             predicted_carc, n_answered, json.dumps(answers, default=str), note))
        return int(cur.lastrowid)


def count() -> int:
    with _connect() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0])
