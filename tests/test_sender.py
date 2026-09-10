"""
test_sender.py
---------------
Phase 11 — Threading verification tests for step6_sender.py.

Ye file 2 hisso mein hai:
1. AUTOMATED unit tests — MIME message ke headers (In-Reply-To,
   References, subject, to) sahi ban rahe hain ya nahi, bina kisi real
   Gmail call ke.
2. MANUAL live test (__main__ block) — real Gmail se apne hi doosre
   address pe ek test reply bhejta hai, taake thread continuity real
   mein confirm ho sake (guide ka Phase 11 checklist item).

Run automated tests: python -m tests.test_sender
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.step6_sender import _build_reply_message, move_to_spam


def test_reply_subject_gets_re_prefix():
    email = {"subject": "Question about pricing", "from": "a@b.com", "message_id_header": "", "references": ""}
    message = _build_reply_message(email, "draft text")
    assert message["subject"] == "Re: Question about pricing"
    print("PASS: subject gets 'Re:' prefix")


def test_reply_subject_not_double_prefixed():
    email = {"subject": "Re: Question about pricing", "from": "a@b.com", "message_id_header": "", "references": ""}
    message = _build_reply_message(email, "draft text")
    assert message["subject"] == "Re: Question about pricing"
    print("PASS: subject not double-prefixed with 'Re:'")


def test_in_reply_to_header_set():
    email = {
        "subject": "Test",
        "from": "a@b.com",
        "message_id_header": "<original123@mail.gmail.com>",
        "references": "",
    }
    message = _build_reply_message(email, "draft text")
    assert message["In-Reply-To"] == "<original123@mail.gmail.com>"
    print("PASS: In-Reply-To header correctly set")


def test_references_header_appends_to_existing():
    email = {
        "subject": "Test",
        "from": "a@b.com",
        "message_id_header": "<new123@mail.gmail.com>",
        "references": "<old111@mail.gmail.com>",
    }
    message = _build_reply_message(email, "draft text")
    assert message["References"] == "<old111@mail.gmail.com> <new123@mail.gmail.com>"
    print("PASS: References header correctly appends to existing chain")


def test_references_header_with_no_prior_references():
    email = {
        "subject": "Test",
        "from": "a@b.com",
        "message_id_header": "<new123@mail.gmail.com>",
        "references": "",
    }
    message = _build_reply_message(email, "draft text")
    assert message["References"] == "<new123@mail.gmail.com>"
    print("PASS: References header set correctly when no prior chain exists")


def test_to_field_set_to_sender():
    email = {"subject": "Test", "from": "customer@example.com", "message_id_header": "", "references": ""}
    message = _build_reply_message(email, "draft text")
    assert message["to"] == "customer@example.com"
    print("PASS: 'to' field correctly set to original sender")


class _FakeMessagesModify:
    """Fake Gmail API chain: service.users().messages().modify(...).execute()"""

    def __init__(self):
        self.calls = []

    def modify(self, userId, id, body):
        self.calls.append({"userId": userId, "id": id, "body": body})
        return self

    def execute(self):
        return {}


class _FakeUsers:
    def __init__(self, messages_obj):
        self._messages_obj = messages_obj

    def messages(self):
        return self._messages_obj


class _FakeService:
    def __init__(self):
        self._messages_obj = _FakeMessagesModify()

    def users(self):
        return _FakeUsers(self._messages_obj)


def test_move_to_spam_adds_spam_label_and_removes_inbox_unread():
    fake_service = _FakeService()
    email = {"id": "msg123"}
    move_to_spam(fake_service, email)

    call = fake_service._messages_obj.calls[0]
    assert call["id"] == "msg123"
    assert call["body"]["addLabelIds"] == ["SPAM"]
    assert set(call["body"]["removeLabelIds"]) == {"INBOX", "UNREAD"}
    print("PASS: move_to_spam() adds SPAM label and removes INBOX/UNREAD (moves email to Spam folder)")


if __name__ == "__main__":
    test_reply_subject_gets_re_prefix()
    test_reply_subject_not_double_prefixed()
    test_in_reply_to_header_set()
    test_references_header_appends_to_existing()
    test_references_header_with_no_prior_references()
    test_to_field_set_to_sender()
    test_move_to_spam_adds_spam_label_and_removes_inbox_unread()
    print("\nALL AUTOMATED SENDER TESTS PASSED (7/7)")

    print("\nFor a LIVE end-to-end test (real send to your own second address), see guide.md")
    print("Phase 11 checklist, or run: python -m src.step6_sender")
