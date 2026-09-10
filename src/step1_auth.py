"""
step1_auth.py
-------------
Phase 3 — Gmail OAuth Authentication.

Kaam:
- credentials.json (Google Cloud se downloaded OAuth client) use karke
  ek baar browser open hota hai, tum apne Gmail se login karte ho aur
  permission dete ho.
- Us permission ka "token" token.json file mein save ho jata hai, taake
  agli baar dobara browser login na karna paray (jab tak token valid hai).
- get_gmail_service() function ye poora kaam handle karta hai aur ek
  authenticated Gmail "service" object return karta hai — yehi object
  Phase 4 (fetcher) aur Phase 8 (sender) import karke use karenge.

Golden Rule follow: koi constant yahan hardcode nahi kiya — CREDENTIALS_FILE,
TOKEN_FILE, GMAIL_SCOPES sab src/config.py se aa rahe hain.
"""

import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.config import GMAIL_SCOPES, CREDENTIALS_FILE, TOKEN_FILE


def get_gmail_service():
    """
    Authenticated Gmail API service object return karta hai.

    Logic:
    1. Agar token.json pehle se maujood hai aur valid hai -> use karo,
       browser dobara nahi khulega.
    2. Agar token expire ho gaya hai lekin refresh_token maujood hai ->
       chupchap refresh kar do, user ko kuch karne ki zaroorat nahi.
    3. Agar koi token hi nahi hai (pehli baar chal raha hai) -> browser
       kholo, login karwao, naya token.json bana do.
    """
    creds = None

    # Step 1: purana token file check karo
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)

    # Step 2/3: agar token nahi hai ya invalid hai
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CREDENTIALS_FILE):
                raise FileNotFoundError(
                    f"'{CREDENTIALS_FILE}' nahi mili project root mein.\n"
                    "Google Cloud Console -> Google Auth Platform -> Clients "
                    "se apne OAuth client (Desktop app) ka JSON download "
                    f"karo aur usay exactly '{CREDENTIALS_FILE}' naam se "
                    "project ke root folder mein rakho."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                CREDENTIALS_FILE, GMAIL_SCOPES
            )
            # Ye line browser open karti hai login ke liye
            creds = flow.run_local_server(port=0)

        # naya/refreshed token save kar do taake agli baar reuse ho
        with open(TOKEN_FILE, "w") as token_file:
            token_file.write(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    return service


if __name__ == "__main__":
    # Ye block sirf tab chalta hai jab ye file DIRECTLY run ki jaye
    # (python -m src.step1_auth), taake hum isko standalone test kar sakein.
    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        print("Authentication successful")
        print(f"Logged in as: {profile.get('emailAddress')}")
    except FileNotFoundError as e:
        print(f"Setup error: {e}")
    except Exception as e:
        print(f"Authentication failed: {e}")
