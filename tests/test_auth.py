"""Auth: headless token from env, clear failures, export helper (no network)."""

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from src import config, step1_auth
from src.step1_auth import GmailAuthError, export_token_b64, get_credentials


def _utcnow():
    # google-auth stores expiry as naive UTC
    return datetime.now(timezone.utc).replace(tzinfo=None)


def token_info(expired=False, refresh=True):
    expiry = _utcnow() + (timedelta(hours=-1) if expired else timedelta(hours=1))
    info = {"token": "access", "client_id": "cid.apps.googleusercontent.com", "client_secret": "s",
            "scopes": config.GMAIL_SCOPES, "expiry": expiry.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "token_uri": "https://oauth2.googleapis.com/token"}
    if refresh:
        info["refresh_token"] = "refresh"
    return info


def b64(info):
    return base64.b64encode(json.dumps(info).encode()).decode()


def test_valid_token_from_env(monkeypatch):
    monkeypatch.setenv(config.TOKEN_JSON_B64_ENV, b64(token_info()))
    assert get_credentials().valid


def test_garbage_env_token_gives_clear_error(monkeypatch):
    monkeypatch.setenv(config.TOKEN_JSON_B64_ENV, "not-base64!!")
    with pytest.raises(GmailAuthError, match="not valid"):
        get_credentials()


def test_failed_refresh_in_cloud_explains_testing_mode(monkeypatch):
    monkeypatch.setenv(config.TOKEN_JSON_B64_ENV, b64(token_info(expired=True)))

    def boom(self, request):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(step1_auth.Credentials, "refresh", boom)
    with pytest.raises(GmailAuthError, match="7 days"):
        get_credentials()


def test_successful_refresh_in_cloud(monkeypatch):
    monkeypatch.setenv(config.TOKEN_JSON_B64_ENV, b64(token_info(expired=True)))

    def refresh(self, request):
        self.token = "new"
        self.expiry = _utcnow() + timedelta(hours=1)

    monkeypatch.setattr(step1_auth.Credentials, "refresh", refresh)
    assert get_credentials().token == "new"


def test_headless_without_token_fails_fast(monkeypatch, tmp_path):
    monkeypatch.delenv(config.TOKEN_JSON_B64_ENV, raising=False)
    monkeypatch.setattr(config, "TOKEN_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(config, "ALLOW_INTERACTIVE_AUTH", False)
    with pytest.raises(GmailAuthError, match="interactive login is disabled"):
        get_credentials()


def test_export_round_trip(monkeypatch, tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps(token_info()))
    monkeypatch.setattr(config, "TOKEN_FILE", str(path))
    monkeypatch.setenv(config.TOKEN_JSON_B64_ENV, export_token_b64())
    assert get_credentials().refresh_token == "refresh"


def test_export_refuses_token_without_refresh(monkeypatch, tmp_path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps(token_info(refresh=False)))
    monkeypatch.setattr(config, "TOKEN_FILE", str(path))
    with pytest.raises(GmailAuthError, match="refresh_token"):
        export_token_b64()