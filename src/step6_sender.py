"""
step6_sender.py
---------------
All Gmail write operations: send an in-thread reply, create a draft for
human review, apply labels, mark read, move to Spam.

* Replies are built with email.message.EmailMessage (SMTP policy) so Urdu /
  non-ASCII subjects and bodies are encoded correctly.
* Replies go to Reply-To when the sender set one (web forms, help desks).
* Replies carry 'Auto-Submitted: auto-replied' (RFC 3834) so other
  auto-responders do not answer back and start a mail loop.
* Label ids are cached per service, so each label is looked up once.
"""

import base64
import re
from email.message import EmailMessage
from email.policy import SMTP

from src import config

_label_cache: dict = {}


def _reply_subject(subject: str) -> str:
    subject = (subject or "").strip()
    return subject if subject.lower().startswith("re:") else f"Re: {subject}".strip()


def _build_reply_message(email: dict, draft_text: str, auto_generated: bool = True) -> EmailMessage:
    message = EmailMessage(policy=SMTP)
    message["To"] = email.get("reply_to") or email.get("from", "")
    message["Subject"] = _reply_subject(email.get("subject", ""))
    original_id = (email.get("message_id_header") or "").strip()
    if original_id:
        message["In-Reply-To"] = original_id
        refs = (email.get("references") or "").strip()
        message["References"] = f"{refs} {original_id}".strip() if refs else original_id
    if auto_generated:
        message["Auto-Submitted"] = "auto-replied"
    text = draft_text or ""
    # Non-ASCII (Urdu, accents): quoted-printable keeps the wire format 7-bit safe.
    cte = "7bit" if text.isascii() else "quoted-printable"
    message.set_content(text, charset="utf-8", cte=cte)
    return message


def _raw(message: EmailMessage) -> str:
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


def _label_key(name: str) -> str:
    """Gmail treats names that differ only in case or space/hyphen/underscore as
    the same label ('Category-General Query' vs 'Category-General-Query')."""
    return re.sub(r"[\s_\-]+", "-", name.strip().lower())


def _find_existing(labels, label_name):
    wanted = _label_key(label_name)
    for label in labels:
        if _label_key(label["name"]) == wanted:
            return label["id"]
    return None


def get_label_id(service, label_name: str) -> str:
    """Id of a user label, reusing an equivalent existing label instead of
    creating a duplicate (older versions named labels slightly differently)."""
    key = (id(service), label_name)
    if key in _label_cache:
        return _label_cache[key]
    labels = service.users().labels().list(userId="me").execute().get("labels", [])
    for label in labels:
        _label_cache[(id(service), label["name"])] = label["id"]
    existing = _label_cache.get(key) or _find_existing(labels, label_name)
    if existing:
        _label_cache[key] = existing
        return existing
    try:
        created = service.users().labels().create(
            userId="me",
            body={"name": label_name, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        ).execute()
        _label_cache[key] = created["id"]
    except Exception:
        # Created meanwhile, or Gmail reports a conflicting name: look it up again.
        labels = service.users().labels().list(userId="me").execute().get("labels", [])
        existing = _find_existing(labels, label_name)
        if not existing:
            raise
        _label_cache[key] = existing
    return _label_cache[key]


def modify_labels(service, gmail_id: str, add_names=(), remove_ids=()):
    body = {}
    add_ids = [get_label_id(service, n) for n in add_names]
    if add_ids:
        body["addLabelIds"] = add_ids
    if remove_ids:
        body["removeLabelIds"] = list(remove_ids)
    if body:
        service.users().messages().modify(userId="me", id=gmail_id, body=body).execute()


def send_reply(service, email: dict, draft_text: str, auto_generated: bool = True) -> dict:
    """Sends the reply in the original thread. ONLY sends - labelling is separate."""
    message = _build_reply_message(email, draft_text, auto_generated)
    return service.users().messages().send(
        userId="me", body={"raw": _raw(message), "threadId": email.get("thread_id")}
    ).execute()


def create_reply_draft(service, email: dict, draft_text: str) -> dict:
    """Saves the AI draft in the thread so a human can review/edit/send it in Gmail."""
    message = _build_reply_message(email, draft_text, auto_generated=False)
    return service.users().drafts().create(
        userId="me", body={"message": {"raw": _raw(message), "threadId": email.get("thread_id")}}
    ).execute()


def _label_display_name(value: str) -> str:
    return value.replace("_", " ").strip().title().replace(" ", "-")


def triage_label_names(category=None, priority=None) -> list:
    names = []
    if category:
        names.append(f"{config.CATEGORY_LABEL_PREFIX}-{_label_display_name(category)}")
    if priority:
        names.append(f"{config.PRIORITY_LABEL_PREFIX}-{_label_display_name(priority)}")
    return names


def move_to_spam(service, email: dict):
    service.users().messages().modify(
        userId="me", id=email["id"],
        body={"addLabelIds": [config.SPAM_GMAIL_LABEL], "removeLabelIds": ["INBOX", "UNREAD"]},
    ).execute()