# Email Reply AI Agent

**An autonomous, Gmail-integrated AI agent that reads incoming email, decides whether it needs a response, drafts that response in the sender's own language and tone, and — when it is confident enough — sends it, entirely on its own.**

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white">
  <img alt="LLM" src="https://img.shields.io/badge/LLM-Groq-F55036">
  <img alt="Gmail API" src="https://img.shields.io/badge/Gmail-API-EA4335?logo=gmail&logoColor=white">
  <img alt="Status" src="https://img.shields.io/badge/Status-Production--Ready-1E8E5A">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-lightgrey">
</p>

---

## Overview

The **Email Reply AI Agent** is a fully autonomous email-handling system built on top of the Gmail API and a Groq-hosted large language model. Unlike a simple auto-responder, it behaves like an agentic system: on every polling cycle it fetches unread mail, filters out noise, reasons about each message using LLM-driven tool calling, and independently chooses one of three actions — **reply**, **skip**, or **escalate to a human** — before acting on that decision inside the user's actual Gmail account.

The project was built as a complete, end-to-end system rather than a proof of concept: authentication, decision-making, guardrails, sending, logging, automated testing, and deployment are all implemented and working together as a single pipeline.

## Problem Statement

Individuals and small teams who manage their own inbox face a recurring set of problems:

- **Repetitive, low-value replies** (business hours, availability, simple queries) consume time that could be spent elsewhere.
- **Manual triage is inconsistent** — urgent messages, angry customers, or legal/refund disputes can sit unread alongside newsletters.
- **Naive "auto-reply" tools are unsafe.** They cannot tell a genuine customer complaint from a promotional email, cannot detect urgency or sentiment, and will confidently send a wrong or tone-deaf reply with no human oversight.
- **Multilingual inboxes** (English, Urdu, Roman Urdu) are poorly served by most automation tools, which assume a single language.

A system that blindly replies to everything is not useful — it is a liability. What is needed is an agent that can be trusted to act *only* when it is safe and appropriate to do so, and to defer to a human otherwise.

## Our Solution

The Email Reply AI Agent addresses this by combining **agentic LLM tool-calling** with a **deterministic, zero-token safety layer** that runs before any model call is made. Every email passes through:

1. **Fetching** — only new, unread messages are considered.
2. **Deduplication** — a local state store guarantees no message is processed twice.
3. **Guardrails** — cheap, rule-based checks (spam signals, sender blacklists, VIP senders, legal/refund keywords) run first, without spending any LLM tokens.
4. **Agent reasoning** — a single Groq LLM call classifies intent, urgency, sentiment, and language, assigns a confidence score, and chooses an action.
5. **Action execution** — the agent replies in-thread through the real Gmail API, escalates to a human via a Gmail label, or takes no action.
6. **Logging** — every decision is recorded for auditability.

The result is an agent that automates the repetitive 80% of inbox triage while deliberately routing the sensitive or ambiguous 20% to a human — instead of guessing.

## System Architecture

![Email Reply AI Agent architecture diagram](architecture-diagram.svg)

The diagram above shows the full decision pipeline the agent executes on every polling cycle. At a high level:

| Stage | Responsibility | Module |
|---|---|---|
| Orchestrator | Drives the polling loop | `main.py` |
| Fetcher | Retrieves unread Gmail messages | `src/step2_fetcher.py` |
| State Guard | Prevents reprocessing the same email | `src/step3_state.py` |
| Guardrails | Zero-token spam / VIP / legal-keyword filtering | `src/step5_guardrails.py` |
| Agent Core | LLM-driven classification and decision-making | `src/step4_agent.py` |
| Sender | Sends the real, in-thread Gmail reply | `src/step6_sender.py` |
| Logger | Persists every decision and outcome | `src/step7_logger.py` |

Only messages that pass every guardrail reach the LLM, which keeps the system both fast and inexpensive to run on free-tier API limits.

## Key Features

- **Fully autonomous inbox loop** — continuously polls Gmail, with no manual triggering required.
- **Real, in-thread Gmail replies** — not a simulated draft; replies are sent through the Gmail API and stay correctly threaded.
- **Agentic tool-calling decision-making** — the LLM itself selects between `reply_to_email`, `skip`, and `escalate_to_human` as callable tools, rather than the code guessing an intent from raw text.
- **Intent and category classification** — every email is labeled as `sales_inquiry`, `complaint`, `support_request`, `meeting_request`, `invoice_payment`, `general_query`, or `other`, and tagged with a matching Gmail label automatically.
- **Priority and urgency detection** — a free keyword pre-scan re-orders each polling batch so urgent messages are handled first, and the LLM assigns a final priority label.
- **Sentiment-aware replies** — negative or angry senders automatically receive a more empathetic tone, and genuinely angry complaints are escalated rather than auto-replied to.
- **Multilingual support** — English, Urdu (script), and Roman Urdu are detected automatically, and the reply is drafted in the same language the sender used.
- **Confidence-gated sending** — if the model's confidence in a reply falls below a configurable threshold, the agent escalates instead of guessing, and tags the email `Needs-Review`.
- **Zero-token escalation rules** — legal-threat language, refund/payment disputes, and angry messages from VIP senders are force-escalated to a human before any LLM call is made, both for safety and cost efficiency.
- **Self-learning spam filter** — spam-classification mistakes can be corrected via a simple CLI command; after a configurable number of corrections, the agent automatically adjusts its future treatment of that sender.
- **Real spam handling** — confirmed spam is physically moved into Gmail's Spam folder, not just silently ignored.
- **Structured logging** — every fetch, decision, and action is logged to `logs/agent.log` for full auditability.
- **Automated test suite** — fetcher, sender/MIME, agent decision-making, and guardrail/escalation logic are all covered by dedicated tests.
- **Optional real-time mode** — a Gmail Pub/Sub push-notification webhook is available as a drop-in alternative to polling.
- **Deployment-ready** — documented for both local scheduled execution (Windows Task Scheduler) and 24/7 cloud hosting (Railway).

## What Makes This Different

Most "email auto-reply" projects fall into one of two categories: a fixed if/else rule engine, or a thin wrapper that sends whatever the LLM outputs with no safety net. This project is neither.

- **Safety comes before intelligence.** Deterministic guardrails run *before* the LLM is ever called, so the riskiest categories of email (legal threats, payment disputes, angry VIPs) never depend on model judgment at all.
- **One LLM call does the work of five.** Intent, priority, sentiment, language, and confidence are all extracted from a single Groq tool-calling call, keeping the system fast and within free-tier rate limits rather than chaining multiple expensive API calls.
- **The agent knows when not to act.** Confidence scoring means the system is explicitly designed to defer to a human when it is unsure, rather than optimizing for reply volume.
- **It improves itself.** The spam-feedback loop lets the system learn from corrections without retraining a model or writing new rules by hand.
- **It is genuinely bilingual.** Replies are drafted in the sender's own language and register, which most inbox-automation tools do not attempt.

### Scenarios

| Scenario | Trigger | Expected Behavior | Log Entry |
|---|---|---|---|
| Promotional email | Any newsletter-type message | Filtered by guardrails, never reaches the LLM | `SKIP (guardrails)` |
| Normal query | e.g. "What are your business hours?" | LLM replies directly, in-thread | `REPLIED` |
| Complaint / legal threat | e.g. refund demand with legal language | LLM escalates, `Needs-Human` label applied, no auto-reply sent | `ESCALATED` |



## Conclusion

The Email Reply AI Agent demonstrates a complete, production-oriented approach to inbox automation: it does not simply generate replies, it makes accountable decisions about *when* to reply, *when* to stay silent, and *when* to hand control back to a human. By pairing deterministic safety guardrails with a single, efficient LLM reasoning step, the system is both cost-effective and trustworthy enough to run unattended against a real inbox — which is the standard a genuinely useful email agent needs to meet.
