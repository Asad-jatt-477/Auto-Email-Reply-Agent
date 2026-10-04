"""
evaluation/run_eval.py
----------------------
Measures the FULL decision policy (guardrails -> LLM -> confidence /
output checks) on a labelled dataset, exactly as production decides,
but without touching Gmail.

  python -m evaluation.run_eval                    # real Groq model (needs GROQ_API_KEY)
  python -m evaluation.run_eval --guardrails-only  # deterministic layer only, no API calls
  python -m evaluation.run_eval --limit 10 --sleep 2

Outputs (evaluation/results/<timestamp>/):
  predictions.jsonl  one row per email (decision, latency, error)
  metrics.json       all metrics
  report.md          human-readable summary

Why these metrics (business view, not just accuracy):
  unsafe_auto_reply_rate  emails that needed a human but were auto-answered  -> must be ~0
  legit_to_spam_rate      real mail moved to Spam (lost mail)                -> must be ~0
  automation_rate         answerable emails actually auto-answered          -> value delivered
Production actions are grouped as reply / human (review+escalate) / skip / spam.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src import config  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DATASET = os.path.join(HERE, "dataset.jsonl")
DEFAULT_CONTEXT = os.path.join(HERE, "business_context_eval.md")
EVAL_AGENT_EMAIL = "agent@northwind-supplies.example"
GROUPS = ["reply", "human", "skip", "spam"]


def group(action: str) -> str:
    return "human" if action in ("escalate", "review") else action


def load_dataset(path=DEFAULT_DATASET) -> list:
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def to_email(row: dict) -> dict:
    from src.step2_fetcher import sender_address

    headers = {k.lower(): v for k, v in (row.get("headers") or {}).items()}
    return {
        "id": row["id"], "thread_id": f"eval-{row['id']}", "from": row["from"],
        "from_address": sender_address(row["from"]), "subject": row.get("subject", ""),
        "body": row["body"], "label_ids": row.get("label_ids", ["INBOX", "UNREAD"]),
        "auto_submitted": headers.get("auto-submitted", ""), "precedence": headers.get("precedence", ""),
        "list_id": headers.get("list-id", ""), "list_unsubscribe": headers.get("list-unsubscribe", ""),
        "reply_to": headers.get("reply-to", ""),
    }


def isolate_environment():
    """Eval must never read or write the production DB, nor use its blacklist/VIP lists."""
    tmp = tempfile.mkdtemp(prefix="email_agent_eval_")
    config.DATA_DIR = tmp
    config.DB_PATH = os.path.join(tmp, "eval.db")
    config.AGENT_EMAIL = EVAL_AGENT_EMAIL
    config.SENDER_BLACKLIST = []
    config.VIP_SENDERS = []


def predict(rows, agent_fn, business_context, sleep=0.0, guardrails_only=False) -> list:
    from src.orchestrator import decide
    from src.step5_guardrails import check_guardrails

    preds = []
    for i, row in enumerate(rows, 1):
        email = to_email(row)
        rec = {"id": row["id"], "expected_action": row["expected_action"],
               "expected_language": row.get("expected_language")}
        start = time.perf_counter()
        try:
            if guardrails_only:
                outcome, reason = check_guardrails(email)
                rec.update(guardrail_outcome=outcome, reason=reason)
            else:
                d = decide(email, agent_fn=agent_fn, business_context=business_context)
                rec.update({k: d.get(k) for k in ("action", "source", "reason", "category",
                                                  "language", "confidence", "draft_text")})
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["latency_s"] = round(time.perf_counter() - start, 3)
        preds.append(rec)
        print(f"[{i}/{len(rows)}] {row['id']}: expected={row['expected_action']} "
              f"got={rec.get('action', rec.get('guardrail_outcome', 'ERROR'))}")
        if sleep and not guardrails_only and rec.get("source") != "guardrails":
            time.sleep(sleep)
    return preds


def _prf(conf, cls):
    tp = conf[cls][cls]
    fp = sum(conf[o][cls] for o in GROUPS if o != cls)
    fn = sum(conf[cls][o] for o in GROUPS if o != cls)
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f1, 3), "support": tp + fn}


def compute_metrics(preds) -> dict:
    scored = [p for p in preds if "action" in p]
    errors = [p for p in preds if "error" in p]
    conf = {e: {g: 0 for g in GROUPS} for e in GROUPS}
    for p in scored:
        conf[group(p["expected_action"])][group(p["action"])] += 1
    n = len(scored)
    correct = sum(conf[g][g] for g in GROUPS)
    per_class = {g: _prf(conf, g) for g in GROUPS}

    exp_human = [p for p in scored if group(p["expected_action"]) == "human"]
    exp_reply = [p for p in scored if p["expected_action"] == "reply"]
    legit = [p for p in scored if p["expected_action"] != "spam"]
    lang = [p for p in scored if p.get("expected_language") and p.get("source") != "guardrails"]
    llm = [p for p in scored if p.get("source") not in ("guardrails", None)]

    def rate(items, cond):
        return round(sum(1 for p in items if cond(p)) / len(items), 3) if items else None

    return {
        "n_emails": len(preds), "n_scored": n, "n_errors": len(errors),
        "accuracy": round(correct / n, 3) if n else None,
        "macro_f1": round(sum(v["f1"] for v in per_class.values()) / len(GROUPS), 3),
        "per_class": per_class,
        "confusion_matrix": conf,
        "unsafe_auto_reply_rate": rate(exp_human, lambda p: p["action"] == "reply"),
        "automation_rate": rate(exp_reply, lambda p: p["action"] == "reply"),
        "legit_to_spam_rate": rate(legit, lambda p: p["action"] == "spam"),
        "language_accuracy": rate(lang, lambda p: p.get("language") == p["expected_language"]),
        "decided_by_guardrails": sum(1 for p in scored if p.get("source") == "guardrails"),
        "llm_calls": len(llm),
        "mean_llm_latency_s": round(sum(p["latency_s"] for p in llm) / len(llm), 2) if llm else None,
        "action_counts": dict(Counter(p["action"] for p in scored)),
    }


def guardrail_metrics(preds, rows) -> dict:
    expected = {r["id"]: r["guardrail_expected"] for r in rows}
    wrong = [{"id": p["id"], "expected": expected[p["id"]], "got": p.get("guardrail_outcome")}
             for p in preds if p.get("guardrail_outcome") != expected[p["id"]]]
    return {"n_emails": len(preds), "guardrail_accuracy": round(1 - len(wrong) / len(preds), 3) if preds else None,
            "mismatches": wrong}


def write_report(out_dir, metrics, preds, model, guardrails_only):
    lines = [f"# Evaluation report", "", f"- Date: {datetime.now().isoformat(timespec='seconds')}",
             f"- Mode: {'guardrails only' if guardrails_only else 'full pipeline'}", f"- Model: {model}", ""]
    if guardrails_only:
        lines += [f"Guardrail accuracy: **{metrics['guardrail_accuracy']}** on {metrics['n_emails']} emails", ""]
        for m in metrics["mismatches"]:
            lines.append(f"- {m['id']}: expected {m['expected']}, got {m['got']}")
    else:
        keys = ["n_emails", "n_errors", "accuracy", "macro_f1", "unsafe_auto_reply_rate", "automation_rate",
                "legit_to_spam_rate", "language_accuracy", "decided_by_guardrails", "llm_calls", "mean_llm_latency_s"]
        lines += ["| Metric | Value |", "|---|---|"] + [f"| {k} | {metrics[k]} |" for k in keys]
        lines += ["", "Confusion matrix (rows = expected, columns = predicted)", "",
                  "| expected \\ predicted | " + " | ".join(GROUPS) + " |", "|---" * (len(GROUPS) + 1) + "|"]
        for e in GROUPS:
            lines.append(f"| {e} | " + " | ".join(str(metrics["confusion_matrix"][e][g]) for g in GROUPS) + " |")
        wrong = [p for p in preds if "action" in p and group(p["action"]) != group(p["expected_action"])]
        lines += ["", f"Misclassified ({len(wrong)})", ""]
        lines += [f"- {p['id']}: expected {p['expected_action']}, got {p['action']} ({p.get('reason')})" for p in wrong]
        errs = [p for p in preds if "error" in p]
        if errs:
            lines += ["", "Errors", ""] + [f"- {p['id']}: {p['error']}" for p in errs]
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None, agent_fn=None, out_root=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=DEFAULT_DATASET)
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sleep", type=float, default=1.0, help="pause between LLM calls (free-tier rate limits)")
    ap.add_argument("--guardrails-only", action="store_true")
    args = ap.parse_args(argv)

    isolate_environment()
    rows = load_dataset(args.dataset)[: args.limit]
    with open(args.context, encoding="utf-8") as fh:
        context = fh.read()
    if agent_fn is None and not args.guardrails_only:
        from src.step4_agent import run_agent as agent_fn

    preds = predict(rows, agent_fn, context, args.sleep, args.guardrails_only)
    metrics = guardrail_metrics(preds, rows) if args.guardrails_only else compute_metrics(preds)
    model = "n/a" if args.guardrails_only else config.GROQ_MODEL
    metrics["model"] = model

    out_dir = os.path.join(out_root or os.path.join(HERE, "results"), datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "predictions.jsonl"), "w", encoding="utf-8") as fh:
        for p in preds:
            fh.write(json.dumps(p, ensure_ascii=False) + "\n")
    with open(os.path.join(out_dir, "metrics.json"), "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, indent=2)
    write_report(out_dir, metrics, preds, model, args.guardrails_only)
    print(f"\nResults written to {out_dir}")
    print(json.dumps({k: v for k, v in metrics.items() if k not in ("per_class", "confusion_matrix", "mismatches")},
                     indent=2))
    return metrics, out_dir


if __name__ == "__main__":
    main()