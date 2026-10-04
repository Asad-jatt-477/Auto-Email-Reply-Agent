"""Human review actions (dashboard backend) against the fake Gmail."""

import pytest

from src.orchestrator import run_cycle
from src.review_actions import approve_and_send, mark_handled, record_verdict
from src.step3_state import count_replies_since, get_processed
from tests.conftest import make_agent


@pytest.fixture
def reviewed(gmail):
    gmail.add_message("m1", body="Could you give me a rough idea about bulk pricing?")
    run_cycle(gmail, agent_fn=make_agent(confidence=0.3))
    assert get_processed("m1")["action"] == "review"
    return gmail


def test_approve_and_send(reviewed):
    gmail = reviewed
    approve_and_send(gmail, "m1", "Our sales team will send a quote today.\n\nBest regards")
    assert len(gmail.sent) == 1
    sent = gmail.sent[0]["msg"]
    assert sent["Auto-Submitted"] is None                     # a human wrote/approved it
    assert sent["In-Reply-To"] == "<m1@mail.example.com>"
    names = gmail.label_names("m1")
    assert "Human-Approved" in names and "Needs-Review" not in names and "UNREAD" not in names
    assert gmail.drafts == []                                 # the AI draft is cleaned up
    assert get_processed("m1")["review_status"] == "approved_sent"
    assert count_replies_since("t-m1", "2000-01-01") == 1     # counts towards rate limit


def test_cannot_send_twice_or_empty(reviewed):
    with pytest.raises(ValueError, match="empty"):
        approve_and_send(reviewed, "m1", "   ")
    approve_and_send(reviewed, "m1", "Hello there, thanks.")
    with pytest.raises(ValueError, match="already sent"):
        approve_and_send(reviewed, "m1", "Hello again")
    assert len(reviewed.sent) == 1


def test_mark_handled_and_verdict(reviewed):
    mark_handled(reviewed, "m1")
    assert "Needs-Review" not in reviewed.label_names("m1")
    assert get_processed("m1")["review_status"] == "dismissed"
    record_verdict("m1", False)
    assert get_processed("m1")["review_status"] == "incorrect"