"""
step1_auth.py
-------------
Gmail OAuth. Returns an authenticated Gmail API service.

Credential sources, in order:
1. GMAIL_TOKEN_JSON_B64 env var  -> cloud / headless (Railway). The
   token.json content holds client_id, client_secret and refresh_token,
   so credentials.json is NOT needed on the server.
2. token.json on disk             -> normal local runs.
3. Browser login (credentials.json) -> first local run only, and only
   when ALLOW_INTERACTIVE_AUTH is true. On a server this would hang
   waiting for a browser that does not exist, so it fails fast instead.

Create the cloud variable once, locally:  python main.py export-token
"""

import base64
import json
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from src import config


class GmailAuthError(RuntimeError):
    """Raised when no usable Gmail credentials are available."""


def _creds_from_env():
    raw = os.getenv(config.TOKEN_JSON_B64_ENV)
    if not raw:
        return None
    try:
        info = json.loads(base64.b64decode(raw.strip()).decode("utf-8"))
    except Exception as exc:  # malformed variable -> clear message
        raise GmailAuthError(
            f"{config.TOKEN_JSON_B64_ENV} is set but is not valid base64-encoded "
            "token JSON. Regenerate it with: python main.py export-token"
        ) from exc
    return Credentials.from_authorized_user_info(info, config.GMAIL_SCOPES)


def _creds_from_file():
    if os.path.exists(config.TOKEN_FILE):
        return Credentials.from_authorized_user_file(config.TOKEN_FILE, config.GMAIL_SCOPES)
    return None


def _interactive_login():
    if not config.ALLOW_INTERACTIVE_AUTH:
        raise GmailAuthError(
            "No valid Gmail token and interactive login is disabled "
            "(ALLOW_INTERACTIVE_AUTH=false). Set GMAIL_TOKEN_JSON_B64 "
            "(see DEPLOYMENT.md)."
        )
    if not os.path.exists(config.CREDENTIALS_FILE):
        raise GmailAuthError(
            f"'{config.CREDENTIALS_FILE}' not found in the project root. Download "
            "the OAuth client (Desktop app) JSON from Google Cloud Console."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(config.CREDENTIALS_FILE, config.GMAIL_SCOPES)
    return flow.run_local_server(port=0)


def get_credentials() -> Credentials:
    from_env = os.getenv(config.TOKEN_JSON_B64_ENV) is not None
    creds = _creds_from_env() if from_env else _creds_from_file()

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            if from_env:
                raise GmailAuthError(
                    "Gmail token refresh failed. The refresh token was revoked or "
                    "expired (OAuth apps in 'Testing' status expire refresh tokens "
                    "after 7 days). Log in locally again and re-export the token."
                ) from exc
            creds = None
        if creds:
            if not from_env:
                _save_token(creds)
            return creds

    if from_env:
        raise GmailAuthError(
            f"{config.TOKEN_JSON_B64_ENV} has no usable refresh token. Re-export it."
        )

    creds = _interactive_login()
    _save_token(creds)
    return creds


def _save_token(creds: Credentials):
    with open(config.TOKEN_FILE, "w", encoding="utf-8") as fh:
        fh.write(creds.to_json())


def get_gmail_service():
    from googleapiclient.discovery import build

    # cache_discovery=False avoids a noisy file-cache warning on servers.
    return build("gmail", "v1", credentials=get_credentials(), cache_discovery=False)


def export_token_b64() -> str:
    """Base64 of the local token.json, for the GMAIL_TOKEN_JSON_B64 variable."""
    if not os.path.exists(config.TOKEN_FILE):
        raise GmailAuthError(f"'{config.TOKEN_FILE}' not found. Run the agent locally once to log in.")
    with open(config.TOKEN_FILE, "rb") as fh:
        data = fh.read()
    info = json.loads(data)
    if not info.get("refresh_token"):
        raise GmailAuthError("token.json has no refresh_token; delete it and log in again.")
    return base64.b64encode(data).decode("ascii")


if __name__ == "__main__":
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    print(f"Authentication successful. Logged in as: {profile.get('emailAddress')}")