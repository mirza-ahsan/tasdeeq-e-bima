"""SQLite log for the "this prediction is wrong" button.

This demonstrates the biller-in-the-loop idea: a correction from a human becomes a
labelled example. We deliberately only LOG it. No retraining happens, and the demo
does not claim otherwise.

The log holds the claim's answers, which with real data would be health information.
Here everything is synthetic; before a partner clinic's data ever reached it this would
need encryption at rest, access control and a retention period (docs/deployment.md).

`FEEDBACK_DB_PATH` moves the file, which is how the tests keep their rows out of the
real log, and how a container keeps it on a mounted volume across redeploys.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "feedback.db"

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

_ready: set[Path] = set()
_lock = threading.Lock()


def db_path() -> Path:
    override = os.getenv("FEEDBACK_DB_PATH", "").strip()
    return Path(override) if override else DEFAULT_DB_PATH


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    with _lock:
        if path not in _ready:
            conn.execute(_SCHEMA)
            conn.commit()
            _ready.add(path)
    return conn


def log_feedback(session_id: str, predicted_prob: float | None, predicted_carc: str | None,
                 n_answered: int, answers: dict, note: str | None) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO feedback (created_at, session_id, verdict, predicted_prob, "
            "predicted_carc, n_answered, answers_json, note) VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), session_id, "wrong", predicted_prob,
             predicted_carc, n_answered, json.dumps(answers, default=str), note))
        return int(cur.lastrowid)
