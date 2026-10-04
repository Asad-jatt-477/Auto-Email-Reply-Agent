"""
review_actions.py
-----------------
What a human reviewer can do with an escalated / review item. Used by the
dashboard; kept separate so it is testable without Streamlit.

The original message is re-fetched from Gmail before replying, so the
reply uses the real, current threading headers.
"""

from src import config
from src.step2_fetcher import fetch_message
from src.step3_state import get_processed, record_human_reply, set_review
from src.step6_sender import get_label_id, modify_labels, send_reply

_QUEUE_LABELS = (config.NEEDS_HUMAN_LABEL, config.LOW_CONFIDENCE_LABEL)


def _queue_label_ids(service):
    return [get_label_id(service, name) for name in _QUEUE_LABELS]


def approve_and_send(service, gmail_id: str, text: str) -> dict:
    row = get_processed(gmail_id)
    if not row:
        raise ValueError(f"Unknown message {gmail_id}")
    if row.get("review_status") == "approved_sent":
        raise ValueError("A reply was already sent for this message.")
    if not (text or "").strip():
        raise ValueError("Reply text is empty.")
    email = fetch_message(service, gmail_id)
    sent = send_reply(service, email, text.strip(), auto_generated=False)
    # Record immediately after sending (same rule as the agent: never risk a double send).
    set_review(gmail_id, "approved_sent", human_action="sent from dashboard", draft_text=text.strip())
    record_human_reply(gmail_id, email.get("thread_id", ""))
    if row.get("gmail_draft_id"):
        try:
            service.users().drafts().delete(userId="me", id=row["gmail_draft_id"]).execute()
        except Exception:
            pass  # draft already sent/deleted by the user in Gmail
    modify_labels(service, gmail_id, add_names=[config.HUMAN_APPROVED_LABEL],
                  remove_ids=[*_queue_label_ids(service), "UNREAD"])
    return sent


def mark_handled(service, gmail_id: str, note: str = "handled outside the dashboard"):
    set_review(gmail_id, "dismissed", human_action=note)
    if service is not None:
        modify_labels(service, gmail_id, remove_ids=_queue_label_ids(service))


def record_verdict(gmail_id: str, correct: bool):
    """Human judgement of the AI decision -> becomes labelled data for evaluation."""
    set_review(gmail_id, "correct" if correct else "incorrect")
