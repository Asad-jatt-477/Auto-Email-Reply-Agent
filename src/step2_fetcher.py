"""
step2_fetcher.py
-----------------
Phase 4 — Email Fetcher (Poll Inbox).

Kaam:
- Phase 3 (get_gmail_service) se authenticated service lena
- Unread inbox emails ki list Gmail se lana
- Har email ka pura data (headers + body + label_ids) nikal ke
  ek list of dicts return karna

Ye file sirf DATA nikalti hai — koi decision (reply/skip) yahan nahi hoti,
wo Phase 6 (agent) aur Phase 7 (guardrails) ka kaam hai.
"""

import base64

from src.step1_auth import get_gmail_service
from src.config import GMAIL_QUERY


def _get_header(headers, name):
    """Gmail headers list mein se ek specific header ki value dhoondo."""
    for header in headers:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _extract_body(payload):
    """
    Email body nikalna. Gmail API body ko do tareeqon se deta hai:
    1. Simple email -> payload['body']['data'] mein seedha base64 text
    2. Multipart email (HTML+text ya attachments) -> payload['parts'][]
       mein alag alag pieces, hum unmein se 'text/plain' part dhoondte hain

    Recursive isliye hai kyunki multipart emails ke andar bhi nested
    multipart parts ho sakte hain (jaise multipart/alternative ke andar
    multipart/related).
    """
    if payload.get("body", {}).get("data"):
        data = payload["body"]["data"]
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    parts = payload.get("parts", [])

    # Pehle plain text part dhoondo (priority)
    for part in parts:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            data = part["body"]["data"]
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")

    # Nahi mila to nested parts ke andar recursively dhoondo
    for part in parts:
        if part.get("parts"):
            nested = _extract_body(part)
            if nested:
                return nested

    return ""


def fetch_unread_emails():
    """
    Unread inbox emails fetch karta hai aur list of dicts return karta hai:

    {
      "id": "...", "thread_id": "...", "from": "...", "subject": "...",
      "body": "...", "message_id_header": "...", "references": "...",
      "label_ids": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"]
    }
    """
    service = get_gmail_service()

    results = service.users().messages().list(
        userId="me", q=GMAIL_QUERY , maxResults=40
    ).execute()
    message_stubs = results.get("messages", [])

    emails = []
    for stub in message_stubs:
        msg = service.users().messages().get(
            userId="me", id=stub["id"], format="full"
        ).execute()

        headers = msg.get("payload", {}).get("headers", [])
        body = _extract_body(msg.get("payload", {}))

        emails.append({
            "id": msg["id"],
            "thread_id": msg["threadId"],
            "from": _get_header(headers, "From"),
            "subject": _get_header(headers, "Subject"),
            "body": body,
            "message_id_header": _get_header(headers, "Message-ID"),
            "references": _get_header(headers, "References"),
            "label_ids": msg.get("labelIds", []),
        })

    return emails


if __name__ == "__main__":
    # Standalone test: python -m src.step2_fetcher
    found_emails = fetch_unread_emails()
    print(f"Total unread emails found: {len(found_emails)}\n")

    for e in found_emails:
        print("-" * 50)
        print(f"From: {e['from']}")
        print(f"Subject: {e['subject']}")
        print(f"Labels: {e['label_ids']}")
        preview = e["body"][:100].replace("\n", " ")
        print(f"Body preview: {preview}")
