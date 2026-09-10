"""
step3_state.py
---------------
Phase 5 — State Management (Duplicate-Reply Guard).

Kaam:
- SQLite DB (data/agent_state.db) mein ek table maintain karna jo track
  kare kaunsi emails already process ho chuki hain.

Ye phase critical hai — is ke bina agent same email ko baar baar reply
kar dega har polling cycle mein (kyunki Gmail ka 'unread' filter tab tak
usay dikhata rahega jab tak hum khud UNREAD label remove na karein, aur
network/logic error ki soorat mein duplicate reply ka risk rehta hai).
"""

import os
import sqlite3
from datetime import datetime, timezone

from src.config import DB_PATH, DATA_DIR


def _get_connection():
    """
    Har baar connection banate waqt pehle data/ folder ko ensure karo
    (Golden Rule: data/ folder manually nahi banana, code khud banaye),
    aur table bhi ensure karo — isse ye guarantee hoti hai ke koi bhi
    function (is_processed, get_last_reply_time, etc.) kabhi bhi call ho,
    chahe init_db() explicitly call hua ho ya nahi, "no such table" error
    kabhi nahi aayega.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            gmail_id  TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            action    TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            category    TEXT,
            priority    TEXT,
            sentiment   TEXT,
            language    TEXT,
            confidence  REAL
        )
    """)
    # Phase 12 — spam learning feedback loop: har sender ke liye
    # human-corrections ka running tally rakhta hai.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sender_feedback (
            sender        TEXT PRIMARY KEY,
            spam_votes    INTEGER NOT NULL DEFAULT 0,
            not_spam_votes INTEGER NOT NULL DEFAULT 0,
            last_updated  TEXT NOT NULL
        )
    """)
    return conn


def _ensure_columns(conn):
    """
    Purani DB files (feature add honay se pehle bani hui) mein naye
    columns nahi hongay — is function se unhe safely add kar dete hain
    taake existing users ka data/agent_state.db bhi bina crash break ho.
    """
    cursor = conn.execute("PRAGMA table_info(processed_emails)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    new_cols = {
        "category": "TEXT",
        "priority": "TEXT",
        "sentiment": "TEXT",
        "language": "TEXT",
        "confidence": "REAL",
    }
    for col, col_type in new_cols.items():
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE processed_emails ADD COLUMN {col} {col_type}")
    conn.commit()


def init_db():
    """
    Table ensure karta hai. NOTE: _get_connection() ab khud table create
    karta hai (idempotent), isliye ye function technically optional ho
    gaya hai — lekin isay explicitly rakha hai taake main.py (Phase 9)
    startup pe saaf tareeqe se "DB ready" confirm kar sake.
    """
    conn = _get_connection()
    _ensure_columns(conn)
    conn.close()


def is_processed(gmail_id: str) -> bool:
    """Check karta hai ke ye email pehle process ho chuki hai ya nahi."""
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM processed_emails WHERE gmail_id = ?", (gmail_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return row is not None


def mark_processed(
    gmail_id: str,
    thread_id: str,
    action: str,
    category: str = None,
    priority: str = None,
    sentiment: str = None,
    language: str = None,
    confidence: float = None,
):
    """
    Email ko processed mark karta hai. action mein 'reply', 'skip',
    ya 'escalate' aayega (Phase 6/9 se). category/priority/sentiment/
    language/confidence Phase 12 classification ke optional fields
    hain (guardrails-only skips ke paas ye nahi hongay, None rahenge).
    """
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO processed_emails
            (gmail_id, thread_id, action, timestamp, category, priority, sentiment, language, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            gmail_id, thread_id, action, datetime.now(timezone.utc).isoformat(),
            category, priority, sentiment, language, confidence,
        ),
    )
    conn.commit()
    conn.close()


def get_last_reply_time(thread_id: str):
    """
    Phase 7 (Guardrails) ke rate-limit check ke liye — is thread pe
    aakhri baar kab reply hua tha, wo ISO timestamp string return karta
    hai. Agar kabhi reply nahi hua to None.
    """
    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT timestamp FROM processed_emails
        WHERE thread_id = ? AND action = 'reply'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (thread_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


# ══════════════════════════════════════════════════════════════════
# Phase 12 — Spam Learning Feedback Loop
# ══════════════════════════════════════════════════════════════════
# Idea: jab bhi guardrails ka spam/skip filter GALTI kare (ya sahi
# kare), insaan ek chhota CLI command chala kar correction de sakta
# hai:  python main.py feedback <sender_email> spam|not_spam
#
# Har correction is table mein tally hoti hai. step5_guardrails isay
# check karta hai:
#   - agar kisi sender ke spam_votes >= SPAM_FEEDBACK_THRESHOLD aur
#     spam_votes > not_spam_votes -> future emails hard-skip.
#   - agar not_spam_votes zyada hain -> future emails ko auto-skip
#     filters se WHITELIST kar diya jata hai (agli baar LLM tak jayengi).
# Isse system har correction se "seekhta" jata hai, bina kisi extra
# LLM/token cost ke (pure SQLite lookup hai).

SPAM_FEEDBACK_THRESHOLD = 2  # itni corrections ke baad learned rule lagu hoti hai


def record_sender_feedback(sender: str, is_spam: bool):
    """
    Human correction record karta hai. sender = email address (lowercase
    normalize karke store hota hai), is_spam=True matlab "ye spam thi",
    False matlab "ye spam NAHI thi, galti se skip/mark hui".
    """
    sender = (sender or "").strip().lower()
    if not sender:
        raise ValueError("sender khaali nahi ho sakta")

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT spam_votes, not_spam_votes FROM sender_feedback WHERE sender = ?", (sender,))
    row = cursor.fetchone()
    spam_votes, not_spam_votes = row if row else (0, 0)

    if is_spam:
        spam_votes += 1
    else:
        not_spam_votes += 1

    cursor.execute(
        """
        INSERT OR REPLACE INTO sender_feedback (sender, spam_votes, not_spam_votes, last_updated)
        VALUES (?, ?, ?, ?)
        """,
        (sender, spam_votes, not_spam_votes, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    return spam_votes, not_spam_votes


def get_sender_feedback_verdict(sender: str):
    """
    Ek sender ke liye learned verdict return karta hai:
    "spam"     -> is sender ki emails hard-skip karo
    "not_spam" -> is sender ko auto-skip filters se whitelist karo
    None       -> is sender par abhi tak koi feedback nahi hai (neutral)
    """
    sender = (sender or "").strip().lower()
    if not sender:
        return None

    conn = _get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT spam_votes, not_spam_votes FROM sender_feedback WHERE sender = ?", (sender,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return None

    spam_votes, not_spam_votes = row
    if spam_votes >= SPAM_FEEDBACK_THRESHOLD and spam_votes > not_spam_votes:
        return "spam"
    if not_spam_votes >= SPAM_FEEDBACK_THRESHOLD and not_spam_votes > spam_votes:
        return "not_spam"
    return None


if __name__ == "__main__":
    # Standalone test: python -m src.step3_state
    init_db()
    print(f"Database ready at: {DB_PATH}")

    test_id = "test_gmail_id_123"
    test_thread = "test_thread_456"

    print(f"is_processed('{test_id}') before marking -> {is_processed(test_id)}")
    mark_processed(test_id, test_thread, "skip")
    print(f"is_processed('{test_id}') after marking  -> {is_processed(test_id)}")
    print(f"get_last_reply_time('{test_thread}') (no reply yet) -> {get_last_reply_time(test_thread)}")

    mark_processed(test_id, test_thread, "reply")
    print(f"get_last_reply_time('{test_thread}') (after reply)  -> {get_last_reply_time(test_thread)}")

    print("\n--- Phase 12: spam feedback loop test ---")
    test_sender = "test_spammer@example.com"
    print(f"verdict before any feedback -> {get_sender_feedback_verdict(test_sender)}")
    record_sender_feedback(test_sender, is_spam=True)
    record_sender_feedback(test_sender, is_spam=True)
    print(f"verdict after 2x 'spam' feedback -> {get_sender_feedback_verdict(test_sender)}")
