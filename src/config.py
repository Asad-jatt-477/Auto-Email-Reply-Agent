"""
config.py
---------
Ye file poore project ki SHARED, NON-SECRET settings rakhti hai.
Golden Rule: koi bhi constant (poll interval, label names, model name, etc.)
kisi aur file mein hardcode NAHI hoga — hamesha yahan se import hoga.

Secrets (API keys) yahan NAHI aatay — wo .env mein rehte hain aur
python-dotenv ke through load hotay hain.
"""

import os
from dotenv import load_dotenv

# .env file load karo (project root se)
load_dotenv()

# ── Secrets (loaded from .env, NEVER hardcoded here) ─────────────────
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
AGENT_EMAIL = os.getenv("AGENT_EMAIL")

# ── Gmail OAuth ────────────────────────────────────────────────────
# 'modify' scope: read + send + label sab is ek scope mein aa jata hai.
# 'send'-only scope isliye nahi liya kyunki hume unread read karna aur
# labels add/remove karna bhi zaroori hai.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

CREDENTIALS_FILE = "credentials.json"   # Phase 1 se download hui file
TOKEN_FILE = "token.json"               # Phase 3 mein auto-generate hogi

# ── Polling ────────────────────────────────────────────────────────
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", 60))

# ── LLM (Groq) ─────────────────────────────────────────────────────
GROQ_MODEL = "openai/gpt-oss-120b"

# ── Agent identity (Phase 6 isko reply drafts mein use karega) ──────
# Apna naam/signature yahan badal lena.
AGENT_SIGNATURE = "Regards,\nAsad"

# ── Gmail Search Query (Phase 4 fetcher isko use karega) ────────────
GMAIL_QUERY = "is:unread in:inbox"

# ── Gmail Category Labels (Phase 7 guardrails isko use karenge) ─────
# In categories ki mail LLM tak kabhi nahi jayegi.
#
# NATIVE_SPAM_LABELS -> Gmail ne khud is email ko SPAM confirm kiya hua
# hai. Ye "spam" outcome deta hai -> email ko physically Spam folder
# mein move kiya jata hai (Phase 13).
#
# NON_SPAM_SKIP_LABELS -> ye sirf Gmail ke inbox TABS hain (Promotions/
# Social/Forums) — ye spam NAHI hain, sender ne khud subscribe/follow
# kiya hota hai, isliye inhe sirf "skip" (reply na karo) kiya jata hai,
# Spam folder mein move NAHI kiya jata.
NATIVE_SPAM_LABELS = ["SPAM"]
NON_SPAM_SKIP_LABELS = [
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_FORUMS",
]

# Gmail ka apna built-in Spam label ID (isay rename nahi kiya ja sakta,
# Gmail API mein hamesha "SPAM" hi rehta hai). Email ko Spam folder mein
# bhejne ke liye isay addLabelIds mein use karte hain (Phase 13).
SPAM_GMAIL_LABEL = "SPAM"

# ── Sender-based loop-prevention (Phase 7) ───────────────────────────
AUTO_SKIP_SENDER_PREFIXES = [
    "no-reply@",
    "noreply@",
    "donotreply@",
    "mailer-daemon@",
    "postmaster@",
]

# Manually blacklist karne ke liye senders (agar koi specific sender
# hamesha skip karna ho) — yahan add karte jana.
SENDER_BLACKLIST: list[str] = []

# ── Rate limiting (Phase 7) ──────────────────────────────────────────
MAX_REPLIES_PER_THREAD_PER_HOUR = 1

# ── Content sanity check (Phase 7) ───────────────────────────────────
MIN_BODY_LENGTH = 10  # isse chhota body = skip

# ── Custom Gmail labels agent khud manage karega ─────────────────────
AI_REPLIED_LABEL = "AI-Replied"
NEEDS_HUMAN_LABEL = "Needs-Human"

# ── Paths ─────────────────────────────────────────────────────────
DATA_DIR = "data"
LOGS_DIR = "logs"
DB_PATH = os.path.join(DATA_DIR, "agent_state.db")
LOG_PATH = os.path.join(LOGS_DIR, "agent.log")

# ── Logging (Phase 10) ────────────────────────────────────────────
LOG_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per file before rotating
LOG_BACKUP_COUNT = 3              # kitni purani rotated files rakhni hain

# ══════════════════════════════════════════════════════════════════
# Phase 12 — Classification, Priority, Escalation & Feedback Settings
# ══════════════════════════════════════════════════════════════════
# NOTE: Golden Rule follow hui hai yahan bhi — koi bhi naya constant
# is section mein hardcode NAHI hoga kisi module ke andar, sab yahan
# se import hoga.

# ── Intent / Category options (LLM inhi mein se ek choose karega) ───
# NOTE (Phase 13): "spam" category is khaas maqsad ke liye hai — jab
# LLM khud (Gmail ki apni classification ke bagair) kisi email ko
# spam/scam/phishing/fraudulent/unsolicited bulk-junk pehchane. category
# == "spam" set hone par ye email skip hone ke sath sath Gmail ke Spam
# folder mein bhi physically move kar di jati hai (main.py dekho).
EMAIL_CATEGORIES = [
    "spam",
    "sales_inquiry",
    "complaint",
    "support_request",
    "meeting_request",
    "invoice_payment",
    "general_query",
    "other",
]

# ── Priority levels ───────────────────────────────────────────────
PRIORITY_LEVELS = ["high", "medium", "low"]

# Deterministic (zero-token) urgency keywords — inbox ko LLM call se
# PEHLE hi rough priority order mein sort karne ke liye use hote hain,
# taake urgent emails pehle process hon (poll cycle ke andar).
URGENT_KEYWORDS = [
    "asap", "urgent", "immediately", "right away", "emergency",
    "deadline", "today", "tonight", "critical", "escalate",
    "fauran", "jaldi", "abhi", "zaroori",  # Roman Urdu urgency words
]

# ── Sentiment options ─────────────────────────────────────────────
SENTIMENT_LEVELS = ["positive", "neutral", "negative", "angry"]

# ── Language options (LLM inhi mein se detect karega) ─────────────
SUPPORTED_LANGUAGES = ["english", "urdu", "roman_urdu", "other"]

# ── Confidence scoring ─────────────────────────────────────────────
# Agar LLM ka apne "reply" decision par confidence isse kam ho, to
# reply khud mat bhejo — human review ke liye flag/escalate kar do.
CONFIDENCE_THRESHOLD = 0.6

# ── Deterministic (pre-LLM) forced-escalation keyword rules ───────
# Ye rules LLM call se PEHLE chalte hain (guardrails ke andar) — agar
# match ho jaye to LLM ko call hi nahi karte (token bachao) aur seedha
# escalate kar dete hain, kyunki ye categories hamesha human-handled
# honi chahiye chahe AI kitna bhi confident kyun na ho.
LEGAL_THREAT_KEYWORDS = [
    "lawsuit", "sue you", "legal action", "attorney", "lawyer",
    "court", "law suit", "legally binding", "cease and desist",
    "consumer court", "small claims",
]

REFUND_DISPUTE_KEYWORDS = [
    "refund", "chargeback", "money back", "dispute the charge",
    "unauthorized charge", "paisay wapis", "refund chahiye",
]

# Angry-tone heuristic (regex-free, cheap) — inn phrases ya 3+ '!'
# ya poora-CAPS lafz milne par tone "angry" treat hota hai pre-LLM.
ANGRY_TONE_PHRASES = [
    "unacceptable", "furious", "extremely disappointed", "disgusted",
    "worst service", "never again", "outrageous", "ridiculous",
    "bakwaas", "bohat bura", "sharam",
]

# VIP senders — inn addresses (ya domains) se aane wali angry-tone
# email hamesha escalate hogi, AI khud reply nahi karega.
VIP_SENDERS: list[str] = []

# ── Gmail triage labels agent khud manage karega (Phase 12) ────────
CATEGORY_LABEL_PREFIX = "Category"        # e.g. "Category-Sales-Inquiry"
PRIORITY_LABEL_PREFIX = "Priority"        # e.g. "Priority-High"
LOW_CONFIDENCE_LABEL = "Needs-Review"     # confidence threshold se neechay
SPAM_LEARNED_LABEL = "Spam-Learned"       # feedback-learned spam senders
