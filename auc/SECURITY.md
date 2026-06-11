# AUC Security Notes

This file records the security review and patches applied to AUC, in plain
language, so you can remember what was done and why.

**Review and patches applied: June 11, 2026**

---

## What was fixed

### 1. Anyone could download any file on the computer — including the entire database (critical)

**The problem:** The part of the server that hands the web app to your browser
would serve *any* file path it was asked for. A request like
`http://your-computer:3000/../data/auc.db` would hand over the complete
database — every resident, every note, every summary — to anyone on the
network. No password, no trace.

**The fix:** The server now checks that every requested file actually lives
inside the app's own `frontend/dist` folder before serving it. Anything
outside that folder is refused. The same check was added to the photo
endpoint.

*Changed in:* `backend/app.py` (`serve_frontend` and `get_photo`)

### 2. No login of any kind (critical)

**The problem:** Every part of the app — reading notes, deleting residents,
approving summaries, importing MedHub evaluations, even the
**"download a backup of the whole database"** button — was open to anyone who
could reach the computer over the network. The server listens on all network
interfaces, so on a hospital or home network that means everyone on it.

**The fix:** The app now requires a password.

- The first time you open the app, it asks you to **create a password**
  (minimum 8 characters).
- It then shows you a **recovery key** (format `XXXX-XXXX-XXXX-XXXX`) exactly
  once. Write it down and keep it safe.
- After that, every visit requires the password. Once you log in, your
  browser stays logged in for 30 days (or until you press **Log Out**).
- After 5 wrong password attempts in a row, the app makes you wait before
  trying again (the wait doubles with each further failure, up to 5 minutes).
  This makes password-guessing impractical.

The password gate covers **all** data endpoints at once — including the
newer MedHub import/sync, resident advancement, and database-backup features —
because it is enforced in one place for every `/api/` route except the login
routes themselves. New features added later are protected automatically.

Technical details, for the record: the password and recovery key are stored
only as scrypt hashes (never in plain text); sessions are random 256-bit
tokens stored hashed in the database and delivered as an `HttpOnly` cookie.

*Changed in:* `backend/auth.py` (new), `backend/app.py` (login required for
all `/api/` routes), `frontend/src/pages/Login.jsx` (new), `frontend/src/App.jsx`,
`frontend/src/api.js`

### 3. Other websites could talk to the app from your browser (high)

**The problem:** The server's CORS settings (`allow_origins=["*"]` with
credentials) told browsers that *any* website was allowed to read from and
write to this app. A malicious or compromised webpage open in your browser
could have quietly read or altered resident data.

**The fix:** The CORS settings were removed entirely. The web app and the
server run from the same address, so cross-site access is simply not needed —
and browsers now block it by default.

*Changed in:* `backend/app.py` (CORS middleware deleted),
`frontend/vite.config.js` (dev proxy port corrected from 8000 to 3000)

### 4. Photo uploads accepted any file (medium)

**The problem:** The photo upload accepted any file of any size with any
name. Someone could upload an `.html` or `.svg` file that the app would then
serve back as a webpage — a way to plant malicious scripts inside the app
(known as stored cross-site scripting).

**The fix:** Uploads now must be `.jpg`, `.jpeg`, `.png`, or `.webp`; the
file's actual contents are checked against its claimed type; files over 5 MB
are rejected; and a resident's old photo file is deleted when a new one is
uploaded. Photos are always served with the correct image content type.

*Changed in:* `backend/app.py` (`upload_photo`, `get_photo`)

### 5. Stricter input checking (low)

**The problem:** Sending an invalid sentiment or priority value caused an
ugly internal server error instead of a clear validation message.

**The fix:** The API now validates these fields up front and returns a clear
error.

*Changed in:* `backend/app.py` (Pydantic `Literal` types)

---

## A note on the MedHub API key

The MedHub integration reads its API URL and key from environment variables
(`backend/config.py`), and the committed file contains **empty defaults** — no
real credentials are stored in the code or in git. When you configure MedHub,
set the key via an environment variable (or a local, untracked config) rather
than typing it into a file that gets committed.

---

## Passwords and recovery — how it works

| Situation | What to do |
|---|---|
| Normal use | Enter the password on the login screen. You stay logged in for 30 days per browser. |
| Forgot the password | Click **Forgot password?** on the login screen, enter your recovery key, choose a new password. You'll get a **new** recovery key — write it down; the old one no longer works. |
| Want to change the password | The API supports it (`/api/auth/change-password`); a settings button can be added on request. Changing the password also logs out all devices and issues a new recovery key. |
| Lost the password AND the recovery key | Use the last-resort reset below. |

### Last-resort reset (requires access to the computer running AUC)

Open a terminal on the machine that runs AUC and type:

```bash
systemctl --user stop auc
cd /path/to/auc/backend
python3 reset_password.py
systemctl --user start auc
```

Then open the app in your browser — it will ask you to create a new password,
just like the first run. All data (residents, notes, summaries) is untouched;
only the password is cleared.

Note what this implies: **anyone who can log into the computer itself can
reset the app password.** The app password protects against people on the
network; physical/login access to the machine is protected by the computer's
own user account password.

---

## Still recommended before hosting on the DGX Spark (NOT yet done)

These were identified in the review but intentionally left for a later round:

1. **HTTPS.** The app currently runs over plain HTTP, so on an untrusted
   network the password could in principle be intercepted in transit. The
   standard fix is to put a reverse proxy (Caddy is the simplest) in front of
   the app to provide HTTPS, and have the app listen only on `127.0.0.1`.
2. **Start-on-boot fix.** The systemd *user* service only starts when someone
   logs into the machine. On a headless DGX Spark run
   `loginctl enable-linger $USER` once so the app starts at boot.
3. **Automated backups.** Use the in-app backup button or
   `sqlite3 data/auc.db ".backup /somewhere/safe/auc-backup.db"` on a
   schedule, ideally to another machine or drive. (Copying photos needs a
   separate copy of `data/photos`.)
4. **Per-user accounts and an audit trail** (who wrote/approved/advanced what),
   if multiple committee members will use it.
5. **Full-disk encryption** on the Spark, and checking your institution's
   policy on where resident evaluation data may be stored.
