"""
config.py
---------
Single source of truth for all NON-SECRET settings.

Secrets (GROQ_API_KEY, Gmail token) live in .env / platform variables.
Values that differ between local and cloud (paths, auth mode, signature)
can be overridden with environment variables; everything else is a
plain constant here so it is never hardcoded inside a module.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


# -- Secrets ------------------------------------------------------------
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
AGENT_EMAIL = (os.getenv("AGENT_EMAIL") or "").strip().lower()

# -- Gmail OAuth --------------------------------------------------------
# 'modify' = read + send + labels + drafts (no permanent delete).
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = os.getenv("GMAIL_CREDENTIALS_FILE", "credentials.json")
TOKEN_FILE = os.getenv("GMAIL_TOKEN_FILE", "token.json")
# Headless / cloud: the full token.json content, base64-encoded.
TOKEN_JSON_B64_ENV = "GMAIL_TOKEN_JSON_B64"
# Browser login is only allowed where a human can click (local machine).
ALLOW_INTERACTIVE_AUTH = _env_bool("ALLOW_INTERACTIVE_AUTH", True)

# -- Polling ------------------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "60"))
MAX_EMAILS_PER_CYCLE = int(os.getenv("MAX_EMAILS_PER_CYCLE", "50"))

# -- LLM (Groq) ---------------------------------------------------------
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
LLM_MAX_RETRIES = 3            # retries for rate-limit / network errors
LLM_MAX_BODY_CHARS = 3000      # email body sent to the model is truncated
LLM_TEMPERATURE = 0.2          # low = consistent decisions

# Signature is personal -> configured per deployment, never hardcoded.
AGENT_SIGNATURE = os.getenv("AGENT_SIGNATURE", "Best regards").replace("\\n", "\n")

# Facts the agent may state (hours, pricing, policies). If a question
# needs a fact that is NOT in this file, the agent must escalate instead
# of inventing an answer.
BUSINESS_CONTEXT_FILE = os.getenv("BUSINESS_CONTEXT_FILE", "knowledge/business_info.md")

# -- Gmail query --------------------------------------------------------
# Every message the agent finishes gets AI_PROCESSED_LABEL, and the query
# excludes it. Gmail itself therefore remembers what was handled, so:
#  * escalated mail can stay UNREAD for the human without being re-fetched
#  * a lost/rebuilt SQLite DB (e.g. cloud redeploy) cannot cause re-replies
# 'in:inbox' already excludes Spam and Trash.
AI_PROCESSED_LABEL = "AI-Processed"


def gmail_label_query_name(label: str) -> str:
    """Gmail search syntax for a user label: lowercase, spaces -> '-'."""
    return label.strip().lower().replace(" ", "-")


# Only recent mail is considered. Without this, an inbox with a large unread
# backlog would be worked through 50 messages per cycle until the agent
# answers mail that is weeks or months old. 0 disables the limit.
MAX_EMAIL_AGE_DAYS = int(os.getenv("MAX_EMAIL_AGE_DAYS", "2"))


def build_gmail_query(max_age_days: int = None) -> str:
    days = MAX_EMAIL_AGE_DAYS if max_age_days is None else max_age_days
    query = f"is:unread in:inbox -label:{gmail_label_query_name(AI_PROCESSED_LABEL)}"
    if days and days > 0:
        query += f" newer_than:{int(days)}d"
    return query


GMAIL_QUERY = build_gmail_query()

# -- Gmail category labels (guardrails) ---------------------------------
# SPAM never reaches the agent with the default 'in:inbox' query. The
# check is kept as defence-in-depth in case GMAIL_QUERY is ever widened.
NATIVE_SPAM_LABELS = ["SPAM"]
NON_SPAM_SKIP_LABELS = ["CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_FORUMS"]
SPAM_GMAIL_LABEL = "SPAM"

# -- Sender rules -------------------------------------------------------
# Matched against the LOCAL PART of the parsed sender address.
AUTO_SKIP_LOCAL_PARTS = [
    "no-reply", "noreply", "donotreply", "do-not-reply",
    "mailer-daemon", "postmaster", "bounce", "bounces",
]
# Entries: exact address ("x@y.com") or whole domain ("@y.com").
SENDER_BLACKLIST: list[str] = []
VIP_SENDERS: list[str] = []

# -- Rate limiting ------------------------------------------------------
MAX_REPLIES_PER_THREAD_PER_HOUR = int(os.getenv("MAX_REPLIES_PER_THREAD_PER_HOUR", "1"))

# -- Content sanity -----------------------------------------------------
MIN_BODY_LENGTH = 10

# -- Failure handling ---------------------------------------------------
# A message whose processing keeps failing (e.g. LLM error) is handed to a
# human after this many attempts instead of being retried forever.
MAX_PROCESSING_ATTEMPTS = 3

# -- Read/unread behaviour ----------------------------------------------
# Agent-replied mail is marked read (it has been answered). Escalated mail
# always stays unread (a human still has to look). Skipped mail stays
# unread by default so the agent never silently hides the user's mail.
MARK_SKIPPED_AS_READ = _env_bool("MARK_SKIPPED_AS_READ", False)

# -- Labels the agent manages -------------------------------------------
AI_REPLIED_LABEL = "AI-Replied"
NEEDS_HUMAN_LABEL = "Needs-Human"
LOW_CONFIDENCE_LABEL = "Needs-Review"
HUMAN_APPROVED_LABEL = "Human-Approved"
CATEGORY_LABEL_PREFIX = "Category"
PRIORITY_LABEL_PREFIX = "Priority"

# -- Paths --------------------------------------------------------------
# On Railway point AGENT_DATA_DIR at a mounted Volume (e.g. /data).
DATA_DIR = os.getenv("AGENT_DATA_DIR", "data")
LOGS_DIR = os.getenv("AGENT_LOGS_DIR", "logs")
DB_PATH = os.path.join(DATA_DIR, "agent_state.db")
LOG_PATH = os.path.join(LOGS_DIR, "agent.log")

# -- Logging ------------------------------------------------------------
LOG_TO_FILE = _env_bool("LOG_TO_FILE", True)   # cloud: stdout is enough
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

# -- Classification -----------------------------------------------------
EMAIL_CATEGORIES = [
    "spam", "sales_inquiry", "complaint", "support_request",
    "meeting_request", "invoice_payment", "general_query", "other",
]
PRIORITY_LEVELS = ["high", "medium", "low"]
SENTIMENT_LEVELS = ["positive", "neutral", "negative", "angry"]
SUPPORTED_LANGUAGES = ["english", "urdu", "roman_urdu", "other"]
CONFIDENCE_THRESHOLD = 0.6

# Urgency hints only change processing ORDER inside a cycle.
URGENT_KEYWORDS = [
    "asap", "urgent", "immediately", "right away", "emergency",
    "deadline", "today", "tonight", "critical",
    "fauran", "jaldi", "zaroori",
]

# -- Forced-escalation rules (whole-word / whole-phrase matching) -------
LEGAL_THREAT_KEYWORDS = [
    "lawsuit", "sue you", "sue your company", "legal action", "legal notice",
    "attorney", "lawyer", "take you to court", "see you in court",
    "consumer court", "small claims", "cease and desist",
    "court case", "court mein", "qanooni karwai",
]
# A bare "refund" is NOT forced: "what is your refund policy?" is a normal
# question. Only demand / dispute phrasing is forced to a human. Any other
# refund-related mail still reaches the agent, whose prompt forbids
# promising refunds and tells it to escalate them.
REFUND_DISPUTE_KEYWORDS = [
    "want a refund", "want my refund", "need a refund", "demand a refund",
    "full refund", "refund my", "refund me", "issue a refund",
    "money back", "chargeback", "charge back", "dispute the charge",
    "unauthorized charge", "unauthorised charge", "charged twice",
    "double charged", "paisay wapis", "paise wapis", "refund chahiye",
    "refund karo", "refund kar dein",
]
ANGRY_TONE_PHRASES = [
    "unacceptable", "furious", "extremely disappointed", "disgusted",
    "worst service", "never again", "outrageous", "ridiculous",
    "bakwaas", "bohat bura", "sharam",
]
# Text that tries to steer the model ("ignore previous instructions").
# Such mail is never auto-answered.
PROMPT_INJECTION_PATTERNS = [
    "ignore previous instructions", "ignore all previous instructions",
    "ignore the above", "disregard previous instructions",
    "disregard all instructions", "reveal your system prompt",
    "print your system prompt", "developer mode", "new instructions:",
]

# -- Reply output checks (post-LLM, deterministic) ----------------------
MAX_REPLY_CHARS = 2000