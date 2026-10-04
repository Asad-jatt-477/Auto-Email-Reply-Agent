"""Gmail label-name conflicts: labels created by older versions must be reused,
and an optional label problem must never block the essential labels."""

import pytest

from src.orchestrator import run_cycle
from src.step3_state import get_processed
from src.step6_sender import get_label_id
from tests import fakes
from tests.conftest import make_agent


@pytest.fixture
def gmail_with_conflicts(gmail, monkeypatch):
    """Fake that rejects names Gmail would see as duplicates (case / space / hyphen)."""
    original = fakes._Labels.create

    def create(self, userId, body):
        norm = lambda n: n.lower().replace(" ", "-")
        if any(norm(n) == norm(body["name"]) for n in self.g.label_store.values()):
            def boom():
                raise RuntimeError("HttpError 409: Label name exists or conflicts")
            return fakes._Exec(boom)
        return original(self, userId, body)

    monkeypatch.setattr(fakes._Labels, "create", create)
    gmail.label_store["Label_old"] = "Category-General Query"     # made by the previous version
    return gmail


def test_existing_label_with_old_spelling_is_reused(gmail_with_conflicts):
    assert get_label_id(gmail_with_conflicts, "Category-General-Query") == "Label_old"
    assert "labels.create" not in gmail_with_conflicts.calls


def test_reply_gets_all_labels_despite_old_label_names(gmail_with_conflicts):
    g = gmail_with_conflicts
    g.add_message("m1", body="What are your opening hours on Saturday?")
    run_cycle(g, agent_fn=make_agent(category="general_query"))
    names = g.label_names("m1")
    assert {"AI-Processed", "AI-Replied", "Category-General Query"} <= set(names)
    assert "UNREAD" not in names
    assert run_cycle(g, agent_fn=make_agent())["fetched"] == 0      # not picked up again


def test_triage_label_failure_does_not_block_essential_labels(gmail, monkeypatch):
    from src import step6_sender

    real = step6_sender.get_label_id

    def flaky(service, name):
        if name.startswith("Category-"):
            raise RuntimeError("HttpError 409: Label name exists or conflicts")
        return real(service, name)

    monkeypatch.setattr(step6_sender, "get_label_id", flaky)
    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    run_cycle(gmail, agent_fn=make_agent(category="general_query"))
    names = gmail.label_names("m1")
    assert {"AI-Processed", "AI-Replied"} <= set(names) and "UNREAD" not in names
    assert get_processed("m1")["action"] == "reply" and len(gmail.sent) == 1
    assert run_cycle(gmail, agent_fn=make_agent())["fetched"] == 0      # not picked up again