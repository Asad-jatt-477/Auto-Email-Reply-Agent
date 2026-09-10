"""
advanced/webhook_app.py
------------------------
Phase 12 (ADVANCED/OPTIONAL) — Real-Time via Gmail Push Notifications.

⚠️ Ye phase SIRF tab karna jab polling-based version (main.py, Phase 1-11)
fully test ho chuka ho. Ye guide.md ka explicit rule hai.

⚠️ Ye file ek SKELETON/STARTING POINT hai, copy-paste-and-run production
code nahi. Isay chalane ke liye extra cloud setup chahiye hoga jo iss
project ke sandbox mein verify nahi ho sakta:
   1. Google Cloud Pub/Sub topic banana
   2. Us topic ko Gmail se watch() API call ke zariye link karna
   3. Ye FastAPI app kisi publicly-reachable URL pe deploy karna
      (jaise Railway — jaisa ShopEase project mein kiya tha)
   4. Pub/Sub push subscription ko us deployed URL se point karna

Polling (main.py) ke bajaye ye approach real-time hai: Gmail naye email
pe turant Pub/Sub ko push karta hai, Pub/Sub is FastAPI endpoint ko hit
karta hai, aur hum turant agent trigger kar dete hain — POLL_INTERVAL_SECONDS
ka wait nahi karna padta.

Extra dependencies chahiye (requirements.txt mein NAHI hain, taake core
polling agent halka rahe): dekho advanced/requirements-advanced.txt
"""

import base64
import json

from fastapi import FastAPI, Request

from src.step1_auth import get_gmail_service
from src.step2_fetcher import fetch_unread_emails
from src.step3_state import init_db, is_processed, mark_processed
from src.step4_agent import run_agent
from src.step5_guardrails import passes_guardrails
from src.step6_sender import send_reply, mark_needs_human
from src.step7_logger import setup_logger

# main.py ka process_email() reuse kar rahe hain taake logic duplicate
# na ho (Golden Rule: doosre step ka logic copy-paste nahi karna)
from main import process_email

logger = setup_logger()
app = FastAPI(title="Email Reply Agent - Push Webhook")


@app.on_event("startup")
def startup():
    init_db()
    logger.info("Webhook app started (Phase 12 - push notification mode).")


@app.post("/gmail-webhook")
async def gmail_webhook(request: Request):
    """
    Google Cloud Pub/Sub is endpoint ko POST request bhejta hai jab bhi
    naya email aata hai (Gmail watch() API se subscribe kiya hua).

    Pub/Sub ka payload base64-encoded JSON hota hai jismein sirf itna
    hota hai ke "kuch naya hua hai" — asli email data hume khud
    fetch_unread_emails() se hi lana padta hai (Gmail security design).
    """
    envelope = await request.json()

    if "message" not in envelope:
        return {"status": "ignored", "reason": "no message in envelope"}

    # Pub/Sub message data base64 encoded hota hai
    try:
        raw_data = base64.b64decode(envelope["message"]["data"])
        notification = json.loads(raw_data)
        logger.info(f"Push notification received: {notification}")
    except Exception as e:
        logger.error(f"Failed to decode push notification: {e}")
        return {"status": "error", "reason": str(e)}

    # Notification sirf trigger hai -- asli kaam wahi hai jo main.py
    # polling cycle mein karta hai: fetch + process
    service = get_gmail_service()
    emails = fetch_unread_emails()
    for email in emails:
        process_email(service, email)

    return {"status": "processed", "emails_checked": len(emails)}


@app.get("/health")
def health():
    """Deployment platform (Railway) ke liye health check endpoint."""
    return {"status": "ok"}


# Local test ke liye: uvicorn advanced.webhook_app:app --reload --port 8000
# Production (Railway) ke liye: Procfile mein 'web: uvicorn advanced.webhook_app:app --host 0.0.0.0 --port $PORT'
