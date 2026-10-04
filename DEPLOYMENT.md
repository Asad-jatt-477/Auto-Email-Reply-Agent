# Deployment

Run **one** of these against a mailbox, never two at the same time (two runners on the
same inbox could pick up the same message in the same second).

| Option | When |
|---|---|
| A. Windows Task Scheduler (polling) | personal use, PC is usually on |
| B. Railway worker (polling, 24/7) | production, simplest cloud setup |
| C. Railway webhook (real-time push) | replies within seconds instead of up to a minute |

Before any option, run locally once and check the decisions without changing anything:

```powershell
python main.py --once --dry-run
```

---

## A. Windows Task Scheduler

1. `pip install -r requirements.txt` inside `venv`, fill `.env`, run `python main.py` once and complete the Google login (creates `token.json`).
2. Task Scheduler -> Create Task:
   - Trigger: **At log on** (or At startup).
   - Action: Start a program -> `run_agent.bat`.
   - Settings: **Do not start a new instance** if the task is already running.
3. Logs: `logs/agent.log` (rotating) and `logs/task_scheduler_output.log`.

Limitation: the agent stops while the PC sleeps or is off.

---

## B. Railway worker (24/7 polling)

### 1. Keep the Gmail login valid on a server

A server has no browser, so the agent uses the refresh token from your local login.

- In Google Cloud Console -> Google Auth Platform -> **Audience**, set the publishing status to
  **In production**. In *Testing* status Google expires refresh tokens after **7 days** and the
  agent would stop with an auth error. (Your own unverified app shows a warning screen at login;
  that is expected for personal use.)
- Log in locally (`python main.py --once --dry-run`), then export the token:

```powershell
python main.py export-token
```

Copy the printed value. It contains your refresh token: treat it like a password.

### 2. Railway service

1. Push the repo to GitHub (`credentials.json`, `token.json`, `.env` are git-ignored).
2. Railway -> New Project -> Deploy from GitHub repo. The `Procfile` (`worker: python main.py`) starts the agent.
3. **Add a Volume** to the service, mount path `/data`. Spam feedback, rate limits and the
   decision log live there and must survive redeploys. (Even without it the agent never re-answers
   old mail, because Gmail carries the `AI-Processed` label - but the history would be lost.)
4. Variables:

| Variable | Value |
|---|---|
| `GROQ_API_KEY` | your key |
| `AGENT_EMAIL` | the Gmail address |
| `AGENT_SIGNATURE` | e.g. `Best regards,\nAhmad` |
| `GMAIL_TOKEN_JSON_B64` | output of `export-token` |
| `ALLOW_INTERACTIVE_AUTH` | `false` |
| `AGENT_DATA_DIR` | `/data` |
| `LOG_TO_FILE` | `false` (Railway keeps stdout logs) |
| `BUSINESS_CONTEXT_FILE` | path of your business facts file (commit it, or keep the default `knowledge/business_info.md` and remove that line from `.gitignore`) |

5. Deploy. On SIGTERM (redeploy) the agent finishes the current email and exits cleanly.

If the logs show `Gmail token refresh failed`, the token was revoked or expired: log in locally again
and update `GMAIL_TOKEN_JSON_B64`.

---

## C. Real-time webhook (Gmail -> Pub/Sub -> Railway)

Gmail publishes a notification to Pub/Sub on every inbox change; Pub/Sub pushes it to
`/gmail-webhook`; the app answers 204 immediately and a background worker processes the inbox.
The worker also polls every 10 minutes as a safety net (Google can delay or drop notifications)
and renews the Gmail watch every 24 hours (a watch expires after 7 days).

### 1. Pub/Sub (Google Cloud Shell)

```bash
PROJECT=<your-project-id>
APP_URL=https://<your-app>.up.railway.app/gmail-webhook

gcloud services enable pubsub.googleapis.com --project $PROJECT
gcloud pubsub topics create gmail-agent --project $PROJECT

# Gmail's system account must be allowed to publish to the topic
gcloud pubsub topics add-iam-policy-binding gmail-agent --project $PROJECT \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com --role=roles/pubsub.publisher

# Identity that signs the push requests (OIDC)
gcloud iam service-accounts create gmail-agent-push --project $PROJECT

gcloud pubsub subscriptions create gmail-agent-push --project $PROJECT --topic=gmail-agent \
  --push-endpoint=$APP_URL \
  --push-auth-service-account=gmail-agent-push@$PROJECT.iam.gserviceaccount.com \
  --push-auth-token-audience=$APP_URL \
  --ack-deadline=30
```

### 2. Railway service

- Build command: `pip install -r requirements.txt -r advanced/requirements-advanced.txt`
- Start command: `uvicorn advanced.webhook_app:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health` (reports `degraded` with the last error if Gmail/LLM calls fail)
- Variables: everything from option B, plus

| Variable | Value |
|---|---|
| `GMAIL_PUBSUB_TOPIC` | `projects/<project-id>/topics/gmail-agent` |
| `PUBSUB_PUSH_AUDIENCE` | exactly the `APP_URL` used above |
| `PUBSUB_PUSH_SERVICE_ACCOUNT` | `gmail-agent-push@<project-id>.iam.gserviceaccount.com` |

Every push is verified (Google-signed OIDC token, audience and service account). Without
authentication configured the endpoint rejects all requests. A shared secret
(`PUBSUB_VERIFICATION_TOKEN`, sent as `?token=` in the push URL) is supported as a simpler fallback.

The watch starts automatically when the app starts. To start or stop it by hand:
`python main.py watch start` / `python main.py watch stop`.

Delete the polling worker (option B) when you switch to this.

---

## Human review console

The Streamlit console reads the SQLite DB, so run it where the DB is - normally your PC
(options A, or a local run):

```powershell
pip install -r dashboard/requirements.txt
streamlit run dashboard/app.py
```

For cloud deployments, review happens in Gmail: filter by `label:needs-review` (AI draft ready
in the thread) and `label:needs-human`.