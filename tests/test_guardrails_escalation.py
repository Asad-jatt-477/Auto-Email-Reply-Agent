"""
test_guardrails_escalation.py
-------------------------------
Phase 12 — Automated tests for the new 3-way guardrail outcome
(proceed/skip/escalate), forced-escalation keyword rules, angry-VIP
detection, and the spam-learning feedback loop.

No live API calls here (pure logic + local SQLite), safe to run anytime.

Run: python -m tests.test_guardrails_escalation
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.step5_guardrails import check_guardrails, passes_guardrails
from src.step3_state import init_db, record_sender_feedback, get_sender_feedback_verdict


def test_genuine_email_proceeds():
    email = {"from": "customer@example.com", "label_ids": ["INBOX"], "body": "Can you tell me your working hours?", "thread_id": "g1", "subject": "Hours?"}
    outcome, reason = check_guardrails(email)
    assert outcome == "proceed", f"expected proceed, got {outcome} ({reason})"
    print("PASS: genuine email proceeds to LLM")


def test_promo_label_skips():
    email = {"from": "deals@shop.com", "label_ids": ["CATEGORY_PROMOTIONS"], "body": "Big sale!", "thread_id": "g2", "subject": "Sale"}
    outcome, reason = check_guardrails(email)
    assert outcome == "skip"
    print("PASS: promotional label results in skip (not moved to spam)")


def test_native_spam_label_results_in_spam():
    email = {"from": "scammer@fake.com", "label_ids": ["SPAM"], "body": "You won a lottery, claim now!", "thread_id": "g8", "subject": "Winner!!!"}
    outcome, reason = check_guardrails(email)
    assert outcome == "spam", f"expected spam, got {outcome}"
    print("PASS: Gmail's native SPAM label results in 'spam' outcome (moved to Spam folder)")


def test_sender_blacklist_results_in_spam(monkeypatch=None):
    import src.step5_guardrails as guardrails_module
    original_blacklist = guardrails_module.SENDER_BLACKLIST
    guardrails_module.SENDER_BLACKLIST = ["blacklisted-spammer@example.com"]
    try:
        email = {"from": "blacklisted-spammer@example.com", "label_ids": ["INBOX"], "body": "Some junk offer.", "thread_id": "g9", "subject": "Offer"}
        outcome, reason = check_guardrails(email)
        assert outcome == "spam", f"expected spam, got {outcome}"
        print("PASS: blacklisted sender results in 'spam' outcome")
    finally:
        guardrails_module.SENDER_BLACKLIST = original_blacklist


def test_legal_threat_forces_escalation():
    email = {
        "from": "angry@example.com", "label_ids": ["INBOX"], "thread_id": "g3",
        "subject": "Final warning",
        "body": "If this isn't resolved I will contact my lawyer and take legal action.",
    }
    outcome, reason = check_guardrails(email)
    assert outcome == "escalate", f"expected escalate, got {outcome}"
    assert "legal" in reason.lower()
    print("PASS: legal threat keyword forces escalation (no LLM call)")


def test_refund_dispute_forces_escalation():
    email = {
        "from": "customer2@example.com", "label_ids": ["INBOX"], "thread_id": "g4",
        "subject": "Charge issue",
        "body": "This is an unauthorized charge on my card, I want a refund immediately.",
    }
    outcome, reason = check_guardrails(email)
    assert outcome == "escalate"
    print("PASS: refund/payment dispute keyword forces escalation")


def test_angry_vip_forces_escalation(monkeypatch=None):
    import src.step5_guardrails as guardrails_module
    original_vips = guardrails_module.VIP_SENDERS
    guardrails_module.VIP_SENDERS = ["vip@bigclient.com"]
    try:
        email = {
            "from": "VIP@BigClient.com", "label_ids": ["INBOX"], "thread_id": "g5",
            "subject": "TERRIBLE experience",
            "body": "This is absolutely UNACCEPTABLE, worst service I have EVER received!!!",
        }
        outcome, reason = check_guardrails(email)
        assert outcome == "escalate", f"expected escalate for angry VIP, got {outcome}"
        print("PASS: angry-tone VIP sender forces escalation")
    finally:
        guardrails_module.VIP_SENDERS = original_vips


def test_backwards_compatible_wrapper():
    email = {"from": "customer@example.com", "label_ids": ["INBOX"], "body": "Simple question please.", "thread_id": "g6", "subject": "Q"}
    passed, reason = passes_guardrails(email)
    assert passed is True
    print("PASS: passes_guardrails() backwards-compatible boolean wrapper works")


def test_spam_feedback_loop_learns():
    init_db()
    sender = "repeated_spammer@example.com"
    assert get_sender_feedback_verdict(sender) is None

    record_sender_feedback(sender, is_spam=True)
    assert get_sender_feedback_verdict(sender) is None  # 1 vote, threshold is 2

    record_sender_feedback(sender, is_spam=True)
    verdict = get_sender_feedback_verdict(sender)
    assert verdict == "spam", f"expected 'spam' after 2 corrections, got {verdict}"

    email = {"from": sender, "label_ids": ["INBOX"], "body": "Buy my product now!", "thread_id": "g7", "subject": "Offer"}
    outcome, reason = check_guardrails(email)
    assert outcome == "spam", f"expected spam, got {outcome}"
    print("PASS: sender learned as spam after repeated feedback, now moved to Spam folder")


if __name__ == "__main__":
    test_genuine_email_proceeds()
    test_promo_label_skips()
    test_native_spam_label_results_in_spam()
    test_sender_blacklist_results_in_spam()
    test_legal_threat_forces_escalation()
    test_refund_dispute_forces_escalation()
    test_angry_vip_forces_escalation()
    test_backwards_compatible_wrapper()
    test_spam_feedback_loop_learns()
    print("\nALL AUTOMATED GUARDRAIL/ESCALATION/FEEDBACK TESTS PASSED (9/9)")
