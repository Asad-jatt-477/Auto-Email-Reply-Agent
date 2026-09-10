# Email Reply AI Agent — Complete Build Guide

**Goal:** Ek real Gmail account se connected AI Agent jo naye incoming emails ko khud detect kare, samjhe ke reply chahiye ya nahi, reply draft kare, aur **real mein Gmail se bhej de** — bina RAG/vectorstore ke, pure **agentic decision-making** (tool-calling) k through.

**Stack decided (defaults — bata dena agar change karwana ho):**
- Python 3.11+, venv (Windows/PowerShell)
- Gmail API (official Google client) — OAuth2, polling-based (real-time push wala advanced option Phase 12 mein hai)
- Groq API (`llama-3.3-70b-versatile`) — LLM reasoning, tumhare ShopEase project jaisa hi
- SQLite — processed email IDs + logs (loop/duplicate reply se bachne k liye)
- Simple polling loop (`while True + time.sleep`) — cron/Task Scheduler pe deploy hoga

**Golden Rule (jaisa ShopEase mein tha):** Har phase ka code alag file mein banega (`src/step1_*.py`, `src/step2_*.py`...). Ek phase complete + tested hone ke baad hi agla phase shuru karna. Pehle wale steps ki files ko baad mein modify nahi karna — sirf upar layer add karna.

---

## Phase 0 — Prerequisites Check

- [ ] Python 3.11+ installed (`python --version`)
- [ ] Gmail account jis pe agent chalega (test ke liye ek secondary/dummy Gmail account use karna recommended hai, production account pe direct mat try karna)
- [ ] Groq API key (free) — https://console.groq.com/keys se le lena
- [ ] Google Cloud account (free) — Gmail API enable karne ke liye

**Confirm karo:** in sab cheezon ka access hai? Agar Groq key nahi hai to pehle wo bana lena.

---

## Phase 1 — Google Cloud Project + Gmail API Setup

1. https://console.cloud.google.com/ pe jao → naya project banao: `email-reply-agent`
2. Left menu → **APIs & Services → Library** → search "Gmail API" → **Enable**
3. **APIs & Services → OAuth consent screen**:
   - User Type: **External**
   - App name: `Email Reply Agent`
   - Scopes: baad mein code se add honge, yahan skip kar sakte ho
   - Test users: apna Gmail address add karo (jo agent use karega)
4. **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Application type: **Desktop app**
   - Name: `email-agent-desktop`
   - Download the JSON → rename to `credentials.json`

Is `credentials.json` file ko safe rakhna, ye kabhi commit nahi karni (`.gitignore` mein daalenge).

---

## Phase 2 — Project Setup

```powershell
mkdir email-reply-agent
cd email-reply-agent
python -m venv venv
.\venv\Scripts\Activate.ps1
```

Folder structure banao:

```powershell
mkdir src, logs, data
New-Item .env, .gitignore, requirements.txt
```

`requirements.txt`:
```
google-api-python-client
google-auth-httplib2
google-auth-oauthlib
groq
python-dotenv
```

```powershell
pip install -r requirements.txt
```

`.gitignore`:
```
venv/
credentials.json
token.json
.env
data/*.db
logs/*.log
```

`.env`:
```
GROQ_API_KEY=your_groq_key_here
AGENT_EMAIL=your_gmail_address_here
```

`credentials.json` ko project root mein rakh do (Phase 1 se download ki hui).

### Final Folder Structure (poora project isi structure mein banega)

Ye final structure hai — har phase ki file yahin apni jagah pe banegi. Isse follow karna taake koi file idhar-udhar na bane aur imports break na hon.

```
email-reply-agent/
│
├── venv/                          # virtual environment (git ignored)
│
├── credentials.json               # Google OAuth client secret (git ignored, Phase 1)
├── token.json                     # auto-generated after login (git ignored, Phase 3)
├── .env                           # API keys + config (git ignored)
├── .gitignore
├── requirements.txt
├── guide.md                       # ye guide file
├── README.md                      # Phase 14 mein banegi
│
├── src/
│   ├── __init__.py
│   ├── step1_auth.py              # Phase 3  — Gmail OAuth + get_gmail_service()
│   ├── step2_fetcher.py           # Phase 4  — unread emails fetch karna
│   ├── step3_state.py             # Phase 5  — SQLite duplicate-guard
│   ├── step4_agent.py             # Phase 6  — Groq tool-calling agent core
│   ├── step5_guardrails.py        # Phase 7  — hardcoded safety + category filters
│   ├── step6_sender.py            # Phase 8  — real reply send + threading
│   ├── step7_logger.py            # Phase 10 — logging setup
│   └── config.py                  # shared constants (POLL_INTERVAL, labels, etc.)
│
├── main.py                        # Phase 9  — orchestrator loop (root mein, entry point)
│
├── data/
│   └── agent_state.db             # SQLite DB (git ignored, auto-created Phase 5)
│
├── logs/
│   └── agent.log                  # rotating logs (git ignored, auto-created Phase 10)
│
└── tests/
    ├── test_fetcher.py            # Phase 11 — manual/automated test scripts
    ├── test_agent_decisions.py    # Phase 11 — 3 test-case emails (normal/promo/complaint)
    └── test_sender.py             # Phase 11 — threading verification
```

**Rules jo strictly follow karni hain (Golden Rule ka extension):**
1. Har `stepN_*.py` sirf apna kaam karega, doosre step ka logic apne andar copy-paste nahi karna — hamesha `import` karna (e.g. `main.py` mein `from src.step1_auth import get_gmail_service`).
2. Koi bhi naya constant (poll interval, label names, model name) seedha kisi file mein hardcode nahi karna — `src/config.py` mein daalna aur wahan se import karna. Isse baad mein koi bhi setting ek hi jagah se change hogi, poori codebase mein dhoondhna nahi padega.
3. `.env` mein sirf secrets/keys (`GROQ_API_KEY`, `AGENT_EMAIL`) — non-secret config `config.py` mein.
4. `data/` aur `logs/` folders auto-create honge code se (`os.makedirs(exist_ok=True)`), manually nahi banane — taake fresh clone pe bhi agent bina error chale.
5. Ek phase complete hone ke baad us file ko **dobara kabhi edit nahi karna** jab tak koi bug fix na ho — sirf naye phase ki nayi file banegi jo purani ko import karke use kare.

---

## Phase 3 — Gmail Authentication (OAuth)

File: `src/step1_auth.py`

Is file ka kaam: `credentials.json` use karke browser mein login karwana, aur `token.json` generate karna jo baad mein reuse hoga (dobara login nahi karna padega).

Requirements is file ke liye:
- Scopes: `https://www.googleapis.com/auth/gmail.modify` (read + send + label — `send`-only scope nahi, kyunki hume read+label bhi chahiye)
- `InstalledAppFlow.from_client_secrets_file()` use karke ek baar browser open hoga, login karoge, `token.json` ban jayega
- Function: `get_gmail_service()` jo authenticated `service` object return kare — ye function har next phase mein import hoga

**Test:** Ye file run karne pe browser open ho, tum apne Gmail se login karo, permission do, aur terminal mein "Authentication successful" print ho.

Mujhe iske baad batana "Phase 3 start" likh k, mai poora `step1_auth.py` code de dunga.

---

## Phase 4 — Email Fetcher (Poll Inbox)

File: `src/step2_fetcher.py`

Kaam:
- `get_gmail_service()` se service lo
- `service.users().messages().list(userId='me', q='is:unread in:inbox')` se naye unread emails ki list lao
- Har email ka full data `messages().get()` se fetch karo (headers: From, Subject, Date, Message-ID, References, Thread-ID + body)

Output: List of dicts, e.g.
```python
{
  "id": "...", "thread_id": "...", "from": "...", "subject": "...",
  "body": "...", "message_id_header": "...", "references": "...",
  "label_ids": ["INBOX", "UNREAD", "CATEGORY_PERSONAL"]
}
```

**Important:**
- `Message-ID` header aur `References` header zaroor nikalna — Phase 9 (reply sending) mein thread continuity ke liye chahiye honge.
- `labelIds` field (Gmail API response mein already available hota hai, `messages().get()` ka part hai) zaroor nikalna aur save karna — Phase 7 (Guardrails) isko use karke Promotions/Social/Spam category ki emails ko LLM tak pohanchne se pehle hi filter karega.

---

## Phase 5 — State Management (Duplicate-Reply Guard)

File: `src/step3_state.py`

Kaam: SQLite DB (`data/agent_state.db`) banao with table `processed_emails(gmail_id TEXT PRIMARY KEY, thread_id TEXT, action TEXT, timestamp TEXT)`.

Functions:
- `is_processed(gmail_id) -> bool`
- `mark_processed(gmail_id, thread_id, action)`

**Ye phase critical hai** — is ke bina agent same email ko baar baar reply kar dega har polling cycle mein.

---

## Phase 6 — Agent Core (Decision + Draft using Groq Tool-Calling)

File: `src/step4_agent.py`

Ye asli "Agent" wala part hai. Groq ke tool-calling (function calling) use karenge taake LLM khud decide kare ke kya karna hai, sirf text generate na kare.

**Tools define karenge:**
1. `reply_to_email(draft_text: str)` — agent decide kare ke reply bhejna hai, ye tool call kare draft ke saath
2. `skip_email(reason: str)` — spam/newsletter/no-action-needed emails ke liye
3. `escalate_to_human(reason: str)` — jo email agent confidently handle na kar sake (complaint, legal, sensitive)

**System prompt design karega:**
- Agent ka role: professional email assistant jo tumhari taraf se reply karta hai
- Tumhari tone/signature (tum define karoge — jaise "Regards, Asad")
- Rules: promotional/spam ko skip karo, unclear/sensitive cheezein escalate karo, baaki reply karo

LLM call mein `tools` parameter pass hoga, model jo bhi tool choose kare wahi action execute hoga.

---

## Phase 7 — Guardrails & Safety Rules

File: `src/step5_guardrails.py`

Hardcoded (LLM pe depend nahi karna) safety checks, LLM call se **pehle** chalte hain — kyunki ye rules deterministic hain, LLM ke "guess" pe chorna galat hoga.

**Filter order (isi sequence mein check karna, pehla match hi final decision hai):**

1. **Gmail category filter (spam/promo detection — sabse pehla check):**
   Email ke `label_ids` (Phase 4 se) check karo. Agar in mein se koi bhi label present hai:
   - `SPAM` → hard-skip, log karo, LLM ko call hi mat karo
   - `CATEGORY_PROMOTIONS` → hard-skip
   - `CATEGORY_SOCIAL` → hard-skip
   - `CATEGORY_FORUMS` → hard-skip

   Ye Gmail ka apna machine-learning based classifier hai (Google saalon se train karta hai), isliye ye LLM se zyada reliable aur free hai. Sirf `CATEGORY_PERSONAL` aur `CATEGORY_UPDATES` (jismein transactional/important mail aata hai) LLM tak pohanchengi — aur `CATEGORY_UPDATES` bhi tab jab sender legitimate lage.

2. **Sender-based filter (loop prevention):**
   - Sender agar `no-reply@`, `noreply@`, `donotreply@`, `mailer-daemon@`, `postmaster@` ya khud `AGENT_EMAIL` hai → auto-skip
   - Sender agar already blacklist (`config.py` mein list) mein hai → auto-skip

3. **Rate limit:** ek thread pe 1 hour mein max 1 reply (state DB se last-reply-timestamp check karke)

4. **Content sanity check:** khali body, sirf image/attachment (no text), ya bohat chhota (<10 characters) body → skip

**Sirf ye 4 checks pass karne wali email hi Phase 6 (LLM Agent) tak jaati hai.** Isse do fayde: (a) spam/promo pe kabhi LLM se ghalat "reply" decision nahi aayega, (b) Groq API calls sirf genuine emails pe lagengi, cost/rate-limit dono save honge.

Ye phase agent ko "galti se sabko spam reply" karne se bachayega — isko strictly LLM se pehle hi run karna, kabhi bhi order reverse nahi karna.

---

## Phase 8 — Reply Sender (Real Send via Gmail API)

File: `src/step6_sender.py`

Kaam:
- MIME message banao (`email.mime.text.MIMEText`)
- Headers set karo: `In-Reply-To` aur `References` = original email ka `Message-ID` (taake Gmail thread mein sahi se dikhe, naya conversation na bane)
- Base64 encode → `service.users().messages().send(userId='me', body={'raw': ..., 'threadId': thread_id})`
- Send hone ke baad: original email ko `UNREAD` label remove karo, `AI-Replied` custom label add karo

**Test phase:** Pehle apne hi doosre email address pe test bhejna, real client ko nahi.

---

## Phase 9 — Main Agent Loop (Polling Orchestrator)

File: `src/main.py`

Sab phases ko jodta hai:

```
while True:
    emails = fetch_unread_emails()
    for email in emails:
        if is_processed(email.id): continue
        if fails_guardrails(email): mark_processed(skip); continue
        action = run_agent(email)   # LLM decides: reply/skip/escalate
        if action == reply: send_reply(); mark_processed()
        elif action == escalate: add_label("Needs-Human"); mark_processed()
        else: mark_processed(skip)
    log_cycle_summary()
    time.sleep(POLL_INTERVAL_SECONDS)   # e.g. 60 seconds
```

`POLL_INTERVAL_SECONDS` `.env` mein configurable rakhna.

---

## Phase 10 — Logging & Error Handling

File: `src/step7_logger.py`

- Python `logging` module → `logs/agent.log` mein rotate hote logs
- Har email ka: id, subject, decision, timestamp log karo
- Har external call (Gmail API, Groq API) try/except mein wrap karo — ek email fail ho to poora loop crash na ho, agla email process ho

---

## Phase 11 — Testing Checklist (Phase-wise)

- [ ] Phase 3: Auth successful, `token.json` bana
- [ ] Phase 4: Unread emails console mein sahi print ho rahe hain
- [ ] Phase 6: 3 alag test emails (ek normal query, ek promotional, ek sensitive complaint) bhejo apne test inbox mein → check karo agent teeno ko sahi classify kar raha hai (reply/skip/escalate)
- [ ] Phase 7 (Guardrails): ek promotional newsletter khud ko forward karo/subscribe karo → confirm karo ke wo `CATEGORY_PROMOTIONS` label ki wajah se LLM tak pohanchay bina hi skip ho gayi (logs mein check karo)
- [ ] Phase 7: apna khud ka email address se test email bhejo (dusre account se) → confirm karo agent reply nahi karta (loop-prevention check)
- [ ] Phase 8: Real reply Gmail thread mein sahi jagah (same thread) dikh raha hai, naya thread nahi bana
- [ ] Phase 9: Loop 2-3 cycles chala k dekho, duplicate reply to nahi ho raha
- [ ] Guardrails: apne khud ke email pe agent reply na kare (loop test)

---

## Phase 12 (Advanced/Optional) — Real-Time via Gmail Push Notifications

Polling ke bajaye real-time trigger chahiye ho to:
- Google Cloud Pub/Sub topic banao
- Gmail `watch()` API call — Gmail naye email pe Pub/Sub ko push karega
- Ek webhook endpoint (FastAPI, Railway pe deploy — jaisa ShopEase mein kiya tha) jo Pub/Sub se notification receive kare aur turant agent trigger kare

Ye phase sirf tab karna jab polling-based version fully test ho chuka ho.

---

## Phase 13 — Deployment

**Option A (Windows local):** Task Scheduler se `main.py` ko startup pe ya har X minute pe run karwana.

**Option B (Cloud, 24/7):** Railway pe worker service deploy karna (`token.json`/`credentials.json` ko environment variables/secrets ke through securely pass karna, file system pe nahi).

---

## Phase 14 — README + Demo Checklist

- Setup steps (env vars, credentials)
- Architecture diagram (fetch → guardrails → agent decision → send/skip/escalate → log)
- Demo video/script: ek promotional email (skip), ek normal query (auto-reply), ek complaint (escalate)

---

### Aage kaise chalna hai

Mujhe har phase shuru karte waqt bas likh dena, e.g. **"Phase 3 start"** ya **"Phase 6 start"**, mai us phase ka poora working code de dunga (copy-paste ready), phase ke pichle wale files ko touch kiye bina.
