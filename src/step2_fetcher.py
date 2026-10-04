"""
step2_fetcher.py
----------------
Fetches unread inbox messages that the agent has not processed yet and
normalises each into a plain dict. No decisions are made here.

Body extraction:
* prefers text/plain
* falls back to text/html converted to text (many real senders - web
  forms, Outlook, marketing tools - send HTML only)
* decodes using the part's declared charset, not a blind utf-8 guess
"""

import base64
import re
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser

from src import config


def _get_header(headers, name):
    for header in headers or []:
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _part_charset(part) -> str:
    content_type = _get_header(part.get("headers", []), "Content-Type")
    match = re.search(r'charset="?([\w\-.:]+)"?', content_type, re.IGNORECASE)
    return match.group(1) if match else "utf-8"


def _decode_part(part) -> str:
    data = (part.get("body") or {}).get("data")
    if not data:
        return ""
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    charset = _part_charset(part)
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:  # unknown charset name in a malformed mail
        return raw.decode("utf-8", errors="replace")


class _HTMLToText(HTMLParser):
    _BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "blockquote"}
    _SKIP_TAGS = {"script", "style", "head", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._chunks = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._chunks.append(data)

    def text(self) -> str:
        text = unescape("".join(self._chunks)).replace("\xa0", " ")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def html_to_text(html: str) -> str:
    parser = _HTMLToText()
    parser.feed(html)
    parser.close()
    return parser.text()


def _find_part(payload, mime_type):
    """Depth-first search for the first non-attachment part of mime_type."""
    if payload.get("mimeType") == mime_type and not payload.get("filename"):
        if (payload.get("body") or {}).get("data"):
            return payload
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return None


def _extract_body(payload) -> str:
    if not payload:
        return ""
    plain = _find_part(payload, "text/plain")
    if plain:
        return _decode_part(plain).strip()
    html = _find_part(payload, "text/html")
    if html:
        return html_to_text(_decode_part(html))
    # Single-part message whose mimeType is missing / unusual.
    if (payload.get("body") or {}).get("data") and not payload.get("parts"):
        return _decode_part(payload).strip()
    return ""


def sender_address(from_header: str) -> str:
    """'Ali Khan <Ali@Example.com>' -> 'ali@example.com'."""
    return parseaddr(from_header or "")[1].strip().lower()


def parse_message(msg: dict) -> dict:
    payload = msg.get("payload", {}) or {}
    headers = payload.get("headers", [])
    from_header = _get_header(headers, "From")
    return {
        "id": msg["id"],
        "thread_id": msg.get("threadId", ""),
        "from": from_header,
        "from_address": sender_address(from_header),
        "reply_to": _get_header(headers, "Reply-To"),
        "subject": _get_header(headers, "Subject"),
        "body": _extract_body(payload),
        "snippet": msg.get("snippet", ""),
        "message_id_header": _get_header(headers, "Message-ID"),
        "references": _get_header(headers, "References"),
        "auto_submitted": _get_header(headers, "Auto-Submitted"),
        "precedence": _get_header(headers, "Precedence"),
        "list_id": _get_header(headers, "List-Id"),
        "list_unsubscribe": _get_header(headers, "List-Unsubscribe"),
        "label_ids": msg.get("labelIds", []),
        "internal_date": int(msg.get("internalDate", 0) or 0),
    }


def list_unprocessed_ids(service, query=None, limit=None) -> list:
    """Message ids matching the query, following pagination up to limit."""
    query = query or config.GMAIL_QUERY
    limit = limit or config.MAX_EMAILS_PER_CYCLE
    ids, page_token = [], None
    while len(ids) < limit:
        resp = service.users().messages().list(
            userId="me", q=query, maxResults=min(100, limit - len(ids)), pageToken=page_token
        ).execute()
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return ids[:limit]


def fetch_message(service, message_id: str) -> dict:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()
    return parse_message(msg)


def fetch_unread_emails(service=None) -> list:
    if service is None:
        from src.step1_auth import get_gmail_service

        service = get_gmail_service()
    emails = [fetch_message(service, mid) for mid in list_unprocessed_ids(service)]
    # Oldest first: within equal urgency, first come first served.
    return sorted(emails, key=lambda e: e["internal_date"])


if __name__ == "__main__":
    for e in fetch_unread_emails():
        print("-" * 50)
        print(f"From: {e['from']}\nSubject: {e['subject']}\nLabels: {e['label_ids']}")
        print("Body preview:", e["body"][:120].replace("\n", " "))
