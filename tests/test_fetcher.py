"""Fetcher: body extraction (plain / HTML-only / charset / attachments), headers, pagination."""

import base64

from src.step2_fetcher import (_extract_body, _get_header, fetch_unread_emails, html_to_text,
                               list_unprocessed_ids, sender_address)


def enc(text, charset="utf-8"):
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def part(mime, text, charset="utf-8", filename=""):
    return {"mimeType": mime, "filename": filename,
            "headers": [{"name": "Content-Type", "value": f"{mime}; charset={charset}"}],
            "body": {"data": enc(text, charset)}}


def test_header_lookup_case_insensitive_and_missing():
    headers = [{"name": "From", "value": "a@b.com"}]
    assert _get_header(headers, "FROM") == "a@b.com"
    assert _get_header(headers, "Subject") == ""


def test_simple_plain_body():
    assert _extract_body(part("text/plain", "Hello there")) == "Hello there"


def test_multipart_prefers_plain_over_html():
    payload = {"mimeType": "multipart/alternative",
               "parts": [part("text/html", "<p>html</p>"), part("text/plain", "plain")]}
    assert _extract_body(payload) == "plain"


# -- Bug 3: HTML-only mail ---------------------------------------------------
def test_html_only_body_is_converted_to_text():
    html = ("<html><head><style>p{color:red}</style><title>x</title></head><body>"
            "<p>Hello,</p><p>Please send your price list for 50&nbsp;units.</p>"
            "<script>alert(1)</script><br>Thanks &amp; regards</body></html>")
    payload = {"mimeType": "multipart/alternative", "parts": [part("text/html", html)]}
    body = _extract_body(payload)
    assert "Please send your price list for 50 units." in body
    assert "Thanks & regards" in body
    assert "alert" not in body and "color" not in body


def test_nested_html_inside_mixed_with_attachment():
    payload = {"mimeType": "multipart/mixed", "parts": [
        {"mimeType": "multipart/alternative", "parts": [part("text/html", "<div>Invoice question</div>")]},
        part("text/plain", "ATTACHMENT TEXT", filename="notes.txt"),
    ]}
    assert _extract_body(payload) == "Invoice question"


def test_declared_charset_is_used():
    text = "Caf\u00e9 r\u00e9servation pour demain"
    assert _extract_body(part("text/plain", text, charset="iso-8859-1")) == text


def test_urdu_utf8_body():
    text = "\u0645\u06cc\u0679\u0646\u06af \u06a9\u0627 \u0648\u0642\u062a \u0628\u062a\u0627\u0626\u06cc\u06ba"
    assert _extract_body(part("text/plain", text)) == text


def test_empty_payload():
    assert _extract_body({}) == ""


def test_html_to_text_keeps_paragraph_breaks():
    assert html_to_text("<p>One</p><p>Two</p>") == "One\n\nTwo"


def test_sender_address_parsing():
    assert sender_address("Ali Khan <Ali.Khan@Example.COM>") == "ali.khan@example.com"


def test_pagination_and_limit(gmail):
    for i in range(7):
        gmail.add_message(f"m{i}")
    ids = list_unprocessed_ids(gmail, limit=5)
    assert len(ids) == 5
    assert len(list_unprocessed_ids(gmail, limit=50)) == 7


def test_fetch_returns_normalised_dicts(gmail):
    gmail.add_message("m1", from_="Sara <sara@client.com>", subject="Hours?",
                      body="<p>What are your hours?</p>", html=True,
                      headers={"Reply-To": "support-form@client.com", "Auto-Submitted": "no"})
    email = fetch_unread_emails(gmail)[0]
    assert email["from_address"] == "sara@client.com"
    assert email["reply_to"] == "support-form@client.com"
    assert email["body"] == "What are your hours?"
    assert email["auto_submitted"] == "no"