# Deployment Guide (Phase 13)

Agent poll-based hai (`main.py`), isliye ye kisi bhi jagah chal sakta hai
jahan Python continuously chal sake. Do options hain:

---

## Option A — Windows Local (Task Scheduler)

Agent tumhare apne PC pe chalega, jab tak PC on hai.

1. Confirm karo `venv` aur saari dependencies already install hain
   (`pip install -r requirements.txt` chal chuka ho).
2. Project root mein `run_agent.bat` file already maujood hai (isi zip
   mein) — ye venv activate kar ke `python main.py` chalati hai aur
   output ko `logs/task_scheduler_output.log` mein bhi save karti hai.
3. Windows **Task Scheduler** kholo → **Create Basic Task**:
   - **Trigger**: "At startup" (PC on hote hi chale) ya "Daily, repeat
     every X minutes" (agar chaho ke wo har X minute pe restart ho)
   - **Action**: "Start a program" → Browse → `run_agent.bat` select karo
   - Finish
4. Test karne ke liye task ko right-click → **Run** karo, phir
   `logs/agent.log` check karo ke agent chal raha hai.

**Limitation:** Jab PC band/sleep ho, agent bhi ruk jayega.

---

## Option B — Cloud (Railway, 24/7)

Agent Railway pe ek "worker" service ke tor pe chalega, hamesha on rahega.

1. Is project ko GitHub repo bana lo (`credentials.json`, `token.json`,
   `.env` — ye teeno `.gitignore` mein already hain, push nahi hongi).
2. Railway.app pe naya project banao → **Deploy from GitHub repo**.
3. `Procfile` (isi zip mein maujood hai: `worker: python main.py`)
   Railway ko batayega ke ye ek background worker hai, web service nahi.
4. Railway ke **Variables** tab mein environment variables set karo
   (`.env` file ki jagah):
   - `GROQ_API_KEY`
   - `AGENT_EMAIL`
   - `POLL_INTERVAL_SECONDS`
5. `credentials.json` aur `token.json` ko Railway pe **secrets/volume**
   ke through pass karo — file system pe seedha commit nahi karna. Do
   tareeqe:
   - Railway ke "Files" / volume feature se upload karo, ya
   - Content ko base64 kar ke ek env var (`CREDENTIALS_JSON_B64`,
     `TOKEN_JSON_B64`) mein daalo aur `main.py` shuru hone se pehle
     unhe decode karke disk pe likh do (chhota helper script chahiye
     hoga agar ye route lena ho).
6. Deploy karo. Railway logs mein `logs/agent.log` jaisi output dikhni
   chahiye (ya Railway ke apne log viewer mein, agar file-based logging
   ke bajaye stdout use karo).

**Note:** `token.json` time ke sath expire/refresh hota hai — agar
long-term Railway pe chalana hai, ek baar local pe login kar ke us
`token.json` ko upload karna sabse aasan hai (jab tak refresh_token
valid hai, dobara login nahi mangega).

---

## Kaunsa option choose karun?

- **Testing/personal use, PC hamesha on hai** → Option A (Task Scheduler)
- **Production, 24/7 chahiye, PC off ho sakta hai** → Option B (Railway)
