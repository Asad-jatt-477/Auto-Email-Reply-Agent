"""CLI entry point."""

import main
from src.step3_state import get_sender_feedback_verdict


def test_feedback_command(capsys):
    assert main.main(["feedback", "Spammer@X.com", "spam"]) == 0
    assert main.main(["feedback", "spammer@x.com", "spam"]) == 0
    assert get_sender_feedback_verdict("spammer@x.com") == "spam"
    assert "spam_votes=2" in capsys.readouterr().out


def test_auth_problem_exits_cleanly(monkeypatch, tmp_path):
    from src import config

    monkeypatch.delenv(config.TOKEN_JSON_B64_ENV, raising=False)
    monkeypatch.setattr(config, "TOKEN_FILE", str(tmp_path / "none.json"))
    assert main.main(["export-token"]) == 2


def test_dry_run_once_uses_pipeline(monkeypatch, gmail):
    import src.orchestrator as orch
    from src import step1_auth
    from tests.conftest import make_agent

    gmail.add_message("m1", body="What are your opening hours on Saturday?")
    monkeypatch.setattr(step1_auth, "get_gmail_service", lambda: gmail)
    real = orch.run_cycle
    monkeypatch.setattr(orch, "run_cycle", lambda s, **kw: real(s, agent_fn=make_agent(), **kw))
    assert main.main(["--once", "--dry-run"]) == 0
    assert gmail.sent == []