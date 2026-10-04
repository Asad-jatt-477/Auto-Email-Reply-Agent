"""Console logging must survive emoji/Urdu on a cp1252 console; only recent mail is fetched."""

import io
import logging

from src import config
from src.step7_logger import SafeStreamHandler


def test_cp1252_console_does_not_break_on_emoji_or_urdu():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    handler = SafeStreamHandler(stream)
    errors = []
    handler.handleError = lambda record: errors.append(record)
    logger = logging.getLogger("test_cp1252")
    logger.handlers = [handler]
    logger.propagate = False
    logger.warning("subject='Big news \U0001f389 Lock\u2011in \u0645\u06cc\u0679\u0646\u06af'")
    stream.flush()
    out = raw.getvalue().decode("cp1252")
    assert errors == []
    assert "subject='Big news ? Lock?in ?????'" in out


def test_utf8_console_keeps_characters():
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="utf-8")
    handler = SafeStreamHandler(stream)
    logger = logging.getLogger("test_utf8")
    logger.handlers = [handler]
    logger.propagate = False
    logger.warning("Urdu \u0645\u06cc\u0679\u0646\u06af \U0001f389")
    stream.flush()
    assert "\u0645\u06cc\u0679\u0646\u06af \U0001f389" in raw.getvalue().decode("utf-8")


def test_query_limits_mail_age_by_default():
    assert config.build_gmail_query(2) == "is:unread in:inbox -label:ai-processed newer_than:2d"
    assert "newer_than:" in config.GMAIL_QUERY


def test_age_limit_can_be_disabled():
    assert "newer_than" not in config.build_gmail_query(0)