"""
main.py
--------
THE SINGLE ENTRY POINT for the whole project.

    Normal run (polling agent):      python main.py
    Give spam-feedback correction:   python main.py feedback <sender_email> spam
                                      python main.py feedback <sender_email> not_spam
    Stop the agent:                  Ctrl+C

Poore project ka ENTRY POINT hai — sab phases ko jodta hai aur ek
continuous polling loop chalata hai jo:
1. Naye unread emails detect karta hai              (fetcher)
2. Priority ke hisaab se sort karta hai              (urgent pehle)
3. Duplicate check karta hai                         (state)
4. Guardrails run karta hai LLM se pehle              (guardrails:
   spam / skip / forced-escalate / proceed — confirmed spam (Gmail
   native label, blacklist, learned-feedback), legal & refund &
   angry-VIP yahin pakde jate hain, bina LLM call ke)
4.5. Guardrails "spam" outcome par email ko Gmail ke asli Spam
   folder mein physically move kar deta hai                (sender)
5. LLM se decision leta hai (reply/skip/escalate +
   category/priority/sentiment/language/confidence)   (agent)
   -> agar action "skip" ho aur category "spam" ho, ye email bhi
      Spam folder mein physically move ki jati hai        (sender)
6. Confidence threshold check karta hai — kam confidence
   wale replies khud nahi bhejta, human-review flag karta hai
7. Reply bhejta hai (sahi language/tone mein) ya escalate
   label lagata hai, category/priority labels bhi lagata hai (sender)
8. Sab kuch log karta hai                             (logger)
"""

import sys
import time

from src.step1_auth import get_gmail_service
from src.step2_fetcher import fetch_unread_emails
from src.step3_state import init_db, is_processed, mark_processed, record_sender_feedback
from src.step4_agent import run_agent
from src.step5_guardrails import check_guardrails
from src.step6_sender import send_reply, mark_needs_human, apply_triage_labels, move_to_spam
from src.step7_logger import setup_logger
from src.config import POLL_INTERVAL_SECONDS, CONFIDENCE_THRESHOLD, URGENT_KEYWORDS

logger = setup_logger()


# ══════════════════════════════════════════════════════════════════
# Priority pre-sort (Phase 12) — zero-token, regex-free keyword scan.
# Sirf PROCESSING ORDER decide karne ke liye hai (poll cycle ke andar
# urgent emails pehle handle ho), asli/final priority label LLM ki
# classification se aati hai.
# ══════════════════════════════════════════════════════════════════

def _urgency_hint_score(email: dict) -> int:
    """Zyada score = zyada urgent lagti hai (sirf sorting ke liye)."""
    text = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    return sum(1 for kw in URGENT_KEYWORDS if kw in text)


def _sort_by_urgency(emails: list) -> list:
    return sorted(emails, key=_urgency_hint_score, reverse=True)


def mark_done(service, gmail_id, thread_id, status, classification: dict = None):
    """
    Email processing complete hone ke baad do kaam karta hai:
    1. Local database mein "processed" mark karo (duplicate-check ke liye),
       classification metadata (category/priority/sentiment/language/
       confidence) bhi save karo agar mojood ho (Phase 12).
    2. Gmail pe UNREAD label hatao (taake wo dobara fetch na ho aur
       backlog na jamay)
    """
    classification = classification or {}
    mark_processed(
        gmail_id,
        thread_id,
        status,
        category=classification.get("category"),
        priority=classification.get("priority"),
        sentiment=classification.get("sentiment"),
        language=classification.get("language"),
        confidence=classification.get("confidence"),
    )
    try:
        service.users().messages().modify(
            userId="me", id=gmail_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception as e:
        logger.error(f"Failed to mark as read | id={gmail_id} | error={e}")


def process_email(service, email: dict):
    """
    Ek single email ko poore pipeline se guzarta hai:
    duplicate-check -> guardrails (skip/escalate/proceed) -> agent
    decision -> confidence check -> action execute -> triage labels.

    Har external call (guardrails DB read, LLM call, Gmail send) apni
    jagah try/except mein wrapped hai taake EK email ka error poori
    cycle ko crash na kare — agli email process hoti rahe.
    """
    gmail_id = email["id"]
    thread_id = email["thread_id"]
    subject = email.get("subject", "")

    # Duplicate-guard -- agar pehle hi process ho chuki hai to skip
    if is_processed(gmail_id):
        return

    # Guardrails -- LLM se PEHLE chalte hain (deterministic, free)
    try:
        outcome, reason = check_guardrails(email)
    except Exception as e:
        logger.error(f"Guardrails check error | id={gmail_id} | error={e}")
        return  # is baar skip, agli cycle mein retry hoga

    if outcome == "spam":
        # Confirmed spam (Gmail native label / blacklist / learned
        # feedback) -> email ko physically Gmail ke Spam folder mein
        # move karo, LLM call bilkul skip (token save + safest).
        try:
            move_to_spam(service, email)
            logger.info(f"SPAM (guardrails) | id={gmail_id} | subject='{subject}' | reason={reason}")
            mark_done(service, gmail_id, thread_id, "spam")
        except Exception as e:
            logger.error(f"Spam-move action error | id={gmail_id} | error={e}")
            # mark_done jaan-boojh kar nahi bulaya -- agli cycle mein retry hoga
        return

    if outcome == "skip":
        logger.info(f"SKIP (guardrails) | id={gmail_id} | subject='{subject}' | reason={reason}")
        mark_done(service, gmail_id, thread_id, "skip")
        return

    if outcome == "escalate":
        # Forced escalation (legal / refund dispute / angry VIP) --
        # LLM call yahan jaan-boojh kar SKIP kiya jata hai (token save).
        try:
            mark_needs_human(service, email)
            logger.info(f"ESCALATED (forced-rule) | id={gmail_id} | subject='{subject}' | reason={reason}")
            mark_done(service, gmail_id, thread_id, "escalate")
        except Exception as e:
            logger.error(f"Forced-escalation action error | id={gmail_id} | error={e}")
        return

    # outcome == "proceed" -> genuine email, LLM tak jaati hai
    try:
        decision = run_agent(email)
    except Exception as e:
        logger.error(f"Agent (LLM) error | id={gmail_id} | subject='{subject}' | error={e}")
        return  # is baar skip, agli cycle mein retry hoga

    action = decision["action"]
    confidence = decision.get("confidence", 0.5)
    low_confidence = False

    # Confidence scoring (Phase 12): agent "reply" karna chahta hai
    # lekin apne aap par yaqeen kam hai -> khud reply mat bhejo,
    # human-review ke liye escalate/flag kar do.
    if action == "reply" and confidence < CONFIDENCE_THRESHOLD:
        logger.info(
            f"LOW CONFIDENCE override | id={gmail_id} | subject='{subject}' | "
            f"confidence={confidence:.2f} < threshold={CONFIDENCE_THRESHOLD} -> escalate"
        )
        action = "escalate"
        decision["reason"] = f"Low confidence ({confidence:.2f}) on reply draft — flagged for human review."
        low_confidence = True

    log_meta = (
        f"category={decision.get('category')} | priority={decision.get('priority')} | "
        f"sentiment={decision.get('sentiment')} | language={decision.get('language')} | "
        f"confidence={confidence:.2f}"
    )

    # Action execute karo
    try:
        if action == "reply":
            send_reply(service, email, decision["draft_text"])
            logger.info(f"REPLIED | id={gmail_id} | subject='{subject}' | {log_meta}")
            apply_triage_labels(service, email, decision.get("category"), decision.get("priority"))
            mark_done(service, gmail_id, thread_id, "reply", decision)

        elif action == "escalate":
            mark_needs_human(service, email)
            logger.info(f"ESCALATED | id={gmail_id} | subject='{subject}' | reason={decision['reason']} | {log_meta}")
            apply_triage_labels(service, email, decision.get("category"), decision.get("priority"), low_confidence)
            mark_done(service, gmail_id, thread_id, "escalate", decision)

        else:  # skip (agent's own decision, not guardrails)
            if decision.get("category") == "spam":
                # LLM ne khud is email ko spam/scam/phishing pehchana --
                # Gmail ki apni classification is par nahi thi (warna
                # guardrails hi pehle pakad leta), isliye yahan khud
                # Spam folder mein physically move karo.
                move_to_spam(service, email)
                logger.info(f"SPAM (agent) | id={gmail_id} | subject='{subject}' | reason={decision['reason']} | {log_meta}")
                mark_done(service, gmail_id, thread_id, "spam", decision)
            else:
                logger.info(f"SKIP (agent) | id={gmail_id} | subject='{subject}' | reason={decision['reason']} | {log_meta}")
                mark_done(service, gmail_id, thread_id, "skip", decision)

    except Exception as e:
        logger.error(f"Action execution error | id={gmail_id} | action={action} | error={e}")
        # NOTE: yahan mark_done() jaan-boojh kar nahi bulaya -- agar
        # send_reply beech mein fail ho jaye, agli cycle mein retry hoga
        # taake email "silently lost" na ho.


def run_cycle(service):
    """Ek poora polling cycle — saari unread emails fetch, priority-sort aur process karo."""
    try:
        emails = fetch_unread_emails()
    except Exception as e:
        logger.error(f"Fetch error (cycle skipped): {e}")
        return

    emails = _sort_by_urgency(emails)

    logger.info(f"Cycle start | {len(emails)} unread email(s) found (sorted by urgency hint)")

    for email in emails:
        process_email(service, email)

    logger.info("Cycle complete")


def run_polling_loop():
    init_db()
    service = get_gmail_service()
    logger.info(f"Email Reply AI Agent started. Polling every {POLL_INTERVAL_SECONDS} seconds.")

    while True:
        try:
            run_cycle(service)
        except Exception as e:
            # Ye ek extra safety net hai -- kabhi bhi is loop ko crash
            # nahi hona chahiye, warna agent poora ruk jayega.
            logger.error(f"Cycle-level error (loop continues): {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


# ══════════════════════════════════════════════════════════════════
# CLI: spam-learning feedback command (Phase 12)
#   python main.py feedback <sender_email> spam
#   python main.py feedback <sender_email> not_spam
# ══════════════════════════════════════════════════════════════════

def run_feedback_command(argv):
    if len(argv) != 4:
        print("Usage: python main.py feedback <sender_email> <spam|not_spam>")
        sys.exit(1)

    sender = argv[2]
    label = argv[3].strip().lower()

    if label not in ("spam", "not_spam"):
        print("Invalid label. Use 'spam' or 'not_spam'.")
        sys.exit(1)

    init_db()
    spam_votes, not_spam_votes = record_sender_feedback(sender, is_spam=(label == "spam"))
    print(f"Feedback recorded for '{sender}': spam_votes={spam_votes}, not_spam_votes={not_spam_votes}")
    print("The system will use this correction automatically on future emails from this sender.")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "feedback":
        run_feedback_command(sys.argv)
        return

    try:
        run_polling_loop()
    except KeyboardInterrupt:
        logger.info("Agent stopped by user (Ctrl+C).")


if __name__ == "__main__":
    main()
