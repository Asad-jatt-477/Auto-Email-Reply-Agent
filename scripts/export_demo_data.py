#!/usr/bin/env python3
"""
scripts/export_demo_data.py - export PRIVACY-SAFE data for the public demo page.

Writes two files into web/data/ (served as static files by the demo site):

  metrics.json  latest full-pipeline evaluation run (evaluation/results/*/metrics.json)
                -> accuracy, F1, confusion matrix, per-class results, latency ...
  stats.json    aggregated activity counts from the agent's SQLite DB
                -> totals per action / source / language / category / day

PRIVACY: only numbers and fixed enum labels are exported. Sender addresses,
subjects, snippets, drafts, reasons and message ids are never read into the
output. Every string in the final JSON is also checked against a strict
allow-list pattern before anything is written; if the check fails nothing is
written and the script exits non-zero.

Usage:
  python scripts/export_demo_data.py                 # export both files
  python scripts/export_demo_data.py --dry-run       # print, write nothing
  python scripts/export_demo_data.py --skip-stats    # evaluation metrics only
  python scripts/export_demo_data.py --db D:/x.db --results-dir evaluation/results

Exit codes: 0 ok | 1 no usable evaluation run / invalid metrics | 2 privacy check failed
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
RUN_DIR_RE = re.compile(r"^(\d{8})-(\d{6})$")
ENUM_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")             # action/source/category labels
SAFE_STR_RE = re.compile(r"^[A-Za-z0-9_.:+\- /()]{0,64}$")    # any string allowed in output
ERROR_KIND_RE = re.compile(r"^\s*-\s*\S+:\s*([A-Za-z]+Error)\b", re.M)
REQUIRED_METRICS = (
    "n_emails", "n_scored", "n_errors", "accuracy", "macro_f1", "per_class", "confusion_matrix",
    "unsafe_auto_reply_rate", "legit_to_spam_rate", "language_accuracy",
    "decided_by_guardrails", "llm_calls", "mean_llm_latency_s", "action_counts", "model",
)

log = logging.getLogger("export_demo_data")


class ExportError(Exception):
    """Expected, user-facing failure (bad input)."""

    exit_code = 1


class PrivacyError(ExportError):
    exit_code = 2


# --------------------------------------------------------------------------- helpers
def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _enum(value, default: str = "unclassified") -> str:
    """Normalise a DB label to a safe lowercase enum; anything odd collapses to 'other'."""
    if value is None or str(value).strip() == "":
        return default
    v = str(value).strip().lower()
    return v if ENUM_RE.match(v) else "other"


def _run_timestamp(run_id: str) -> str:
    m = RUN_DIR_RE.match(run_id)
    d, t = m.groups()
    return datetime.strptime(d + t, "%Y%m%d%H%M%S").isoformat()


def assert_no_pii(obj, path: str = "$") -> None:
    """Fail closed: every string must match a strict allow-list and contain no '@'."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert_no_pii(str(k), f"{path}.<key>")
            assert_no_pii(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_no_pii(v, f"{path}[{i}]")
    elif isinstance(obj, str):
        if "@" in obj or not SAFE_STR_RE.match(obj):
            raise PrivacyError(f"Unsafe string at {path}; refusing to write output.")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=True)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------- metrics.json
def find_latest_eval(results_dir: Path) -> tuple[str, Path, dict]:
    """Newest run folder holding a full-pipeline metrics.json (guardrail-only runs are skipped)."""
    if not results_dir.is_dir():
        raise ExportError(f"Results folder not found: {results_dir}")
    for run in sorted((d for d in results_dir.iterdir() if d.is_dir() and RUN_DIR_RE.match(d.name)),
                      key=lambda d: d.name, reverse=True):
        mfile = run / "metrics.json"
        if not mfile.is_file():
            continue
        try:
            data = json.loads(mfile.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipping %s: unreadable metrics.json (%s)", run.name, exc)
            continue
        if all(k in data for k in REQUIRED_METRICS):
            return run.name, run, data
        log.info("Skipping %s: not a full-pipeline run", run.name)
    raise ExportError(f"No full-pipeline evaluation run found in {results_dir}. Run evaluation/run_eval.py first.")


def _num(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExportError(f"metrics.json: '{name}' must be a number, got {value!r}")
    return value


def build_metrics(run_id: str, run_dir: Path, m: dict) -> dict:
    labels = list(m["per_class"].keys())
    cm = m["confusion_matrix"]
    try:
        matrix = [[int(_num(cm[r][c], f"confusion_matrix.{r}.{c}")) for c in labels] for r in labels]
    except KeyError as exc:
        raise ExportError(f"metrics.json: confusion_matrix is missing label {exc}") from exc

    per_class = []
    for lab in labels:
        pc = m["per_class"][lab]
        per_class.append({
            "label": _enum(lab),
            "precision": _num(pc["precision"], f"{lab}.precision"),
            "recall": _num(pc["recall"], f"{lab}.recall"),
            "f1": _num(pc["f1"], f"{lab}.f1"),
            "support": int(_num(pc["support"], f"{lab}.support")),
        })

    error_kinds: dict[str, int] = {}
    report = run_dir / "report.md"
    if report.is_file():
        found = ERROR_KIND_RE.findall(report.read_text(encoding="utf-8", errors="replace"))
        error_kinds = dict(Counter(found))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "evaluated_at": _run_timestamp(run_id),
        "run_id": run_id,
        "model": str(m["model"]),
        "dataset": {
            "n_emails": int(_num(m["n_emails"], "n_emails")),
            "n_scored": int(_num(m["n_scored"], "n_scored")),
            "n_errors": int(_num(m["n_errors"], "n_errors")),
            "error_kinds": error_kinds,
        },
        "summary": {
            "accuracy": _num(m["accuracy"], "accuracy"),
            "macro_f1": _num(m["macro_f1"], "macro_f1"),
            "language_accuracy": _num(m["language_accuracy"], "language_accuracy"),
            "unsafe_auto_reply_rate": _num(m["unsafe_auto_reply_rate"], "unsafe_auto_reply_rate"),
            "legit_to_spam_rate": _num(m["legit_to_spam_rate"], "legit_to_spam_rate"),
        },
        "pipeline": {
            "decided_by_guardrails": int(_num(m["decided_by_guardrails"], "decided_by_guardrails")),
            "llm_calls": int(_num(m["llm_calls"], "llm_calls")),
            "mean_llm_latency_s": _num(m["mean_llm_latency_s"], "mean_llm_latency_s"),
        },
        "labels": [_enum(x) for x in labels],
        "confusion_matrix": matrix,          # rows = expected, columns = predicted
        "per_class": per_class,
        "action_counts": {_enum(k): int(_num(v, f"action_counts.{k}")) for k, v in m["action_counts"].items()},
    }


# --------------------------------------------------------------------------- stats.json
def _group(conn: sqlite3.Connection, column: str, default: str = "unclassified") -> dict[str, int]:
    out: Counter = Counter()
    # column is a hard-coded literal from this module, never user input
    for value, n in conn.execute(f"SELECT {column}, COUNT(*) FROM processed_emails GROUP BY 1"):
        out[_enum(value, default)] += n
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def build_stats(db_path: Path) -> dict:
    if not db_path.is_file():
        raise ExportError(f"Database not found: {db_path}")
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)   # read-only
    try:
        total = conn.execute("SELECT COUNT(*) FROM processed_emails").fetchone()[0]
        first, last = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM processed_emails").fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) FROM processed_emails WHERE review_status = 'pending'").fetchone()[0]
        by_day = {d: n for d, n in conn.execute(
            "SELECT substr(timestamp, 1, 10), COUNT(*) FROM processed_emails GROUP BY 1 ORDER BY 1")}
        stats = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": _now_iso(),
            "window": {"first_date": (first or "")[:10] or None, "last_date": (last or "")[:10] or None},
            "total_processed": total,
            "pending_review": pending,
            "by_action": _group(conn, "action"),
            "by_source": _group(conn, "source"),
            "by_language": _group(conn, "language"),
            "by_category": _group(conn, "category"),
            "by_day": by_day,
        }
    except sqlite3.Error as exc:
        raise ExportError(f"Could not read database: {exc}") from exc
    finally:
        conn.close()
    return stats


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Export anonymised demo data for web/data/*.json")
    p.add_argument("--db", type=Path, default=ROOT / os.getenv("AGENT_DATA_DIR", "data") / "agent_state.db")
    p.add_argument("--results-dir", type=Path, default=ROOT / "evaluation" / "results")
    p.add_argument("--out-dir", type=Path, default=ROOT / "web" / "data")
    p.add_argument("--skip-stats", action="store_true", help="export evaluation metrics only")
    p.add_argument("--dry-run", action="store_true", help="print the JSON, write nothing")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    try:
        run_id, run_dir, raw = find_latest_eval(args.results_dir)
        outputs = {"metrics.json": build_metrics(run_id, run_dir, raw)}
        if not args.skip_stats:
            try:
                outputs["stats.json"] = build_stats(args.db)
            except ExportError as exc:           # stats are optional: keep any previous stats.json
                log.warning("stats.json skipped: %s", exc)
        for name, data in outputs.items():       # fail closed BEFORE anything is written
            assert_no_pii(data, name)
    except ExportError as exc:
        log.error("%s", exc)
        return exc.exit_code

    for name, data in outputs.items():
        if args.dry_run:
            print(f"--- {name} ---\n{json.dumps(data, indent=2)}")
        else:
            atomic_write_json(args.out_dir / name, data)
            log.info("Wrote %s", args.out_dir / name)
    log.info("Done (evaluation run %s).", run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
