# Changelog

## 2.0.0

### Fixed
- Escalated emails were marked as read, so the human could miss them. They now stay unread and carry `Needs-Human` / `Needs-Review`.
- Keyword rules used substring matching ("courtesy" matched "court", every "refund policy?" question was escalated). Rules now match whole words/phrases, and only refund *demands/disputes* are forced to a human.
- HTML-only emails had an empty body and were silently skipped. HTML is now converted to text and declared charsets are honoured.
- A failed label call after a successful send could re-send the same reply next cycle. The reply is now recorded before any labelling; failed or interrupted sends go to a human instead of being retried.
- The Gmail SPAM check could never run with the `in:inbox` query; it is now an explicit defence-in-depth rule that leaves such mail alone.
- Tests wrote into the real `data/agent_state.db`. Every test now uses a temporary database.
- `MAX_REPLIES_PER_THREAD_PER_HOUR` above 1 was ignored. The real count in the last hour is now compared with the setting.
- Blacklist/VIP/no-reply matching used substrings of the From header; it now matches the parsed address or domain.

### Added
- `AI-Processed` label: Gmail remembers what was handled, so unread escalations are not re-fetched and a lost DB cannot cause duplicate replies.
- Post-LLM policy: low-confidence or suspicious drafts become a Gmail draft (`Needs-Review`) instead of being sent.
- Business context file; the agent may only state facts from it and must escalate otherwise.
- Prompt-injection hardening (pre-LLM rule + untrusted-data framing in the prompt).
- RFC 3834 loop prevention (`Auto-Submitted`, `Precedence`, `List-Id`, `List-Unsubscribe`) and `Auto-Submitted: auto-replied` on replies; `Reply-To` support; 7-bit safe Urdu encoding.
- Bounded retries: transient Groq errors back off and retry; messages failing 3 times go to a human.
- Headless Gmail auth for servers (`GMAIL_TOKEN_JSON_B64`, `python main.py export-token`), Railway volume support, graceful SIGTERM shutdown, `--once` and `--dry-run`.
- Production webhook: OIDC-verified Pub/Sub pushes, immediate ack, single background worker with coalescing, fallback polling, automatic daily watch renewal, `/health`.
- Evaluation framework with a 55-email labelled seed set and business-risk metrics.
- Streamlit human-review console (review queue, decision log with verdicts, metrics).
- Test suite rewritten around an in-memory Gmail fake with failure injection; CI on Python 3.11 and 3.12.

### Changed
- Signature, model and paths are configurable via environment variables (no personal name hardcoded).
- Dependencies are pinned to the versions the suite was verified with.