"""State DB: isolation from real data/, schema migration, counters."""

import os
import sqlite3
from datetime import datetime, timedelta, timezone

from src import config
from src.step3_state import (count_replies_since, get_processed, init_db, is_processed, list_decisions,
                             mark_processed, record_failure, set_review)

PROJECT_DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "agent_state.db"))


# -- Bug 6: tests must never touch the real DB --------------------------------
def test_tests_use_a_temporary_db():
    init_db()
    assert os.path.abspath(config.DB_PATH) != PROJECT_DB
    assert os.path.exists(config.DB_PATH)


def test_db_path_is_read_at_call_time(tmp_path, monkeypatch):
    other = tmp_path / "other" / "x.db"
    monkeypatch.setattr(config, "DB_PATH", str(other))
    mark_processed("a", "t", "skip")
    assert other.exists() and is_processed("a")


def test_migrates_db_created_by_old_version():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with sqlite3.connect(config.DB_PATH) as conn:
        conn.execute("CREATE TABLE processed_emails (gmail_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, "
                     "action TEXT NOT NULL, timestamp TEXT NOT NULL)")
        conn.execute("INSERT INTO processed_emails VALUES ('old', 't', 'reply', '2025-01-01T00:00:00+00:00')")
    mark_processed("new", "t", "review", draft_text="d", review_status="pending")
    assert is_processed("old")
    assert get_processed("new")["draft_text"] == "d"


def test_count_replies_since():
    mark_processed("r1", "t", "reply")
    mark_processed("s1", "t", "skip")
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert count_replies_since("t", since) == 1


def test_failure_counter_resets_on_success():
    assert record_failure("m", "boom") == 1
    assert record_failure("m", "boom") == 2
    mark_processed("m", "t", "skip")
    assert record_failure("m", "boom") == 1


def test_review_queue():
    mark_processed("e1", "t", "escalate", review_status="pending")
    mark_processed("e2", "t", "reply")
    assert [r["gmail_id"] for r in list_decisions(review_status="pending")] == ["e1"]
    set_review("e1", "dismissed", human_action="handled in Gmail")
    assert get_processed("e1")["review_status"] == "dismissed"