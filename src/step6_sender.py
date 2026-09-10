"""
step6_sender.py
-----------------
Phase 8 — Reply Sender (Real Send via Gmail API).
Phase 13 — Spam classification action (move_to_spam).

Kaam:
- MIME reply message banana (In-Reply-To / References headers ke sath,
  taake Gmail thread mein sahi jagah pe dikhe, naya conversation na bane)
- Gmail API se real bhejna
- Send hone ke baad: UNREAD label hatana, "AI-Replied" custom label lagana
- Confirmed-spam emails ko Gmail ke asli Spam folder mein physically
  move karna (move_to_spam)

NOTE: send_reply() ko ek authenticated Gmail 'service' object chahiye
hota hai — ye Phase 9 (main.py) se pass hoga taake har email ke liye
service dobara authenticate na karna paray.
"""

import base64
from email.mime.text import MIMEText

from src.config import (
    AI_REPLIED_LABEL,
    NEEDS_HUMAN_LABEL,
    CATEGORY_LABEL_PREFIX,
    PRIORITY_LABEL_PREFIX,
    LOW_CONFIDENCE_LABEL,
    SPAM_GMAIL_LABEL,
)


def _build_reply_message(email: dict, draft_text: str) -> MIMEText:
    """
    MIME message banata hai (bina Gmail API call kiye) — isay alag
    function isliye rakha hai taake headers ki correctness bina real
    Gmail connection ke bhi test ho sake (dekho tests/test_sender.py).
    """
    reply_subject = email.get("subject", "") or ""
    if not reply_subject.lower().startswith("re:"):
        reply_subject = f"Re: {reply_subject}"

    original_message_id = email.get("message_id_header", "")

    message = MIMEText(draft_text)
    message["to"] = email.get("from", "")
    message["subject"] = reply_subject

    # In-Reply-To / References headers -- Gmail (aur har mail client)
    # inhi se decide karta hai ke ye reply kisi purani thread ka hissa
    # hai, naya conversation nahi.
    if original_message_id:
        message["In-Reply-To"] = original_message_id
        existing_references = email.get("references", "")
        message["References"] = (
            f"{existing_references} {original_message_id}".strip()
            if existing_references
            else original_message_id
        )

    return message


def _get_or_create_label(service, label_name: str) -> str:
    """
    Gmail mein diye gaye naam ka label dhoondta hai, agar exist nahi
    karta to naya bana deta hai. Label ID return karta hai (Gmail
    labels API naam se nahi, ID se kaam karti hai).
    """
    labels_response = service.users().labels().list(userId="me").execute()
    existing_labels = labels_response.get("labels", [])

    for label in existing_labels:
        if label["name"] == label_name:
            return label["id"]

    new_label = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        },
    ).execute()
    return new_label["id"]


def _label_display_name(value: str) -> str:
    """'invoice_payment' -> 'Invoice Payment' (Gmail label ke liye readable naam)."""
    return value.replace("_", " ").strip().title()


def apply_triage_labels(service, email: dict, category: str = None, priority: str = None, low_confidence: bool = False):
    """
    Phase 12 — Email par category/priority/review labels lagata hai
    (Gmail inbox mein visually triage karne ke liye, jaise
    "Category-Complaint", "Priority-High", "Needs-Review"). Har label
    lazily create hoti hai (agar pehle se na ho).

    Ye function best-effort hai — agar koi ek label fail ho jaye
    (jaise naam mein invalid character), poora processing crash nahi
    hona chahiye, isliye har add try/except mein wrapped hai.
    """
    label_names = []
    if category:
        label_names.append(f"{CATEGORY_LABEL_PREFIX}-{_label_display_name(category)}")
    if priority:
        label_names.append(f"{PRIORITY_LABEL_PREFIX}-{_label_display_name(priority)}")
    if low_confidence:
        label_names.append(LOW_CONFIDENCE_LABEL)

    label_ids = []
    for name in label_names:
        try:
            label_ids.append(_get_or_create_label(service, name))
        except Exception:
            continue  # ek label fail ho to baaki processing na ruke

    if not label_ids:
        return

    service.users().messages().modify(
        userId="me", id=email["id"], body={"addLabelIds": label_ids}
    ).execute()


def send_reply(service, email: dict, draft_text: str) -> dict:
    """
    Real reply Gmail se bhejta hai, original thread mein.

    email dict Phase 4 ke format mein hona chahiye (keys: id, from,
    subject, thread_id, message_id_header, references).

    Return: Gmail API ka send response (dict, jismein naye message ki id hai)
    """
    message = _build_reply_message(email, draft_text)
    raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")

    send_body = {"raw": raw_message, "threadId": email.get("thread_id")}

    sent_message = service.users().messages().send(
        userId="me", body=send_body
    ).execute()

    # Original email ko UNREAD se hatao, AI-Replied lagao
    ai_replied_label_id = _get_or_create_label(service, AI_REPLIED_LABEL)
    service.users().messages().modify(
        userId="me",
        id=email["id"],
        body={
            "removeLabelIds": ["UNREAD"],
            "addLabelIds": [ai_replied_label_id],
        },
    ).execute()

    return sent_message


def move_to_spam(service, email: dict):
    """
    Phase 13 — Confirmed-spam email ko Gmail ke ASLI Spam folder mein
    physically move karta hai.

    Gmail mein "Spam folder" koi alag jagah nahi, balke ek special
    SPAM label hai — jab tak koi email SPAM label carry kare AUR INBOX
    label na rakhe, Gmail usay Spam folder mein dikhata hai. Isliye:
    - SPAM label ADD karte hain
    - INBOX aur UNREAD labels REMOVE karte hain (taake wo Inbox se
      hat kar sirf Spam folder mein dikhe, aur backlog mein dobara
      unread na count ho)

    Ye function guardrails-level spam (blacklist/learned-feedback) aur
    agent(LLM)-level spam (category == "spam") — dono jagah se call hoti
    hai (dekho main.py).
    """
    service.users().messages().modify(
        userId="me",
        id=email["id"],
        body={
            "addLabelIds": [SPAM_GMAIL_LABEL],
            "removeLabelIds": ["INBOX", "UNREAD"],
        },
    ).execute()


def mark_needs_human(service, email: dict):
    """
    Phase 9 escalate_to_human decision ke liye — email ko "Needs-Human"
    label lagata hai taake insaan us par nazar rakh sake. UNREAD nahi
    hataya jata (taake pata chale ye abhi tak kisi ne dekha nahi).
    """
    needs_human_label_id = _get_or_create_label(service, NEEDS_HUMAN_LABEL)
    service.users().messages().modify(
        userId="me",
        id=email["id"],
        body={"addLabelIds": [needs_human_label_id]},
    ).execute()


if __name__ == "__main__":
    # Standalone LIVE test: python -m src.step6_sender
    # IMPORTANT: Guide ka rule follow karo — pehle apne hi doosre email
    # address pe test karo, real client ko kabhi nahi. Ye script tumhare
    # inbox ki PEHLI unread email ko ek test reply bhejega.
    from src.step1_auth import get_gmail_service
    from src.step2_fetcher import fetch_unread_emails

    service = get_gmail_service()
    emails = fetch_unread_emails()

    if not emails:
        print("Test ke liye koi unread email nahi mili. Pehle khud ko (doosre address se) ek test email bhejo, phir dobara try karo.")
    else:
        test_email = emails[0]
        print(f"Test reply bhej rahe hain: '{test_email['subject']}' ko ({test_email['from']})...")
        result = send_reply(
            service,
            test_email,
            "This is a test reply from the Email Reply AI Agent. (Phase 8 test)",
        )
        print("Reply bhej di gayi. Message ID:", result.get("id"))
        print("Gmail mein jaake confirm karo ke ye reply original thread mein hi dikh rahi hai (naya conversation nahi bana).")
