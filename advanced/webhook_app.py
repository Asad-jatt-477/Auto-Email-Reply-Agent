"""
advanced/webhook_app.py
-----------------------
Real-time mode: Gmail -> Pub/Sub push -> this endpoint -> agent.

Design (follows how Pub/Sub push behaves in production):
* Every push is AUTHENTICATED before anything happens: Google-signed OIDC
  token (recommended) or a shared secret in the push URL. With neither
  configured the endpoint rejects everything (secure by default).
* The endpoint only signals a background worker and returns 204 at once.
  Processing inside the request would exceed the push ack deadline and
  make Pub/Sub redeliver the same notification again and again.
* One worker thread runs cycles one at a time; a burst of 20 pushes
  becomes one or two cycles (notifications are coalesced).
* Gmail can delay or drop notifications, so the worker also runs a
  fallback cycle every WEBHOOK_FALLBACK_POLL_SECONDS.
* The Gmail watch expires after 7 days; the worker renews it daily.

Run locally:  uvicorn advanced.webhook_app:app --port 8000
Railway:      web: uvicorn advanced.webhook_app:app --host 0.0.0.0 --port $PORT
"""

import base64
import hmac
import json
import os
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from src.step7_logger import setup_logger

logger = setup_logger()

PUSH_AUDIENCE = os.getenv("PUBSUB_PUSH_AUDIENCE")                    # OIDC audience set on the subscription
PUSH_SERVICE_ACCOUNT = os.getenv("PUBSUB_PUSH_SERVICE_ACCOUNT")      # service account used by the subscription
VERIFICATION_TOKEN = os.getenv("PUBSUB_VERIFICATION_TOKEN")          # alternative: ?token=<secret> in push URL
ALLOW_UNAUTHENTICATED = os.getenv("WEBHOOK_ALLOW_UNAUTHENTICATED", "false").lower() == "true"
FALLBACK_POLL_SECONDS = int(os.getenv("WEBHOOK_FALLBACK_POLL_SECONDS", "600"))
WATCH_RENEW_SECONDS = 24 * 3600


def verify_oidc(auth_header: str) -> bool:
    if not (PUSH_AUDIENCE and auth_header and auth_header.startswith("Bearer ")):
        return False
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    try:
        claims = id_token.verify_oauth2_token(auth_header.split(" ", 1)[1], google_requests.Request(),
                                              audience=PUSH_AUDIENCE)
    except Exception as exc:
        logger.warning(f"Webhook: invalid OIDC token: {exc}")
        return False
    if PUSH_SERVICE_ACCOUNT and claims.get("email") != PUSH_SERVICE_ACCOUNT:
        logger.warning("Webhook: OIDC token from an unexpected service account")
        return False
    return bool(claims.get("email_verified", True))


def is_authorized(request: Request, oidc_verifier=verify_oidc) -> bool:
    if PUSH_AUDIENCE:
        return oidc_verifier(request.headers.get("authorization", ""))
    if VERIFICATION_TOKEN:
        return hmac.compare_digest(request.query_params.get("token", ""), VERIFICATION_TOKEN)
    return ALLOW_UNAUTHENTICATED


class AgentWorker:
    """Runs agent cycles in one background thread, triggered by pushes."""

    def __init__(self, service_factory, cycle_fn, watch_fn=None):
        self._service_factory = service_factory
        self._cycle_fn = cycle_fn
        self._watch_fn = watch_fn
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._last_watch = 0.0
        self.cycles_run = 0
        self.last_error = None

    def trigger(self):
        self._wake.set()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="agent-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout=30):
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)

    def _maybe_renew_watch(self, service):
        if self._watch_fn and time.time() - self._last_watch > WATCH_RENEW_SECONDS:
            try:
                self._watch_fn(service)
                self._last_watch = time.time()
                logger.info("Gmail watch renewed")
            except Exception as exc:
                logger.error(f"Gmail watch renewal failed (fallback polling continues): {exc}")

    def _loop(self):
        service = None
        while not self._stop.is_set():
            self._wake.wait(timeout=FALLBACK_POLL_SECONDS)
            if self._stop.is_set():
                break
            self._wake.clear()   # pushes arriving during the cycle trigger exactly one more cycle
            try:
                service = service or self._service_factory()
                self._maybe_renew_watch(service)
                summary = self._cycle_fn(service, stop_event=self._stop)
                self.cycles_run += 1
                self.last_error = None
                if summary.get("fetched"):
                    logger.info(f"Webhook cycle done | {summary}")
            except Exception as exc:
                self.last_error = str(exc)
                logger.error(f"Webhook worker cycle error: {exc}")


def create_app(service_factory=None, cycle_fn=None, watch_fn=None, oidc_verifier=verify_oidc,
               start_worker=True) -> FastAPI:
    if service_factory is None:
        from src.step1_auth import get_gmail_service as service_factory
    if cycle_fn is None:
        from src.orchestrator import run_cycle as cycle_fn
    if watch_fn is None and os.getenv("GMAIL_PUBSUB_TOPIC"):
        from src.step8_watch import start_watch as watch_fn

    worker = AgentWorker(service_factory, cycle_fn, watch_fn)

    @asynccontextmanager
    async def lifespan(_app):
        from src.step3_state import init_db

        init_db()
        if not (PUSH_AUDIENCE or VERIFICATION_TOKEN or ALLOW_UNAUTHENTICATED):
            logger.error("Webhook has no authentication configured - every push will be rejected (401).")
        if start_worker:
            worker.start()
            worker.trigger()   # process anything that arrived while we were down
        yield
        worker.stop()

    app = FastAPI(title="Email Reply Agent - Push Webhook", lifespan=lifespan)
    app.state.worker = worker

    @app.post("/gmail-webhook")
    async def gmail_webhook(request: Request):
        if not is_authorized(request, oidc_verifier):
            return Response(status_code=401)
        try:
            envelope = await request.json()
            data = json.loads(base64.b64decode(envelope["message"]["data"]))
            logger.info(f"Push received | emailAddress={data.get('emailAddress')} historyId={data.get('historyId')}")
        except Exception as exc:
            # Malformed payload: acknowledge (2xx) so Pub/Sub stops redelivering it.
            logger.warning(f"Webhook: ignoring malformed push: {exc}")
            return Response(status_code=204)
        worker.trigger()
        return Response(status_code=204)

    @app.get("/health")
    def health():
        status = "degraded" if worker.last_error else "ok"
        return {"status": status, "cycles_run": worker.cycles_run, "last_error": worker.last_error}

    return app


app = create_app()