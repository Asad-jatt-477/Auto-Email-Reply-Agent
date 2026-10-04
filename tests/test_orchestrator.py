"""End-to-end pipeline against a fake Gmail: actions, failure modes, recovery."""

import os
import sqlite3

from src import config
from src.orchestrator import check_draft, decide, process_email, run_cycle
from src.step2_fetcher import fetch_unread_emails
from src.step3_state import get_processed, mark_processed
from tests.conftest import make_agent


def cycle(gmail, agent, **kw):
    return run_cycle(gmail, agent_fn=agent, **kw)


def test_reply_path(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    summary = cycle(gmail, make_agent())
    assert summary["reply"] == 1 and len(gmail.sent) == 1
    names = gmail.label_names("m1")
    assert {"AI-Replied", "AI-Processed", "Category-General-Query"} <= set(names)
    assert "UNREAD" not in names
    assert get_processed("m1")["action"] == "reply"


# -- Bug 1: escalated mail must stay unread --------------------------------
def test_escalation_keeps_unread_and_is_not_refetched(gmail):
    gmail.add_message("m1", body="I will take you to court over this invoice.")
    agent = make_agent()
    cycle(gmail, agent)
    names = gmail.label_names("m1")
    assert "UNREAD" in names and "Needs-Human" in names and "AI-Processed" in names
    assert agent.calls == []                       # forced rule: no LLM call
    assert cycle(gmail, agent)["fetched"] == 0     # excluded by the -label:ai-processed query
    assert get_processed("m1")["review_status"] == "pending"


def test_agent_escalation_keeps_unread(gmail):
    gmail.add_message("m1", body="Can you explain this odd clause in our contract?")
    cycle(gmail, make_agent(action="escalate", reason="contract question"))
    assert {"UNREAD", "Needs-Human"} <= set(gmail.label_names("m1"))


# -- Bug 4: no duplicate replies -----------------------------------------------
def test_label_failure_after_send_never_resends(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    gmail.fail["modify"] = 1                       # the call right after the send fails
    agent = make_agent()
    cycle(gmail, agent)
    assert len(gmail.sent) == 1
    assert "AI-Processed" not in gmail.label_names("m1")   # label failed...
    cycle(gmail, agent)                                     # ...message comes back
    assert len(gmail.sent) == 1                             # but is NOT answered again
    assert {"AI-Processed", "AI-Replied"} <= set(gmail.label_names("m1"))   # self-healed
    assert len(agent.calls) == 1


def test_label_lookup_failure_after_send_never_resends(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    gmail.fail["labels.list"] = 1
    cycle(gmail, make_agent())
    cycle(gmail, make_agent())
    assert len(gmail.sent) == 1


def test_send_failure_goes_to_human_and_is_not_retried(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    gmail.fail["send"] = 1
    cycle(gmail, make_agent())
    cycle(gmail, make_agent())
    assert gmail.sent == []
    row = get_processed("m1")
    assert row["action"] == "escalate" and "send failed" in row["reason"].lower()
    assert row["draft_text"]                       # human can reuse the draft
    assert {"UNREAD", "Needs-Human"} <= set(gmail.label_names("m1"))


def test_crash_between_send_and_record_is_recovered_safely(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    mark_processed("m1", "t-m1", "sending")        # simulates a process killed mid-send
    agent = make_agent()
    cycle(gmail, agent)
    assert gmail.sent == [] and agent.calls == []
    assert get_processed("m1")["action"] == "escalate"
    assert "Needs-Human" in gmail.label_names("m1")


def test_lost_database_does_not_cause_second_reply(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    cycle(gmail, make_agent())
    os.remove(config.DB_PATH)                      # e.g. redeploy without a volume
    cycle(gmail, make_agent())
    assert len(gmail.sent) == 1


# -- Review path (low confidence / output checks) --------------------------
def test_low_confidence_creates_draft_not_send(gmail):
    gmail.add_message("m1", body="Could you give me a rough idea about bulk pricing?")
    cycle(gmail, make_agent(confidence=0.4))
    assert gmail.sent == [] and len(gmail.drafts) == 1
    assert {"Needs-Review", "UNREAD", "AI-Processed"} <= set(gmail.label_names("m1"))
    row = get_processed("m1")
    assert row["action"] == "review" and row["gmail_draft_id"] == gmail.drafts[0]["id"]


def test_draft_failure_still_records_review(gmail):
    gmail.add_message("m1", body="Could you give me a rough idea about bulk pricing?")
    gmail.fail["drafts.create"] = 1
    cycle(gmail, make_agent(confidence=0.4))
    row = get_processed("m1")
    assert row["action"] == "review" and row["draft_text"] and row["gmail_draft_id"] is None


def test_invented_link_is_blocked():
    assert check_draft("Pay here: https://evil.example/pay thanks", "Our site: https://mybiz.com")
    assert check_draft("See https://mybiz.com for details, thanks.", "Our site: https://mybiz.com") is None
    assert check_draft("Mail billing@other.com please", "") is not None
    assert check_draft("x" * 3000, "")


def test_decide_routes_bad_draft_to_review():
    email = {"id": "x", "from": "a@b.com", "subject": "q", "body": "What is your website address?",
             "label_ids": ["INBOX"], "thread_id": "tx"}
    d = decide(email, agent_fn=make_agent(draft="Visit https://made-up-site.com today.\n\nBest regards"))
    assert d["action"] == "review" and "link" in d["reason"]


# -- Retries ------------------------------------------------------------------
def test_agent_errors_escalate_after_max_attempts(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")

    def broken(email, business_context=None):
        raise RuntimeError("LLM 500")

    for attempt in range(config.MAX_PROCESSING_ATTEMPTS - 1):
        cycle(gmail, broken)
        assert get_processed("m1") is None        # still retrying
    cycle(gmail, broken)
    assert get_processed("m1")["action"] == "escalate"
    assert "Needs-Human" in gmail.label_names("m1")


# -- Skip / spam / dry-run ------------------------------------------------------
def test_skip_keeps_unread_by_default(gmail):
    gmail.add_message("m1", labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS"), body="Big sale this weekend!")
    cycle(gmail, make_agent())
    assert {"UNREAD", "AI-Processed"} <= set(gmail.label_names("m1"))


def test_skip_can_mark_read_when_configured(gmail, monkeypatch):
    monkeypatch.setattr(config, "MARK_SKIPPED_AS_READ", True)
    gmail.add_message("m1", labels=("INBOX", "UNREAD", "CATEGORY_PROMOTIONS"), body="Big sale this weekend!")
    cycle(gmail, make_agent())
    assert "UNREAD" not in gmail.label_names("m1")


def test_agent_spam_is_moved_to_spam(gmail):
    gmail.add_message("m1", body="You won $1,000,000, send bank details to claim.")
    cycle(gmail, make_agent(action="skip", category="spam", reason="lottery scam"))
    names = gmail.label_names("m1")
    assert "SPAM" in names and "INBOX" not in names


def test_dry_run_changes_nothing(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    summary = cycle(gmail, make_agent(), dry_run=True)
    assert summary["reply"] == 1
    assert gmail.sent == [] and "modify" not in gmail.calls
    assert not os.path.exists(config.DB_PATH) or get_processed("m1") is None


def test_urgent_mail_is_processed_first(gmail):
    gmail.add_message("calm", body="Whenever you get time, share the brochure.")
    gmail.add_message("urgent", body="URGENT: need the quote today, deadline tonight.")
    agent = make_agent(action="escalate")
    cycle(gmail, agent)
    assert agent.calls == ["urgent", "calm"]


def test_one_bad_message_does_not_stop_the_cycle(gmail):
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    gmail.add_message("m2", body="Do you deliver to Faisalabad?")
    gmail.fail["get"] = 1
    summary = cycle(gmail, make_agent(action="escalate"))
    assert summary["errors"] == 1 and summary["escalate"] == 1   # the other message is still handled
    summary = cycle(gmail, make_agent(action="escalate"))
    assert summary["escalate"] == 1                              # the failed one is retried next cycle