"""
step8_watch.py
--------------
Gmail push notifications (users.watch) for the real-time webhook mode.

A watch expires after 7 days, so it must be renewed; Google recommends
calling watch() once a day. The webhook app does that automatically.
Prerequisite: the Pub/Sub topic must grant 'Pub/Sub Publisher' to
gmail-api-push@system.gserviceaccount.com (see DEPLOYMENT.md).
"""

import os
from datetime import datetime, timezone

PUBSUB_TOPIC_ENV = "GMAIL_PUBSUB_TOPIC"   # projects/<project-id>/topics/<topic>


def start_watch(service, topic_name: str = None) -> dict:
    topic_name = topic_name or os.getenv(PUBSUB_TOPIC_ENV)
    if not topic_name:
        raise RuntimeError(f"{PUBSUB_TOPIC_ENV} is not set (format: projects/<id>/topics/<name>).")
    response = service.users().watch(
        userId="me",
        body={"topicName": topic_name, "labelIds": ["INBOX"], "labelFilterBehavior": "include"},
    ).execute()
    return response


def stop_watch(service):
    service.users().stop(userId="me").execute()


def expiration_text(watch_response: dict) -> str:
    ms = int(watch_response.get("expiration", 0) or 0)
    if not ms:
        return "unknown"
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
