"""Webhook: authentication, fast ack, coalescing, malformed pushes, watch renewal."""

import base64
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import advanced.webhook_app as webhook
from advanced.webhook_app import AgentWorker, create_app


def push_body():
    data = base64.b64encode(json.dumps({"emailAddress": "me@x.com", "historyId": 5}).encode()).decode()
    return {"message": {"data": data, "messageId": "1"}, "subscription": "s"}


@pytest.fixture
def no_auth_config(monkeypatch):
    monkeypatch.setattr(webhook, "PUSH_AUDIENCE", None)
    monkeypatch.setattr(webhook, "VERIFICATION_TOKEN", None)
    monkeypatch.setattr(webhook, "ALLOW_UNAUTHENTICATED", False)


def app_with(**kw):
    return create_app(service_factory=lambda: object(), cycle_fn=lambda s, stop_event=None: {"fetched": 0},
                      start_worker=False, **kw)


def test_rejects_everything_when_no_auth_configured(no_auth_config):
    with TestClient(app_with()) as client:
        assert client.post("/gmail-webhook", json=push_body()).status_code == 401


def test_shared_token(no_auth_config, monkeypatch):
    monkeypatch.setattr(webhook, "VERIFICATION_TOKEN", "s3cret")
    app = app_with()
    triggered = []
    app.state.worker.trigger = lambda: triggered.append(1)
    with TestClient(app) as client:
        assert client.post("/gmail-webhook?token=wrong", json=push_body()).status_code == 401
        assert client.post("/gmail-webhook?token=s3cret", json=push_body()).status_code == 204
    assert triggered == [1]


def test_oidc_path_uses_verifier(no_auth_config, monkeypatch):
    monkeypatch.setattr(webhook, "PUSH_AUDIENCE", "https://agent.example/gmail-webhook")
    seen = []

    def verifier(header):
        seen.append(header)
        return header == "Bearer good"

    with TestClient(app_with(oidc_verifier=verifier)) as client:
        assert client.post("/gmail-webhook", json=push_body(), headers={"Authorization": "Bearer bad"}).status_code == 401
        assert client.post("/gmail-webhook", json=push_body(), headers={"Authorization": "Bearer good"}).status_code == 204
    assert seen == ["Bearer bad", "Bearer good"]


def test_real_oidc_verifier_rejects_garbage(monkeypatch):
    monkeypatch.setattr(webhook, "PUSH_AUDIENCE", "aud")
    assert webhook.verify_oidc("Bearer not-a-jwt") is False
    assert webhook.verify_oidc("") is False


def test_malformed_push_is_acked_not_processed(no_auth_config, monkeypatch):
    monkeypatch.setattr(webhook, "VERIFICATION_TOKEN", "t")
    app = app_with()
    triggered = []
    app.state.worker.trigger = lambda: triggered.append(1)
    with TestClient(app) as client:
        assert client.post("/gmail-webhook?token=t", json={"nope": 1}).status_code == 204
    assert triggered == []


def test_health(no_auth_config):
    with TestClient(app_with()) as client:
        assert client.get("/health").json()["status"] == "ok"


def test_health_reports_degraded_after_errors():
    def failing_factory():
        raise RuntimeError("no token")

    worker = AgentWorker(failing_factory, lambda s, stop_event=None: {})
    worker.start()
    worker.trigger()
    time.sleep(0.1)
    worker.stop()
    assert worker.last_error == "no token" and worker.cycles_run == 0


def test_worker_coalesces_bursts_and_runs_one_cycle_at_a_time():
    running, max_parallel, cycles = [0], [0], []
    lock = threading.Lock()

    def slow_cycle(service, stop_event=None):
        with lock:
            running[0] += 1
            max_parallel[0] = max(max_parallel[0], running[0])
        time.sleep(0.2)
        with lock:
            running[0] -= 1
        cycles.append(1)
        return {"fetched": 0}

    worker = AgentWorker(lambda: object(), slow_cycle)
    worker.start()
    for _ in range(20):          # burst of 20 notifications
        worker.trigger()
        time.sleep(0.01)
    time.sleep(0.8)
    worker.stop()
    assert max_parallel[0] == 1
    assert 1 <= len(cycles) <= 3


def test_watch_is_renewed_and_failures_do_not_kill_worker():
    renewals = []

    def watch(service):
        renewals.append(1)
        if len(renewals) == 1:
            raise RuntimeError("pubsub permission denied")

    worker = AgentWorker(lambda: object(), lambda s, stop_event=None: {"fetched": 0}, watch_fn=watch)
    worker.start()
    worker.trigger()
    time.sleep(0.1)
    worker.trigger()
    time.sleep(0.1)
    worker.stop()
    assert len(renewals) == 2 and worker.cycles_run == 2