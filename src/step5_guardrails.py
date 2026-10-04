"""
step5_guardrails.py
-------------------
Deterministic (zero-token) checks that run BEFORE the LLM.

check_guardrails(email) -> (outcome, reason)
  "proceed"  -> send to the LLM agent
  "spam"     -> confirmed spam: move to Gmail Spam
  "skip"     -> needs no reply (not spam): leave it alone
  "escalate" -> a human must handle it, LLM is not called

Order (first match wins):
 1. already in Gmail Spam (defence-in-depth)        -> skip (nothing to do)
 2. promotions / social / forums tab                -> skip (unless sender whitelisted by feedback)
 3. automated / bulk mail (RFC 3834, no-reply, self) -> skip (auto-reply loop prevention)
 4. manual blacklist                                -> spam
 5. learned spam feedback                           -> spam
 6. per-thread rate limit                           -> skip
 7. empty / too short body                          -> skip
 8. prompt-injection attempt                        -> escalate
 9. legal threat / refund dispute / angry VIP       -> escalate

Keyword rules use whole-word / whole-phrase matching, so "court" does
not fire on "courtesy" and "abhi" would not fire on "abhinav".
"""

import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from src import config
from src.step2_fetcher import sender_address
from src.step3_state import count_replies_since, get_sender_feedback_verdict


@lru_cache(maxsize=512)
def _phrase_regex(phrase: str):
    words = [re.escape(w) for w in phrase.lower().split()]
    return re.compile(r"(?<!\w)" + r"\s+".join(words) + r"(?!\w)", re.IGNORECASE)


def find_phrase(text: str, phrases) -> str | None:
    """First phrase that occurs as whole words in text, else None."""
    text = text or ""
    for phrase in phrases:
        if _phrase_regex(phrase).search(text):
            return phrase
    return None


def sender_matches(address: str, patterns) -> str | None:
    """Pattern '@domain.com' matches the domain (and subdomains); otherwise exact address."""
    address = (address or "").lower()
    domain = address.rpartition("@")[2]
    for pattern in patterns:
        p = pattern.strip().lower()
        if not p:
            continue
        if p.startswith("@"):
            d = p[1:]
            if domain == d or domain.endswith("." + d):
                return pattern
        elif address == p:
            return pattern
    return None


def _address(email: dict) -> str:
    return email.get("from_address") or sender_address(email.get("from", ""))


def _full_text(email: dict) -> str:
    return f"{email.get('subject') or ''}\n{email.get('body') or ''}"


def _is_automated(email: dict) -> str | None:
    address = _address(email)
    local = address.partition("@")[0]
    if local in config.AUTO_SKIP_LOCAL_PARTS:
        return f"automated sender address '{address}'"
    if config.AGENT_EMAIL and address == config.AGENT_EMAIL:
        return "email from the agent's own address (loop prevention)"
    auto = (email.get("auto_submitted") or "").strip().lower()
    if auto and auto != "no":
        return f"Auto-Submitted: {auto} (RFC 3834: never auto-reply to automated mail)"
    if (email.get("precedence") or "").strip().lower() in ("bulk", "list", "junk", "auto_reply"):
        return f"Precedence: {email.get('precedence')}"
    if email.get("list_id") or email.get("list_unsubscribe"):
        return "mailing-list / bulk mail (List-Id or List-Unsubscribe header)"
    return None


def _rate_limited(email: dict) -> str | None:
    thread_id = email.get("thread_id")
    if not thread_id:
        return None
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    count = count_replies_since(thread_id, since)
    if count >= config.MAX_REPLIES_PER_THREAD_PER_HOUR:
        return (f"thread already has {count} reply(ies) in the last hour "
                f"(limit {config.MAX_REPLIES_PER_THREAD_PER_HOUR})")
    return None


def _has_angry_tone(text: str) -> bool:
    if find_phrase(text, config.ANGRY_TONE_PHRASES):
        return True
    if "!!!" in text:
        return True
    # Two or more shouted words; a single acronym (ASAP, HTML) is not anger.
    shouted = [w for w in re.findall(r"[A-Za-z]{4,}", text) if w.isupper()]
    return len(shouted) >= 2


def check_guardrails(email: dict):
    labels = email.get("label_ids", []) or []
    address = _address(email)
    verdict = get_sender_feedback_verdict(address)

    for label in config.NATIVE_SPAM_LABELS:
        if label in labels:
            return "skip", "already in Gmail Spam - nothing to do"

    if verdict != "not_spam":
        for label in config.NON_SPAM_SKIP_LABELS:
            if label in labels:
                return "skip", f"Gmail tab '{label}' (not spam, no reply needed)"

    automated = _is_automated(email)
    if automated:
        return "skip", f"Sender filter: {automated}"

    hit = sender_matches(address, config.SENDER_BLACKLIST)
    if hit:
        return "spam", f"Sender blacklist match ('{hit}')"

    if verdict == "spam":
        return "spam", "Learned spam filter: sender repeatedly marked as spam via feedback"

    limited = _rate_limited(email)
    if limited:
        return "skip", f"Rate limit: {limited}"

    body = (email.get("body") or "").strip()
    if len(body) < config.MIN_BODY_LENGTH:
        return "skip", f"Content sanity: body too short ({len(body)} chars, min {config.MIN_BODY_LENGTH})"

    text = _full_text(email)
    hit = find_phrase(text, config.PROMPT_INJECTION_PATTERNS)
    if hit:
        return "escalate", f"Forced escalation: possible prompt-injection text ('{hit}')"

    hit = find_phrase(text, config.LEGAL_THREAT_KEYWORDS)
    if hit:
        return "escalate", f"Forced escalation: legal-threat phrase ('{hit}')"

    hit = find_phrase(text, config.REFUND_DISPUTE_KEYWORDS)
    if hit:
        return "escalate", f"Forced escalation: refund/payment dispute phrase ('{hit}')"

    if sender_matches(address, config.VIP_SENDERS) and _has_angry_tone(text):
        return "escalate", "Forced escalation: VIP sender with angry tone"

    return "proceed", None


def passes_guardrails(email: dict):
    """Backwards-compatible boolean wrapper."""
    outcome, reason = check_guardrails(email)
    return outcome == "proceed", reason


def urgency_score(email: dict) -> int:
    """Zero-token hint used only to order processing inside a cycle."""
    text = _full_text(email)
    return sum(1 for kw in config.URGENT_KEYWORDS if _phrase_regex(kw).search(text))
