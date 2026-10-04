"""Evaluation framework: dataset integrity, guardrail layer on real data, metric math."""

import json

from evaluation import run_eval
from evaluation.run_eval import compute_metrics, load_dataset, to_email
from src.step5_guardrails import check_guardrails


def test_dataset_is_well_formed():
    rows = load_dataset()
    assert len(rows) >= 50
    assert len({r["id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["expected_action"] in {"reply", "escalate", "skip", "spam"}
        assert r["guardrail_expected"] in {"proceed", "skip", "escalate", "spam"}
        if r["guardrail_expected"] != "proceed":
            assert r["expected_action"] == r["guardrail_expected"]
    actions = {r["expected_action"] for r in rows}
    assert actions == {"reply", "escalate", "skip", "spam"}
    assert {"english", "urdu", "roman_urdu"} <= {r.get("expected_language") for r in rows}


def test_guardrail_layer_matches_every_label_in_dataset():
    run_eval.isolate_environment()
    wrong = [(r["id"], r["guardrail_expected"], check_guardrails(to_email(r))[0])
             for r in load_dataset() if check_guardrails(to_email(r))[0] != r["guardrail_expected"]]
    assert wrong == []


def oracle_agent(mistakes=None):
    """Answers with the dataset label, except for ids listed in mistakes -> forced action."""
    rows = {r["id"]: r for r in load_dataset()}
    mistakes = mistakes or {}

    def agent(email, business_context=None):
        row = rows[email["id"]]
        action = mistakes.get(email["id"], row["expected_action"])
        tool = {"reply": "reply", "escalate": "escalate", "skip": "skip", "spam": "skip"}[action]
        return {"action": tool, "draft_text": "Thank you for your message.\n\nBest regards" if tool == "reply" else None,
                "reason": "oracle", "category": "spam" if action == "spam" else "general_query",
                "priority": "low", "sentiment": "neutral",
                "language": row.get("expected_language") or "english", "confidence": 0.9}
    return agent


def test_perfect_agent_scores_perfectly(tmp_path):
    metrics, out = run_eval.main(["--sleep", "0"], agent_fn=oracle_agent(), out_root=str(tmp_path))
    assert metrics["accuracy"] == 1.0 and metrics["macro_f1"] == 1.0
    assert metrics["unsafe_auto_reply_rate"] == 0.0 and metrics["legit_to_spam_rate"] == 0.0
    assert metrics["automation_rate"] == 1.0 and metrics["language_accuracy"] == 1.0
    assert metrics["decided_by_guardrails"] == 16
    report = open(f"{out}/report.md").read()
    assert "unsafe_auto_reply_rate" in report


def test_safety_metrics_detect_dangerous_mistakes(tmp_path):
    # h01 (needs human) auto-replied, r01 (legit) sent to spam, r02 escalated (lost automation)
    agent = oracle_agent({"h01": "reply", "r01": "spam", "r02": "escalate"})
    metrics, _ = run_eval.main(["--sleep", "0"], agent_fn=agent, out_root=str(tmp_path))
    n_human = sum(1 for r in load_dataset() if r["expected_action"] == "escalate")
    n_reply = sum(1 for r in load_dataset() if r["expected_action"] == "reply")
    n_legit = sum(1 for r in load_dataset() if r["expected_action"] != "spam")
    assert metrics["unsafe_auto_reply_rate"] == round(1 / n_human, 3)
    assert metrics["automation_rate"] == round((n_reply - 2) / n_reply, 3)
    assert metrics["legit_to_spam_rate"] == round(1 / n_legit, 3)
    assert metrics["confusion_matrix"]["human"]["reply"] == 1


def test_low_confidence_counts_as_human_not_reply():
    preds = [{"id": "a", "expected_action": "reply", "action": "review", "latency_s": 0, "source": "policy"}]
    m = compute_metrics(preds)
    assert m["confusion_matrix"]["reply"]["human"] == 1 and m["automation_rate"] == 0.0


def test_errors_are_reported_not_scored():
    preds = [{"id": "a", "expected_action": "reply", "error": "RateLimitError", "latency_s": 1}]
    assert compute_metrics(preds)["n_errors"] == 1