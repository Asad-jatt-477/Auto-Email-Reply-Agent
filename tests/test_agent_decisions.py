"""
test_agent_decisions.py
-------------------------
Phase 11 — Manual test: 3 alag test-case emails (normal/promo/complaint)
LLM ko bhej kar check karo ke agent teeno ko sahi classify kar raha hai.

Ye LIVE Groq API call karta hai, isliye is file mein hard assert
statements nahi hain — LLM ka output word-for-word predictable nahi
hota, isliye tumhe khud output dekh kar confirm karna hai ke "Actual
decision" wahi hai jo "expected" mein likha hai.

Isko chalane ke liye .env mein VALID GROQ_API_KEY honi zaroori hai.

Run: python -m tests.test_agent_decisions
"""

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.step4_agent import run_agent

TEST_CASES = [
    {
        "label": "Normal query",
        "expected": "reply",
        "email": {
            "from": "customer@example.com",
            "subject": "Question about your working hours",
            "body": "Hi, I wanted to ask what your working hours are on weekends. Thanks!",
        },
    },
    {
        "label": "Promotional",
        "expected": "skip",
        "email": {
            "from": "newsletter@somebrand.com",
            "subject": "50% OFF - This weekend only!",
            "body": "Huge sale this weekend, click here to shop now and save big!",
        },
    },
    {
        "label": "Sensitive complaint",
        "expected": "escalate",
        "email": {
            "from": "angrycustomer@example.com",
            "subject": "I want a refund NOW, this is unacceptable",
            "body": (
                "Your product broke after one day and no one is responding "
                "to my emails. I am extremely unhappy and want a full "
                "refund immediately or I will take legal action."
            ),
        },
    },
]


if __name__ == "__main__":
    print("Running Phase 11 manual agent-decision test (live Groq API call)...\n")

    results = []
    for case in TEST_CASES:
        print("=" * 60)
        print(f"{case['label']} (expected: {case['expected']})")
        decision = run_agent(case["email"])
        matched = decision["action"] == case["expected"]
        results.append(matched)

        print(f"Actual decision: {decision['action']}  {'[MATCH]' if matched else '[MISMATCH]'}")
        if decision["action"] == "reply":
            print(f"Draft preview: {(decision['draft_text'] or '')[:150]}")
        else:
            print(f"Reason: {decision['reason']}")

    print("\n" + "=" * 60)
    print(f"Result: {sum(results)}/{len(results)} matched expected action.")
    print("Note: LLM decisions can vary slightly run-to-run — a mismatch")
    print("doesn't always mean a bug, but investigate if it happens often.")
