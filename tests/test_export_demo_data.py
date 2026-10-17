"""scripts/export_demo_data.py: correct aggregation, latest-run selection and - above all - no PII in the output."""

import json
import sqlite3

import pytest

from scripts import export_demo_data as ex

SECRET_BITS = ["victim@example.com", "Salary negotiation", "my bank password", "Hi dear customer", "gmail-id-123"]
LABELS = ["reply", "human", "skip", "spam"]


def _metrics(acc=0.9):
    cm = {r: {c: 0 for c in LABELS} for r in LABELS}
    for lab in LABELS:
        cm[lab][lab] = 5
    return {
        "n_emails": 20, "n_scored": 20, "n_errors": 0, "accuracy": acc, "macro_f1": 0.9,
        "per_class": {l: {"precision": 1.0, "recall": 1.0, "f1": 1.0, "support": 5} for l in LABELS},
        "confusion_matrix": cm, "unsafe_auto_reply_rate": 0.0, "legit_to_spam_rate": 0.0,
        "language_accuracy": 1.0, "decided_by_guardrails": 4, "llm_calls": 16, "mean_llm_latency_s": 3.2,
        "action_counts": {"reply": 5, "escalate": 5, "skip": 5, "spam": 5}, "model": "test-model",
    }


@pytest.fixture
def env(tmp_path):
    res = tmp_path / "results"
    for name, data in {"20261001-100000": _metrics(0.5),
                       "20261002-100000": {"n_emails": 20, "guardrail_accuracy": 1.0, "model": "n/a"},  # guardrail-only
                       "20261003-100000": _metrics(0.9)}.items():
        (res / name).mkdir(parents=True)
        (res / name / "metrics.json").write_text(json.dumps(data))
    (res / "20261003-100000" / "report.md").write_text("Errors\n\n- h14: RateLimitError: Error code: 429 ...\n")
    db = tmp_path / "state.db"
    c = sqlite3.connect(db)
    c.execute("""CREATE TABLE processed_emails (gmail_id TEXT PRIMARY KEY, thread_id TEXT, action TEXT, timestamp TEXT,
                 category TEXT, language TEXT, source TEXT, review_status TEXT, sender TEXT, subject TEXT,
                 snippet TEXT, reason TEXT, draft_text TEXT)""")
    rows = [("gmail-id-123", "t1", "reply", "2026-10-04T09:00:00+00:00", "general_query", "english", "agent", None),
            ("b", "t2", "skip", "2026-10-04T10:00:00+00:00", None, None, "guardrails", None),
            ("c", "t3", "escalate", "2026-10-05T10:00:00+00:00", "other", "english", "agent", "pending")]
    for gid, t, a, ts, cat, lang, src, rs in rows:
        c.execute("INSERT INTO processed_emails VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (gid, t, a, ts, cat, lang, src, rs, "victim@example.com", "Salary negotiation",
                   "my bank password", "Hi dear customer", "Hi dear customer"))
    c.commit(); c.close()
    return tmp_path, res, db


def run(tmp_path, res, db, *extra):
    out = tmp_path / "out"
    code = ex.main(["--db", str(db), "--results-dir", str(res), "--out-dir", str(out), *extra])
    return code, out


def test_exports_latest_full_pipeline_run_and_aggregates(env):
    tmp, res, db = env
    code, out = run(tmp, res, db)
    assert code == 0
    m = json.loads((out / "metrics.json").read_text())
    s = json.loads((out / "stats.json").read_text())
    assert m["run_id"] == "20261003-100000" and m["summary"]["accuracy"] == 0.9   # skips guardrail-only + older
    assert m["labels"] == LABELS and m["confusion_matrix"][1][1] == 5
    assert m["dataset"]["error_kinds"] == {"RateLimitError": 1}
    assert s["total_processed"] == 3 and s["pending_review"] == 1
    assert s["by_action"] == {"escalate": 1, "reply": 1, "skip": 1}
    assert s["by_source"] == {"agent": 2, "guardrails": 1}
    assert s["by_day"] == {"2026-10-04": 2, "2026-10-05": 1}
    assert s["window"] == {"first_date": "2026-10-04", "last_date": "2026-10-05"}


def test_output_contains_no_email_content(env):
    tmp, res, db = env
    _, out = run(tmp, res, db)
    blob = (out / "metrics.json").read_text() + (out / "stats.json").read_text()
    for bit in SECRET_BITS:
        assert bit not in blob
    assert "@" not in blob


def test_dry_run_writes_nothing(env):
    tmp, res, db = env
    code, out = run(tmp, res, db, "--dry-run")
    assert code == 0 and not out.exists()


def test_missing_db_keeps_metrics_and_skips_stats(env):
    tmp, res, db = env
    code, out = run(tmp, res, tmp / "nope.db")
    assert code == 0 and (out / "metrics.json").exists() and not (out / "stats.json").exists()


def test_no_usable_run_fails_with_code_1(tmp_path):
    (tmp_path / "r" / "20261001-000000").mkdir(parents=True)
    assert ex.main(["--results-dir", str(tmp_path / "r"), "--out-dir", str(tmp_path / "o")]) == 1


def test_privacy_guard_blocks_unsafe_strings():
    with pytest.raises(ex.PrivacyError):
        ex.assert_no_pii({"a": ["someone@example.com"]})
    with pytest.raises(ex.PrivacyError):
        ex.assert_no_pii({"subject": "Hello, here is a free-text subject line!"})
    ex.assert_no_pii({"by_action": {"reply": 3}, "model": "openai/gpt-oss-120b", "d": "2026-10-04T08:06:55"})


def test_odd_db_labels_collapse_to_other():
    assert ex._enum("Reply") == "reply" and ex._enum(None) == "unclassified"
    assert ex._enum("x@y.com") == "other" and ex._enum("a b c") == "other"
