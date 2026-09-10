"""
step4_agent.py
----------------
Phase 6 — Agent Core (Decision + Draft using Groq Tool-Calling).
Phase 12 — Intent, Priority, Sentiment, Language & Confidence added.

Ye asli "Agent" wala part hai. Groq ke tool-calling (function calling)
use karte hain taake LLM khud decide kare ke kya karna hai — sirf text
generate nahi karwa rahe, balke ek STRUCTURED decision le rahe hain
teen options mein se:

1. reply_to_email   -> email clear/safe hai, draft karke reply karo
2. skip_email       -> spam/newsletter/no-action-needed
3. escalate_to_human -> complaint/legal/sensitive, AI confidently
                         handle nahi kar sakta

IMPORTANT (token efficiency / Golden Rule): saari classification
(category, priority, sentiment, language, confidence) isi EK tool-call
ke andar li jati hai — alag LLM calls nahi lagate har cheez ke liye.
Isse free-tier Groq rate/token limits ka khayal rehta hai (1 email =
1 LLM call, chahe kitne bhi signals nikalne hon).

NOTE: Ye file khud email NAHI bhejti — wo Phase 8 (step6_sender.py) ka
kaam hai. Ye sirf DECISION leti hai aur (agar reply hai to) draft text
taiyar karti hai.
"""

import json

from groq import Groq

from src.config import (
    GROQ_API_KEY,
    GROQ_MODEL,
    AGENT_SIGNATURE,
    EMAIL_CATEGORIES,
    PRIORITY_LEVELS,
    SENTIMENT_LEVELS,
    SUPPORTED_LANGUAGES,
)

client = Groq(api_key=GROQ_API_KEY)


# ── Shared classification fields (added to EVERY tool below) ─────────
# Har action (reply/skip/escalate) ke sath ye signals bhi mangwate hain
# taake ek hi call mein poori classification mil jaye.
_CLASSIFICATION_PROPS = {
    "category": {
        "type": "string",
        "enum": EMAIL_CATEGORIES,
        "description": "Email ka intent/category — sabse qareeb wala option chuno.",
    },
    "priority": {
        "type": "string",
        "enum": PRIORITY_LEVELS,
        "description": (
            "Urgency level. 'high' = ASAP/deadline/emergency/angry-urgent "
            "tone, 'medium' = normal business request, 'low' = koi jaldi nahi."
        ),
    },
    "sentiment": {
        "type": "string",
        "enum": SENTIMENT_LEVELS,
        "description": "Sender ka tone/mood is email mein.",
    },
    "language": {
        "type": "string",
        "enum": SUPPORTED_LANGUAGES,
        "description": (
            "Email kis language/script mein likhi gayi hai. 'roman_urdu' "
            "matlab Urdu Roman/English letters mein likhi gayi ('kya haal hai' jaisi)."
        ),
    },
    "confidence": {
        "type": "number",
        "description": (
            "0.0 se 1.0 tak — tumhe apne is decision (khaas kar reply "
            "draft) par kitna yaqeen hai. Agar email ambiguous/complex "
            "hai to kam confidence do (jaise 0.4), taake human review kar sake."
        ),
    },
}

_CLASSIFICATION_REQUIRED = ["category", "priority", "sentiment", "language", "confidence"]


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "reply_to_email",
            "description": (
                "Email ka reply draft karke bhejne ke liye jab email "
                "clear, safe, aur confidently handle karne ke qabil ho "
                "(normal queries, simple questions, routine correspondence)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "draft_text": {
                        "type": "string",
                        "description": (
                            "Reply email ka pura draft text. USE THE SAME "
                            "LANGUAGE as the 'language' field you detected "
                            "(agar email Roman Urdu mein thi to reply bhi "
                            "Roman Urdu mein likho, agar Urdu script mein "
                            "thi to Urdu script mein). Agar sentiment "
                            "'negative' ya 'angry' hai to tone extra "
                            "soft/polite/empathetic rakho. Professional "
                            "tone, signature ke saath end hona chahiye."
                        ),
                    },
                    **_CLASSIFICATION_PROPS,
                },
                "required": ["draft_text"] + _CLASSIFICATION_REQUIRED,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "skip_email",
            "description": (
                "Email ko skip karo jab ye spam, newsletter, automated "
                "notification, ya kisi bhi tarah se reply ki zaroorat na ho. "
                "Agar email actual spam/scam/phishing/fraudulent/unsolicited "
                "bulk-junk hai, category field mein 'spam' zaroor set karo — "
                "isse ye email Gmail ke Spam folder mein physically move ho "
                "jayegi."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Skip karne ki wajah, ek chhota sentence.",
                    },
                    **_CLASSIFICATION_PROPS,
                },
                "required": ["reason"] + _CLASSIFICATION_REQUIRED,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalate_to_human",
            "description": (
                "Email ko human ke paas bhejo jab ye complaint, legal "
                "matter, payment/refund dispute, angry/frustrated "
                "customer, VIP customer, ya koi bhi sensitive ya unclear "
                "cheez ho jo AI confidently khud handle na kar sake."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Escalate karne ki wajah, ek chhota sentence.",
                    },
                    **_CLASSIFICATION_PROPS,
                },
                "required": ["reason"] + _CLASSIFICATION_REQUIRED,
            },
        },
    },
]


SYSTEM_PROMPT = f"""Tum ek professional email assistant ho jo user ki taraf
se incoming emails ka jawab dete ho. Har email dekh kar teen mein se EK
action zaroor lo — sirf plain text mat likho, hamesha ek tool call karo:

1. reply_to_email    — email clear hai, tum confidently professional
   reply draft kar sakte ho.
2. skip_email        — spam, newsletter, automated notification, ya
   koi action ki zaroorat nahi.
3. escalate_to_human — complaint, legal matter, refund/payment dispute,
   angry/frustrated customer, ya koi sensitive/unclear cheez jo tum
   khud confidently handle nahi kar sakte.

Har tool call ke sath ye classification fields BHI hamesha do (chahe
action kuch bhi ho): category, priority, sentiment, language, confidence.

Spam rule (IMPORTANT): agar email actual spam/scam/phishing/fraudulent/
unsolicited bulk-junk hai (jaise "you won a lottery", fake prize claims,
suspicious links, impersonation, malware attachments ka ishara, ya
bilkul random unsolicited marketing jo kisi legitimate business ki
taraf se nahi lagti), to skip_email use karo aur category ko bilkul
theek se "spam" set karo — ye email is category ki wajah se Gmail ke
Spam folder mein physically move kar di jayegi. Agar email sirf ek
newsletter hai jo user ne khud subscribe ki ho, ya ek harmless
automated notification hai, us par category "spam" MAT lagao (bas
"other" ya jo bhi theek ho use karo) — warna legitimate mail galti se
Spam folder mein chali jayegi.

Language rule: email jis language/script mein aaye (English, Urdu
script, ya Roman Urdu), reply bhi USI language mein likho — sender ko
apni hi zabaan mein professional jawab milna chahiye.

Sentiment rule: agar sender gussay mein ya frustrated lag raha hai,
reply ka tone extra polite/empathetic/soothing rakho. Agar gussa
serious complaint/dispute ke sath mila hua hai, reply mat karo —
escalate_to_human use karo, taake ek insaan handle kare.

Confidence rule: agar email ambiguous, technical, ya unusual hai aur
tumhe apne reply par poora yaqeen nahi, confidence ko 0.5 se kam rakho
— system khud is signal se human review trigger kar dega.

Reply draft karte waqt hamesha is signature ke saath end karo:
{AGENT_SIGNATURE}

Tone: professional, concise, helpful. Kabhi bhi wo cheez promise mat karo
jo email mein pooche gaye sawaal se bahar ho (jaise refund process, price
changes) — aisi cheezon ke liye escalate karo."""


def _safe_float(value, default=0.5):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _extract_classification(arguments: dict) -> dict:
    """Common fields jo teeno tools se aatay hain, ek jagah nikal lo."""
    return {
        "category": arguments.get("category", "other"),
        "priority": arguments.get("priority", "medium"),
        "sentiment": arguments.get("sentiment", "neutral"),
        "language": arguments.get("language", "english"),
        "confidence": _safe_float(arguments.get("confidence")),
    }


def run_agent(email: dict) -> dict:
    """
    Ek email dict leta hai (Phase 4 ke fetch_unread_emails() format mein:
    keys = from, subject, body) aur LLM se decision leta hai.

    Return format:
    {
      "action": "reply" | "skip" | "escalate",
      "draft_text": str | None,   # sirf action == "reply" par set hota hai
      "reason": str | None,       # sirf skip/escalate par set hota hai
      "category": str, "priority": str, "sentiment": str,
      "language": str, "confidence": float,
    }
    """
    user_message = (
        f"From: {email.get('from', '')}\n"
        f"Subject: {email.get('subject', '')}\n\n"
        f"Body:\n{email.get('body', '')[:3000]}"
    )

    response = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        tools=TOOLS,
        tool_choice="required",
    )

    message = response.choices[0].message

    if not message.tool_calls:
        # Safety fallback: agar LLM kisi wajah se tool call nahi karta,
        # hum khud reply nahi bhejte — escalate kar dete hain taake
        # koi galat/anjaani email na chali jaye.
        return {
            "action": "escalate",
            "draft_text": None,
            "reason": "Agent ne koi clear tool decision nahi diya (fallback).",
            "category": "other",
            "priority": "medium",
            "sentiment": "neutral",
            "language": "english",
            "confidence": 0.0,
        }

    tool_call = message.tool_calls[0]
    function_name = tool_call.function.name
    try:
        arguments = json.loads(tool_call.function.arguments)
    except (json.JSONDecodeError, TypeError):
        arguments = {}

    classification = _extract_classification(arguments)

    if function_name == "reply_to_email":
        return {
            "action": "reply",
            "draft_text": arguments.get("draft_text"),
            "reason": None,
            **classification,
        }
    elif function_name == "skip_email":
        return {
            "action": "skip",
            "draft_text": None,
            "reason": arguments.get("reason"),
            **classification,
        }
    elif function_name == "escalate_to_human":
        return {
            "action": "escalate",
            "draft_text": None,
            "reason": arguments.get("reason"),
            **classification,
        }
    else:
        # Kabhi na ho, lekin agar Groq koi anjaana tool naam bheje to
        # safe side pe escalate.
        return {
            "action": "escalate",
            "draft_text": None,
            "reason": f"Unknown tool returned: {function_name}",
            "category": "other",
            "priority": "medium",
            "sentiment": "neutral",
            "language": "english",
            "confidence": 0.0,
        }


if __name__ == "__main__":
    # Standalone test: python -m src.step4_agent
    # (Isko chalane ke liye valid GROQ_API_KEY .env mein honi chahiye)
    test_cases = [
        {
            "from": "customer@example.com",
            "subject": "Question about your working hours",
            "body": "Hi, I wanted to ask what your working hours are on weekends. Thanks!",
        },
        {
            "from": "newsletter@somebrand.com",
            "subject": "50% OFF - This weekend only!",
            "body": "Huge sale this weekend, click here to shop now and save big!",
        },
        {
            "from": "angrycustomer@example.com",
            "subject": "I want a refund NOW, this is unacceptable",
            "body": "Your product broke after one day and no one is responding to my emails. I am extremely unhappy and want a full refund immediately or I will take legal action.",
        },
        {
            "from": "sabir@example.pk",
            "subject": "Meeting time",
            "body": "Assalam o alaikum, kal meeting ka time confirm kar dein please, mujhe 3 baje sy pehly free hona hai.",
        },
        {
            "from": "prize-notify@totally-legit-lottery.tk",
            "subject": "CONGRATULATIONS! You have WON $1,000,000!!!",
            "body": "You have been randomly selected as our lucky winner! Click this link immediately and provide your bank details to claim your prize before it expires.",
        },
    ]

    for i, test_email in enumerate(test_cases, start=1):
        print("=" * 60)
        print(f"Test case {i}: {test_email['subject']}")
        decision = run_agent(test_email)
        print(f"Decision: {decision}")
