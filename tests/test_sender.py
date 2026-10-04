"""Sender: threading headers, encoding, Reply-To, RFC 3834, labels."""

from email import message_from_bytes
from email.policy import default

from src.step6_sender import (_build_reply_message, create_reply_draft, get_label_id, modify_labels,
                              move_to_spam, send_reply, triage_label_names)

BASE = {"id": "m1", "thread_id": "t1", "from": "Customer <c@example.com>", "subject": "Question",
        "message_id_header": "<orig@mail.com>", "references": ""}


def parsed(msg):
    return message_from_bytes(msg.as_bytes(), policy=default)


def test_subject_prefix_not_doubled():
    assert parsed(_build_reply_message(BASE, "x"))["Subject"] == "Re: Question"
    assert parsed(_build_reply_message({**BASE, "subject": "RE: Question"}, "x"))["Subject"] == "RE: Question"


def test_threading_headers():
    m = parsed(_build_reply_message({**BASE, "references": "<a@x> <b@x>"}, "x"))
    assert m["In-Reply-To"] == "<orig@mail.com>"
    assert m["References"] == "<a@x> <b@x> <orig@mail.com>"


def test_reply_goes_to_reply_to_when_present():
    m = parsed(_build_reply_message({**BASE, "reply_to": "form@site.com"}, "x"))
    assert m["To"] == "form@site.com"
    assert parsed(_build_reply_message(BASE, "x"))["To"] == "Customer <c@example.com>"


def test_auto_submitted_header_rfc3834():
    assert parsed(_build_reply_message(BASE, "x"))["Auto-Submitted"] == "auto-replied"
    assert parsed(_build_reply_message(BASE, "x", auto_generated=False))["Auto-Submitted"] is None


def test_urdu_subject_and_body_round_trip():
    subject = "\u0645\u06cc\u0679\u0646\u06af \u06a9\u0627 \u0648\u0642\u062a"
    body = "\u062c\u06cc \u0628\u0627\u0644\u06a9\u0644\u060c \u06a9\u0644 3 \u0628\u062c\u06d2"
    raw = _build_reply_message({**BASE, "subject": subject}, body).as_bytes()
    raw.decode("ascii")                       # wire format must be 7-bit safe
    m = message_from_bytes(raw, policy=default)
    assert m["Subject"] == f"Re: {subject}"
    assert m.get_content().strip() == body


def test_send_reply_only_sends(gmail):
    gmail.add_message("m1")
    send_reply(gmail, {**BASE, "thread_id": "t-m1"}, "Hello")
    assert len(gmail.sent) == 1 and gmail.sent[0]["threadId"] == "t-m1"
    assert "modify" not in gmail.calls       # labelling is the orchestrator's job


def test_label_ids_are_cached(gmail):
    first = get_label_id(gmail, "AI-Processed")
    again = get_label_id(gmail, "AI-Processed")
    assert first == again
    assert gmail.calls.count("labels.create") == 1 and gmail.calls.count("labels.list") == 1


def test_modify_labels_and_triage_names(gmail):
    gmail.add_message("m1")
    modify_labels(gmail, "m1", add_names=triage_label_names("invoice_payment", "high"), remove_ids=["UNREAD"])
    assert set(gmail.label_names("m1")) >= {"Category-Invoice-Payment", "Priority-High"}
    assert "UNREAD" not in gmail.label_names("m1")


def test_draft_is_created_in_thread(gmail):
    create_reply_draft(gmail, BASE, "Draft text")
    assert gmail.drafts[0]["message"]["threadId"] == "t1"


def test_move_to_spam(gmail):
    gmail.add_message("m1")
    move_to_spam(gmail, {"id": "m1"})
    assert "SPAM" in gmail.label_names("m1") and "INBOX" not in gmail.label_names("m1")