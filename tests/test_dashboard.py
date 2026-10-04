"""Dashboard renders and its buttons work (Streamlit AppTest, fake Gmail)."""

import os

import pytest
from streamlit.testing.v1 import AppTest

from src import step1_auth
from src.orchestrator import run_cycle
from src.step3_state import get_processed
from tests.conftest import make_agent

APP = os.path.join(os.path.dirname(__file__), "..", "dashboard", "app.py")


def test_empty_dashboard_renders():
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Nothing to review" in i.value for i in at.info)


@pytest.fixture
def seeded(gmail, monkeypatch):
    gmail.add_message("m1", body="Could you give me a rough idea about bulk pricing?", subject="Bulk")
    gmail.add_message("m2", body="What are your opening hours on Saturday?", subject="Hours")
    run_cycle(gmail, agent_fn=lambda e, business_context=None: make_agent(
        confidence=0.3 if e["id"] == "m1" else 0.9)(e))
    monkeypatch.setattr(step1_auth, "get_gmail_service", lambda: gmail)
    return gmail


def test_queue_metrics_and_send(seeded):
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception
    assert any("Waiting for a human: 1" in s.value for s in at.subheader)
    assert [m.value for m in at.metric][:2] == ["2", "50%"]
    send = [b for b in at.button if b.label == "Send reply"][0]
    send.click().run()
    assert not at.exception
    assert len(seeded.sent) == 2                       # 1 auto-reply + 1 human-approved
    assert get_processed("m1")["review_status"] == "approved_sent"


def test_verdict_buttons(seeded):
    at = AppTest.from_file(APP, default_timeout=30).run()
    [b for b in at.button if b.label == "Correct"][0].click().run()
    assert not at.exception
    assert get_processed("m2")["review_status"] == "correct"