"""Shared fixtures: every test gets its own throwaway DB/log dir - the real data/ is never touched."""

import os
import sys

import pytest

os.environ["LOG_TO_FILE"] = "false"      # tests never write to logs/
os.environ.setdefault("GROQ_API_KEY", "")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import config  # noqa: E402
from src import step6_sender  # noqa: E402
from tests.fakes import FakeGmail  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "data" / "agent_state.db"))
    monkeypatch.setattr(config, "AGENT_EMAIL", "agent@mybusiness.com")
    monkeypatch.setattr(config, "SENDER_BLACKLIST", [])
    monkeypatch.setattr(config, "VIP_SENDERS", [])
    monkeypatch.setattr(config, "MARK_SKIPPED_AS_READ", False)
    step6_sender._label_cache.clear()
    yield


@pytest.fixture
def gmail():
    return FakeGmail()


def make_agent(action="reply", draft="Thank you for your email. We are open 9am-5pm.\n\nBest regards",
               confidence=0.9, category="general_query", reason=None):
    """Deterministic stand-in for the LLM agent."""
    calls = []

    def agent(email, business_context=None):
        calls.append(email["id"] if "id" in email else email.get("subject"))
        return {"action": action, "draft_text": draft if action == "reply" else None, "reason": reason,
                "category": category, "priority": "medium", "sentiment": "neutral",
                "language": "english", "confidence": confidence}

    agent.calls = calls
    return agent