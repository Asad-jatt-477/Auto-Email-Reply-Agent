"""
step3_state.py
--------------
Local SQLite state: processed messages (with the decision and its
metadata), per-message failure counts, reply timestamps for rate
limiting, spam-feedback votes and human review verdicts.

The DB path is read from config at CALL time (not import time) so tests
and the cloud deployment can point it somewhere else.
"""

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from src import config

_PROCESSED_COLUMNS = {
    "category": "TEXT", "priority": "TEXT", "sentiment": "TEXT",
    "language": "TEXT", "confidence": "REAL",
    "sender": "TEXT", "subject": "TEXT", "snippet": "TEXT",
    "reason": "TEXT", "draft_text": "TEXT", "source": "TEXT",
    "review_status": "TEXT", "human_action": "TEXT", "reviewed_at": "TEXT",
    "gmail_draft_id": "TEXT",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            gmail_id  TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            action    TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sender_feedback (
            sender         TEXT PRIMARY KEY,
            spam_votes     INTEGER NOT NULL DEFAULT 0,
            not_spam_votes INTEGER NOT NULL DEFAULT 0,
            last_updated   TEXT NOT NULL
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_failures (
            gmail_id   TEXT PRIMARY KEY,
            attempts   INTEGER NOT NULL,
            last_error TEXT,
            updated_at TEXT NOT NULL
        )""")
    existing = {row[1] for row in conn.execute("PRAGMA table_info(processed_emails)")}
    for col, col_type in _PROCESSED_COLUMNS.items():
        if col not in existing:  # migrates DBs created by older versions
            conn.execute(f"ALTER TABLE processed_emails ADD COLUMN {col} {col_type}")
    conn.commit()
    return conn


def init_db():
    with closing(_connect()):
        pass


def is_processed(gmail_id: str) -> bool:
    with closing(_connect()) as conn:
        return conn.execute(
            "SELECT 1 FROM processed_emails WHERE gmail_id = ?", (gmail_id,)
        ).fetchone() is not None


def mark_processed(gmail_id: str, thread_id: str, action: str, **meta):
    """
    Records the final action. meta may hold any of _PROCESSED_COLUMNS
    (category, priority, ..., reason, draft_text, source, review_status).
    """
    fields = {k: v for k, v in meta.items() if k in _PROCESSED_COLUMNS}
    cols = ["gmail_id", "thread_id", "action", "timestamp", *fields]
    vals = [gmail_id, thread_id, action, _now(), *fields.values()]
    with closing(_connect()) as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO processed_emails ({', '.join(cols)}) "
            f"VALUES ({', '.join('?' * len(cols))})",
            vals,
        )
        conn.execute("DELETE FROM processing_failures WHERE gmail_id = ?", (gmail_id,))
        conn.commit()


def get_processed(gmail_id: str):
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM processed_emails WHERE gmail_id = ?", (gmail_id,)).fetchone()
        return dict(row) if row else None


# -- Rate limiting --------------------------------------------------------
_REPLY_ACTIONS = ("reply", "human_reply")


def count_replies_since(thread_id: str, since_iso: str) -> int:
    with closing(_connect()) as conn:
        row = conn.execute(
            f"SELECT COUNT(*) FROM processed_emails WHERE thread_id = ? "
            f"AND action IN ({','.join('?' * len(_REPLY_ACTIONS))}) AND timestamp >= ?",
            (thread_id, *_REPLY_ACTIONS, since_iso),
        ).fetchone()
        return int(row[0])


def get_last_reply_time(thread_id: str):
    with closing(_connect()) as conn:
        row = conn.execute(
            f"SELECT timestamp FROM processed_emails WHERE thread_id = ? "
            f"AND action IN ({','.join('?' * len(_REPLY_ACTIONS))}) ORDER BY timestamp DESC LIMIT 1",
            (thread_id, *_REPLY_ACTIONS),
        ).fetchone()
        return row[0] if row else None


# -- Failure tracking ------------------------------------------------------
def record_failure(gmail_id: str, error: str) -> int:
    """Increments and returns the attempt count for a failing message."""
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT attempts FROM processing_failures WHERE gmail_id = ?", (gmail_id,)
        ).fetchone()
        attempts = (row[0] if row else 0) + 1
        conn.execute(
            "INSERT OR REPLACE INTO processing_failures (gmail_id, attempts, last_error, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (gmail_id, attempts, (error or "")[:500], _now()),
        )
        conn.commit()
        return attempts


# -- Human review (dashboard) ---------------------------------------------
def list_decisions(limit: int = 500, actions=None, review_status=None) -> list:
    sql, params = "SELECT * FROM processed_emails WHERE 1=1", []
    if actions:
        sql += f" AND action IN ({','.join('?' * len(actions))})"
        params += list(actions)
    if review_status:
        sql += " AND review_status = ?"
        params.append(review_status)
    sql += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def set_review(gmail_id: str, review_status: str, human_action: str = None, draft_text: str = None):
    """review_status: pending | approved_sent | dismissed | correct | incorrect."""
    sets, params = ["review_status = ?", "reviewed_at = ?"], [review_status, _now()]
    if human_action is not None:
        sets.append("human_action = ?")
        params.append(human_action)
    if draft_text is not None:
        sets.append("draft_text = ?")
        params.append(draft_text)
    params.append(gmail_id)
    with closing(_connect()) as conn:
        conn.execute(f"UPDATE processed_emails SET {', '.join(sets)} WHERE gmail_id = ?", params)
        conn.commit()


def record_human_reply(gmail_id: str, thread_id: str):
    """A reply sent from the dashboard counts towards the thread rate limit."""
    with closing(_connect()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO processed_emails (gmail_id, thread_id, action, timestamp, source) "
            "VALUES (?, ?, 'human_reply', ?, 'dashboard')",
            (f"{gmail_id}#human_reply", thread_id, _now()),
        )
        conn.commit()


# -- Spam learning feedback loop ------------------------------------------
SPAM_FEEDBACK_THRESHOLD = 2


def record_sender_feedback(sender: str, is_spam: bool):
    sender = (sender or "").strip().lower()
    if not sender:
        raise ValueError("sender cannot be empty")
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT spam_votes, not_spam_votes FROM sender_feedback WHERE sender = ?", (sender,)
        ).fetchone()
        spam_votes, not_spam_votes = (row[0], row[1]) if row else (0, 0)
        if is_spam:
            spam_votes += 1
        else:
            not_spam_votes += 1
        conn.execute(
            "INSERT OR REPLACE INTO sender_feedback VALUES (?, ?, ?, ?)",
            (sender, spam_votes, not_spam_votes, _now()),
        )
        conn.commit()
    return spam_votes, not_spam_votes


def get_sender_feedback_verdict(sender: str):
    sender = (sender or "").strip().lower()
    if not sender:
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT spam_votes, not_spam_votes FROM sender_feedback WHERE sender = ?", (sender,)
        ).fetchone()
    if not row:
        return None
    spam_votes, not_spam_votes = row
    if spam_votes >= SPAM_FEEDBACK_THRESHOLD and spam_votes > not_spam_votes:
        return "spam"
    if not_spam_votes >= SPAM_FEEDBACK_THRESHOLD and not_spam_votes > spam_votes:
        return "not_spam"
    return None
