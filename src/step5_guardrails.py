"""
step5_guardrails.py
--------------------
Phase 7 — Guardrails & Safety Rules.
Phase 12 — Forced Escalation Rules + Spam Learning Feedback Loop.
Phase 13 — Spam ko "skip" se alag karke dedicated "spam" outcome diya
gaya hai, taake main.py in emails ko physically Gmail ke Spam folder
mein move kar sake (pehle sirf UNREAD hata kar chhod diya jata tha,
Spam folder mein daala hi nahi jata tha).

Ye hardcoded (non-LLM) safety checks hain jo LLM call se PEHLE chalte hain
(Phase 9 ke main loop mein). Ye rules deterministic hain, isliye LLM ke
"guess" pe nahi chorte, aur bilkul FREE hain (koi token cost nahi).

check_guardrails() ab CHAAR mumkin outcomes de sakta hai:
- "proceed"  -> email Phase 6 (LLM agent) tak jaati hai
- "spam"     -> email confirmed spam hai -> main.py isay Gmail ke Spam
                folder mein physically move karega (SPAM label + INBOX/
                UNREAD hataega)
- "skip"     -> email ko reply ki zaroorat nahi lekin ye spam NAHI hai
                (promo/social/forums tab, no-reply notification, chhota
                body, rate-limit) -> sirf UNREAD hataya jata hai
- "escalate" -> email seedha human ke paas jati hai, LLM call bhi NAHI
                lagta (legal threat / refund dispute / angry VIP jaisi
                cheezein hamesha insaan hi handle kare — safest + free)

Filter order (STRICTLY isi sequence mein, pehla fail hi final decision hai):
1. Gmail ka apna confirmed-SPAM label                    -> "spam"
2. Gmail inbox-tab categories (Promotions/Social/Forums)  -> "skip"
3. Sender loop-prevention (no-reply/self)                 -> "skip"
4. Manual sender blacklist                                -> "spam"
5. Learned spam-feedback filter (human corrections)       -> "spam"
6. Rate limit (1 thread = max 1 reply/hour)                -> "skip"
7. Content sanity check (empty/too-short body)              -> "skip"
8. Forced-escalation keyword rules (legal/refund/angry VIP) -> "escalate"
"""

from datetime import datetime, timezone, timedelta

from src.config import (
    NATIVE_SPAM_LABELS,
    NON_SPAM_SKIP_LABELS,
    AUTO_SKIP_SENDER_PREFIXES,
    SENDER_BLACKLIST,
    AGENT_EMAIL,
    MAX_REPLIES_PER_THREAD_PER_HOUR,
    MIN_BODY_LENGTH,
    LEGAL_THREAT_KEYWORDS,
    REFUND_DISPUTE_KEYWORDS,
    ANGRY_TONE_PHRASES,
    VIP_SENDERS,
)
from src.step3_state import get_last_reply_time, get_sender_feedback_verdict


def _check_native_spam_label(email: dict):
    """
    Check 1: Gmail ne khud is email ko SPAM confirm kiya hua hai
    (sabse pehla, sabse reliable signal). True matlab ye spam hai.
    """
    label_ids = email.get("label_ids", [])
    for label in NATIVE_SPAM_LABELS:
        if label in label_ids:
            return True, f"Gmail native spam filter: '{label}' label present"
    return False, None


def _check_non_spam_category(email: dict):
    """
    Check 2: Gmail ke inbox TABS (Promotions/Social/Forums) — ye spam
    nahi hain, isliye reply skip hoti hai lekin Spam folder mein move
    NAHI kiya jata.
    """
    label_ids = email.get("label_ids", [])
    for label in NON_SPAM_SKIP_LABELS:
        if label in label_ids:
            return False, f"Gmail category filter: '{label}' label present (not spam, just no-reply-needed)"
    return True, None


def _check_sender(email: dict):
    """Check 3: Loop-prevention (no-reply senders aur khud agent ka apna address)."""
    sender = (email.get("from") or "").lower()

    for prefix in AUTO_SKIP_SENDER_PREFIXES:
        if prefix in sender:
            return False, f"Sender filter: matches auto-skip prefix '{prefix}'"

    if AGENT_EMAIL and AGENT_EMAIL.lower() in sender:
        return False, "Sender filter: email from agent's own address (loop prevention)"

    return True, None


def _check_sender_blacklist(email: dict):
    """
    Check 4: Manual sender blacklist (src/config.py ka SENDER_BLACKLIST).
    Ye senders confirmed spam treat hote hain -> "spam" outcome, taake
    unki emails Spam folder mein physically move ho jayein.
    """
    sender = (email.get("from") or "").lower()
    for blacklisted in SENDER_BLACKLIST:
        if blacklisted.lower() in sender:
            return True, f"Sender filter: sender in blacklist ('{blacklisted}')"
    return False, None


def _check_learned_spam_feedback(email: dict):
    """
    Check 5 (Phase 12) — spam learning feedback loop.
    Agar humans ne pehle "feedback" command se is sender ko baar baar
    spam mark kiya hai, to future emails bhi confirmed-spam treat hongi
    -> Spam folder mein move — bina kisi manual blacklist edit ke,
    system khud seekh gaya.
    (Note: 'not_spam' verdict yahan sirf informational hai — woh
    whitelist ka kaam Gmail category check ko bypass nahi karta, lekin
    is function mein hum usay hamesha 'not spam' hi denge.)
    """
    sender = (email.get("from") or "").lower()
    verdict = get_sender_feedback_verdict(sender)

    if verdict == "spam":
        return True, "Learned spam filter: sender repeatedly marked as spam via feedback"

    return False, None


def _check_rate_limit(email: dict):
    """Check 4: Ek thread pe 1 hour mein max N reply (config se)."""
    thread_id = email.get("thread_id")
    if not thread_id:
        return True, None

    last_reply_str = get_last_reply_time(thread_id)
    if not last_reply_str:
        return True, None

    try:
        last_reply_time = datetime.fromisoformat(last_reply_str)
    except ValueError:
        # timestamp parse na ho paye to safe side pe pass karo
        return True, None

    if last_reply_time.tzinfo is None:
        last_reply_time = last_reply_time.replace(tzinfo=timezone.utc)

    elapsed = datetime.now(timezone.utc) - last_reply_time

    if MAX_REPLIES_PER_THREAD_PER_HOUR <= 1 and elapsed < timedelta(hours=1):
        return False, f"Rate limit: thread already replied {elapsed} ago (<1 hour)"

    return True, None


def _check_content_sanity(email: dict):
    """Check 5: Khali ya bohat chhota body (jaise sirf image/attachment)."""
    body = (email.get("body") or "").strip()
    if len(body) < MIN_BODY_LENGTH:
        return False, f"Content sanity: body too short ({len(body)} chars, min {MIN_BODY_LENGTH})"
    return True, None


def _contains_any(text: str, keywords: list) -> str | None:
    """Case-insensitive substring match — pehla milta keyword return karta hai."""
    text_lower = text.lower()
    for kw in keywords:
        if kw.lower() in text_lower:
            return kw
    return None


def _has_angry_tone(text: str) -> bool:
    """
    Sasta (regex-free) heuristic: known angry phrases, 3+ consecutive
    '!' , ya koi poora-CAPS lafz (4+ letters) milne par angry treat karo.
    """
    if _contains_any(text, ANGRY_TONE_PHRASES):
        return True
    if "!!!" in text:
        return True
    for word in text.split():
        cleaned = "".join(c for c in word if c.isalpha())
        if len(cleaned) >= 4 and cleaned.isupper():
            return True
    return False


def _check_forced_escalation(email: dict):
    """
    Check 6 (Phase 12) — deterministic forced-escalation rules.
    Ye LLM se PEHLE chalta hai taake:
    (a) legal threats / refund disputes hamesha insaan tak pohanchein
        (koi AI galti se "reply" na kar de aisi sensitive email par),
    (b) VIP customer agar angry tone mein likhe to seedha escalate ho
        (extra care), aur
    (c) in cases mein LLM call hi skip ho jaye -> tokens bachte hain.

    Return: (should_escalate: bool, reason: str | None)
    """
    body = email.get("body") or ""
    subject = email.get("subject") or ""
    full_text = f"{subject}\n{body}"
    sender = (email.get("from") or "").lower()

    legal_hit = _contains_any(full_text, LEGAL_THREAT_KEYWORDS)
    if legal_hit:
        return True, f"Forced escalation: legal-threat keyword detected ('{legal_hit}')"

    refund_hit = _contains_any(full_text, REFUND_DISPUTE_KEYWORDS)
    if refund_hit:
        return True, f"Forced escalation: refund/payment dispute keyword detected ('{refund_hit}')"

    is_vip = any(vip.lower() in sender for vip in VIP_SENDERS)
    if is_vip and _has_angry_tone(full_text):
        return True, "Forced escalation: VIP sender with angry tone detected"

    return False, None


def passes_guardrails(email: dict):
    """
    BACKWARDS-COMPATIBLE wrapper (purana API, boolean pass/fail).
    NAYE code ko check_guardrails() use karna chahiye jo 3-way outcome
    deta hai (proceed/skip/escalate). Ye function ab bhi tests aur kisi
    purane caller ke liye maujood hai.
    """
    outcome, reason = check_guardrails(email)
    return outcome == "proceed", reason


def check_guardrails(email: dict):
    """
    Saare deterministic checks STRICT order mein run karta hai.

    Return: (outcome, reason)
      outcome = "proceed"  -> email Phase 6 (LLM agent) tak ja sakti hai
      outcome = "spam"     -> confirmed spam -> Spam folder mein physically move hogi
      outcome = "skip"     -> reply ki zaroorat nahi, lekin spam nahi (Spam folder mein move NAHI hoti)
      outcome = "escalate" -> email seedha human ke paas (LLM call skip)
    """
    # 1. Gmail ka apna confirmed-SPAM label -> "spam"
    is_native_spam, reason = _check_native_spam_label(email)
    if is_native_spam:
        return "spam", reason

    # 2. Gmail inbox tabs (Promotions/Social/Forums) -> "skip" (spam nahi)
    passed, reason = _check_non_spam_category(email)
    if not passed:
        return "skip", reason

    # 3. Loop-prevention senders (no-reply/self) -> "skip"
    passed, reason = _check_sender(email)
    if not passed:
        return "skip", reason

    # 4. Manual sender blacklist -> "spam"
    is_blacklisted, reason = _check_sender_blacklist(email)
    if is_blacklisted:
        return "spam", reason

    # 5. Learned spam-feedback filter -> "spam"
    is_learned_spam, reason = _check_learned_spam_feedback(email)
    if is_learned_spam:
        return "spam", reason

    # 6. Rate limit -> "skip"
    passed, reason = _check_rate_limit(email)
    if not passed:
        return "skip", reason

    # 7. Content sanity -> "skip"
    passed, reason = _check_content_sanity(email)
    if not passed:
        return "skip", reason

    # 8. Forced escalation (legal/refund/angry VIP) -> "escalate"
    should_escalate, escalate_reason = _check_forced_escalation(email)
    if should_escalate:
        return "escalate", escalate_reason

    return "proceed", None


if __name__ == "__main__":
    # Standalone test: python -m src.step5_guardrails
    test_cases = [
        {
            "label": "Native Gmail SPAM label (should be SPAM -> moved to Spam folder)",
            "email": {"from": "scammer@fake.com", "label_ids": ["INBOX", "UNREAD", "SPAM"], "body": "You won a lottery, click here to claim your prize now!", "thread_id": "t0"},
        },
        {
            "label": "Promo email (Gmail category, NOT spam)",
            "email": {"from": "deals@shop.com", "label_ids": ["INBOX", "UNREAD", "CATEGORY_PROMOTIONS"], "body": "Big sale this week, don't miss out!", "thread_id": "t1"},
        },
        {
            "label": "No-reply sender (loop prevention)",
            "email": {"from": "no-reply@service.com", "label_ids": ["INBOX", "CATEGORY_UPDATES"], "body": "Your order has shipped today.", "thread_id": "t2"},
        },
        {
            "label": "Too short body (content sanity)",
            "email": {"from": "customer@example.com", "label_ids": ["INBOX", "CATEGORY_PERSONAL"], "body": "Hi", "thread_id": "t3"},
        },
        {
            "label": "Genuine email (should PROCEED)",
            "email": {"from": "customer@example.com", "label_ids": ["INBOX", "CATEGORY_PERSONAL"], "body": "Hi, can you tell me your working hours please?", "thread_id": "t4"},
        },
        {
            "label": "Legal threat (should ESCALATE, LLM skipped)",
            "email": {"from": "angry@example.com", "label_ids": ["INBOX"], "body": "This is unacceptable, I will take legal action and speak to my lawyer.", "thread_id": "t5"},
        },
        {
            "label": "Refund dispute (should ESCALATE)",
            "email": {"from": "customer2@example.com", "label_ids": ["INBOX"], "body": "I want a refund for this unauthorized charge on my card immediately.", "thread_id": "t6"},
        },
    ]

    for case in test_cases:
        outcome, reason = check_guardrails(case["email"])
        print(f"{case['label']}: outcome={outcome.upper()} | reason={reason}")
