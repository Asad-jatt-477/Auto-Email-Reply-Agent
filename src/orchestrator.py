"""
orchestrator.py
---------------
The pipeline used by every entry point (polling loop, webhook, demo).

decide()        -> pure decision: guardrails -> LLM -> post-LLM policy.
                   No Gmail or DB writes, so it can be unit-tested and
                   evaluated offline on a labelled dataset.
process_email() -> applies the decision to Gmail + the state DB.

Final actions:
  reply     agent sends the reply itself
  review    agent drafted a reply but must not send it (low confidence or
            draft failed an output check): Gmail draft + 'Needs-Review'
  escalate  human must handle it, no draft: 'Needs-Human'
  skip      no reply needed
  spam      moved to Gmail Spam

Reliability rules (each fixes a real failure mode):
* Escalated / review mail stays UNREAD so the human notices it.
* Every handled message gets the 'AI-Processed' label and the fetch query
  excludes it, so unread escalations are not fetched again, and a lost DB
  cannot cause a second reply.
* A reply is recorded as 'sending' BEFORE the send call and as 'reply'
  immediately AFTER it, before any labelling. A failing label call can
  therefore never trigger a re-send. If the send itself fails (or the
  process died mid-send) the message goes to a human - never auto-retried,
  because a timed-out send may still have been delivered.
* Labels are best-effort and self-healing: a processed message that shows
  up again (label call failed) only gets its labels re-applied.
* A message whose processing keeps failing goes to a human after
  MAX_PROCESSING_ATTEMPTS instead of being retried forever.
"""

import re
import threading

from src import config
from src.step3_state import get_processed, mark_processed, record_failure
from src.step5_guardrails import check_guardrails, urgency_score
from src.step6_sender import create_reply_draft, modify_labels, move_to_spam, send_reply, triage_label_names
from src.step7_logger import setup_logger

logger = setup_logger()

_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _decision(action, source, reason=None, draft_text=None, **meta):
    base = {"action": action, "source": source, "reason": reason, "draft_text": draft_text,
            "category": None, "priority": None, "sentiment": None, "language": None, "confidence": None}
    base.update({k: v for k, v in meta.items() if k in base})
    return base


def check_draft(draft: str, business_context: str) -> str | None:
    """Deterministic output checks on an LLM reply. Returns a problem or None."""
    if not draft or len(draft.strip()) < config.MIN_BODY_LENGTH:
        return "empty or too-short draft"
    if len(draft) > config.MAX_REPLY_CHARS:
        return f"draft longer than {config.MAX_REPLY_CHARS} characters"
    allowed = (business_context or "").lower()
    for match in _URL_RE.finditer(draft):
        url = match.group(0).rstrip(".,)")
        if url.lower() not in allowed:
            return f"draft contains a link not in the business context ({url})"
    for addr in _EMAIL_RE.findall(draft):
        if addr.lower() not in allowed:
            return f"draft contains an email address not in the business context ({addr})"
    return None


def decide(email: dict, agent_fn=None, business_context: str = None) -> dict:
    """Full decision policy without side effects."""
    outcome, reason = check_guardrails(email)
    if outcome != "proceed":
        return _decision(outcome, "guardrails", reason)

    if agent_fn is None:
        from src.step4_agent import load_business_context, run_agent

        agent_fn = run_agent
        if business_context is None:
            business_context = load_business_context()
    agent = agent_fn(email, business_context=business_context)
    meta = {k: agent.get(k) for k in ("category", "priority", "sentiment", "language", "confidence")}
    action = agent["action"]

    if action == "skip":
        if agent.get("category") == "spam":
            return _decision("spam", "agent", agent.get("reason"), **meta)
        return _decision("skip", "agent", agent.get("reason"), **meta)

    if action == "escalate":
        return _decision("escalate", "agent", agent.get("reason"), **meta)

    draft = agent.get("draft_text") or ""
    problem = check_draft(draft, business_context)
    if problem:
        return _decision("review", "policy", f"Output check failed: {problem}", draft, **meta)
    confidence = agent.get("confidence")
    if confidence is None or confidence < config.CONFIDENCE_THRESHOLD:
        return _decision("review", "policy",
                         f"Low confidence ({(confidence or 0):.2f} < {config.CONFIDENCE_THRESHOLD})",
                         draft, **meta)
    return _decision("reply", "agent", None, draft, **meta)


# -- Gmail state per action (idempotent, safe to re-apply) -----------------
def _gmail_state(action: str, decision: dict):
    """(essential labels, labels to remove, triage labels) for an action."""
    triage = triage_label_names(decision.get("category"), decision.get("priority"))
    add = [config.AI_PROCESSED_LABEL]
    remove = []
    if action == "reply":
        add.append(config.AI_REPLIED_LABEL)
        remove = ["UNREAD"]
    elif action == "review":
        add.append(config.LOW_CONFIDENCE_LABEL)
    elif action == "escalate":
        add.append(config.NEEDS_HUMAN_LABEL)
    elif action == "skip" and config.MARK_SKIPPED_AS_READ:
        remove = ["UNREAD"]
    if action not in ("reply", "review", "escalate"):
        triage = []
    return add, remove, triage


def _apply_gmail_state(service, gmail_id, action, decision):
    add, remove, triage = _gmail_state(action, decision)
    try:
        # Essential state first and on its own: a problem with an optional
        # category/priority label must never leave a handled email without
        # 'AI-Processed' (that would make it come back every cycle).
        modify_labels(service, gmail_id, add_names=add, remove_ids=remove)
    except Exception as exc:
        # Not fatal: the DB already holds the decision; the message will
        # re-appear and _heal() re-applies these labels next cycle.
        logger.warning(f"Label update failed (will self-heal) | id={gmail_id} | action={action} | error={exc}")
        return
    if triage:
        try:
            modify_labels(service, gmail_id, add_names=triage)
        except Exception as exc:
            logger.warning(f"Optional triage labels skipped | id={gmail_id} | labels={triage} | error={exc}")


def _record(email, action, decision, **extra):
    meta = {k: decision.get(k) for k in ("category", "priority", "sentiment", "language", "confidence",
                                         "reason", "draft_text", "source")}
    if action in ("review", "escalate"):
        meta["review_status"] = "pending"
    meta.update(sender=email.get("from_address") or email.get("from"),
                subject=email.get("subject"), snippet=(email.get("body") or "")[:300])
    meta.update(extra)
    mark_processed(email["id"], email.get("thread_id", ""), action, **meta)


def _heal(service, email, row):
    """Message is already in the DB but came back from Gmail."""
    if row["action"] == "sending":
        # Process stopped between 'sending' and 'reply': delivery unknown.
        decision = _decision("escalate", "recovery",
                             "Interrupted while sending a reply - check the thread before answering.")
        _record(email, "escalate", decision)
        _apply_gmail_state(service, email["id"], "escalate", decision)
        logger.warning(f"RECOVERED interrupted send -> ESCALATED | id={email['id']}")
        return
    _apply_gmail_state(service, email["id"], row["action"], row)


def _escalate_after_failures(service, email, error):
    attempts = record_failure(email["id"], str(error))
    if attempts < config.MAX_PROCESSING_ATTEMPTS:
        logger.error(f"Processing failed (attempt {attempts}/{config.MAX_PROCESSING_ATTEMPTS}, "
                     f"will retry) | id={email['id']} | error={error}")
        return None
    decision = _decision("escalate", "failure", f"Automatic processing failed {attempts} times: {error}")
    _record(email, "escalate", decision)
    _apply_gmail_state(service, email["id"], "escalate", decision)
    logger.error(f"ESCALATED after {attempts} failed attempts | id={email['id']}")
    return decision


def process_email(service, email: dict, agent_fn=None, dry_run: bool = False):
    gmail_id = email["id"]
    subject = email.get("subject", "")

    if not dry_run:
        row = get_processed(gmail_id)
        if row:
            _heal(service, email, row)
            return None

    try:
        decision = decide(email, agent_fn=agent_fn)
    except Exception as exc:
        if dry_run:
            logger.error(f"DRY-RUN decision error | id={gmail_id} | error={exc}")
            return None
        return _escalate_after_failures(service, email, exc)

    action = decision["action"]
    meta = (f"source={decision['source']} | category={decision['category']} | "
            f"confidence={decision['confidence']} | reason={decision['reason']}")
    if dry_run:
        logger.info(f"DRY-RUN would {action.upper()} | id={gmail_id} | subject='{subject}' | {meta}")
        return decision

    if action == "reply":
        mark_processed(gmail_id, email.get("thread_id", ""), "sending")
        try:
            send_reply(service, email, decision["draft_text"])
        except Exception as exc:
            decision = _decision("escalate", "failure",
                                 f"Reply send failed (not retried to avoid a duplicate): {exc}",
                                 decision["draft_text"],
                                 **{k: decision[k] for k in ("category", "priority", "sentiment",
                                                             "language", "confidence")})
            _record(email, "escalate", decision)
            _apply_gmail_state(service, gmail_id, "escalate", decision)
            logger.error(f"SEND FAILED -> ESCALATED | id={gmail_id} | error={exc}")
            return decision
        _record(email, "reply", decision)
        _apply_gmail_state(service, gmail_id, "reply", decision)

    elif action == "review":
        draft_id = None
        try:
            draft_id = create_reply_draft(service, email, decision["draft_text"]).get("id")
        except Exception as exc:
            logger.warning(f"Gmail draft creation failed (draft kept in DB) | id={gmail_id} | error={exc}")
        _record(email, "review", decision, gmail_draft_id=draft_id)
        _apply_gmail_state(service, gmail_id, "review", decision)

    elif action == "spam":
        _record(email, "spam", decision)
        try:
            move_to_spam(service, email)
            _apply_gmail_state(service, gmail_id, "spam", decision)
        except Exception as exc:
            logger.warning(f"Move to Spam failed | id={gmail_id} | error={exc}")

    else:  # escalate / skip
        _record(email, action, decision)
        _apply_gmail_state(service, gmail_id, action, decision)

    logger.info(f"{action.upper()} | id={gmail_id} | subject='{subject}' | {meta}")
    return decision


def run_cycle(service, agent_fn=None, dry_run: bool = False, stop_event: threading.Event = None) -> dict:
    from src.step2_fetcher import fetch_message, list_unprocessed_ids

    summary = {"fetched": 0, "reply": 0, "review": 0, "escalate": 0, "skip": 0, "spam": 0, "errors": 0}
    try:
        ids = list_unprocessed_ids(service)
    except Exception as exc:
        logger.error(f"Fetch error (cycle skipped): {exc}")
        summary["errors"] += 1
        return summary

    emails = []
    for mid in ids:
        try:
            emails.append(fetch_message(service, mid))
        except Exception as exc:   # one unreadable message never blocks the others
            logger.error(f"Could not fetch message | id={mid} | error={exc}")
            summary["errors"] += 1

    emails.sort(key=lambda e: e.get("internal_date", 0))           # oldest first...
    emails.sort(key=urgency_score, reverse=True)                     # ...urgent first (stable)
    summary["fetched"] = len(emails)
    if emails:
        logger.info(f"Cycle start | {len(emails)} message(s)")

    for email in emails:
        if stop_event is not None and stop_event.is_set():
            break
        try:
            decision = process_email(service, email, agent_fn=agent_fn, dry_run=dry_run)
        except Exception as exc:  # last-resort guard: one message never stops the cycle
            logger.error(f"Unexpected error | id={email.get('id')} | error={exc}")
            summary["errors"] += 1
            continue
        if decision:
            summary[decision["action"]] = summary.get(decision["action"], 0) + 1
    return summary