"""
test_fetcher.py
-----------------
Phase 11 — Automated tests for step2_fetcher.py's body/header extraction
logic. Ye tests kisi real Gmail connection ke bagair chalte hain (pure
logic test, fake Gmail-API-shaped data use karte hain).

Run: python -m tests.test_fetcher   (project root se)
"""

import sys
import os
import base64

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.step2_fetcher import _extract_body, _get_header


def _encode(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("utf-8")


def test_header_lookup_case_insensitive():
    headers = [
        {"name": "From", "value": "sender@example.com"},
        {"name": "Subject", "value": "Test Subject"},
    ]
    assert _get_header(headers, "from") == "sender@example.com"
    assert _get_header(headers, "SUBJECT") == "Test Subject"
    print("PASS: header lookup is case-insensitive")


def test_header_lookup_missing_returns_empty():
    assert _get_header([], "From") == ""
    print("PASS: missing header returns empty string")


def test_simple_body_extraction():
    text = "Hello, this is a simple email body."
    payload = {"body": {"data": _encode(text)}}
    assert _extract_body(payload) == text
    print("PASS: simple (non-multipart) body extraction works")


def test_multipart_prefers_plain_text():
    plain = "This is the plain text part."
    html = "<p>This is the html part.</p>"
    payload = {
        "parts": [
            {"mimeType": "text/html", "body": {"data": _encode(html)}},
            {"mimeType": "text/plain", "body": {"data": _encode(plain)}},
        ]
    }
    assert _extract_body(payload) == plain
    print("PASS: multipart extraction prefers text/plain over text/html")


def test_nested_multipart():
    plain = "Nested plain text."
    payload = {
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [{"mimeType": "text/plain", "body": {"data": _encode(plain)}}],
            }
        ]
    }
    assert _extract_body(payload) == plain
    print("PASS: nested multipart parts handled recursively")


def test_empty_payload_returns_empty_string():
    assert _extract_body({}) == ""
    print("PASS: empty payload returns empty string, no crash")


if __name__ == "__main__":
    test_header_lookup_case_insensitive()
    test_header_lookup_missing_returns_empty()
    test_simple_body_extraction()
    test_multipart_prefers_plain_text()
    test_nested_multipart()
    test_empty_payload_returns_empty_string()
    print("\nALL AUTOMATED FETCHER TESTS PASSED (6/6)")
