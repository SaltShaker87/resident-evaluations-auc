# AUC — Assessments Under Curve

A local-first residency feedback management tool for internal medicine programs. Built for clinical competency committee (CCC) meetings and ongoing resident development tracking.

## What It Does

- **Browse residents** — see all 35 residents at a glance, with photos, PGY year, and status (photos must be .jpg/.png/.webp, up to 5 MB)
- **Quick-add notes** — jot observations during CCC meetings tagged with ACGME domains, sentiment (strength/concern), and priority
- **Track follow-ups** — keep a checklist of action items per resident, with a dashboard showing all open items
- **AI-generated summaries** — press a button to generate a draft summary of a resident's strengths, growth areas, and recommended actions using your local Ollama model
- **Edit and approve** — review AI drafts, edit them, and save the final version
- **Download a backup** — from the Settings page, download a dated copy of your entire database with one click
- **Password protected** — a single shared password guards all resident data (see "Logging In" below)

## Requirements

Before running setup, make sure you have:

1. **Linux** (Ubuntu, Fedora, etc.)
2. **Python 3.10 or newer** — check by opening a terminal and typing: `python3 --version`
3. **Node.js 18 or newer** — check by typing: `node --version`
   - If you don't have it: `sudo apt install nodejs npm`
4. **Ollama** (optional, for AI summaries) — install from https://ollama.ai
   - After installing, pull your model: `ollama pull qwen3:8b`

## Setup (One Time)

1. Open a terminal
2. Navigate to this folder: `cd /path/to/auc`
3. Run the setup script: `bash setup.sh`
4. Open your browser to: **http://localhost:3000**

That's it. The app will start automatically every time your machine boots.

## Logging In & Password Recovery

The first time you open the app it asks you to **create a password** (at
least 8 characters). It then shows a **recovery key** one single time —
write it down and keep it somewhere safe.

- **Daily use:** enter the password once; your browser stays logged in for
  30 days (or until you click **Log Out**).
- **Forgot the password?** Click *Forgot password?* on the login screen and
  enter your recovery key. You'll set a new password and receive a **new**
  recovery key (the old one stops working — write down the new one).
- **Lost both?** See the last-resort reset instructions in `SECURITY.md` —
  it requires sitting at the computer that runs AUC and takes one command.

A full record of the app's security measures lives in **`SECURITY.md`**.

## Daily Use

Just open your browser and go to **http://localhost:3000**. The app is already running.

## Managing the App

These commands are typed in your terminal:

| What you want to do | Command |
|---|---|
| Stop the app | `systemctl --user stop auc` |
| Start the app | `systemctl --user start auc` |
| Restart the app | `systemctl --user restart auc` |
| Check if it's running | `systemctl --user status auc` |
| View error logs | `journalctl --user -u auc -f` |

## Changing the AI Model

If you want to use a different Ollama model, edit the service file:

1. Open the file: `nano ~/.config/systemd/user/auc.service`
2. Find the line that says `Environment=OLLAMA_MODEL=qwen3:8b`
3. Change `qwen3:8b` to whatever model you want (e.g., `llama3:8b`)
4. Save and close (Ctrl+X, then Y, then Enter)
5. Restart: `systemctl --user daemon-reload && systemctl --user restart auc`

## Backing Up Your Data

All your data lives in one folder: `auc/data/`

- `auc.db` — the database with all residents, notes, follow-ups, and summaries
- `photos/` — uploaded resident photos

The simplest reliable backup is the in-app button described below. If you'd
rather copy files directly, let SQLite make the database copy so it isn't
caught mid-write:

```bash
sqlite3 auc/data/auc.db ".backup /path/to/backups/auc-backup.db"
cp -r auc/data/photos /path/to/backups/photos
```

Keep the backups on a different drive or machine. (Simply copying the whole
`data` folder while the app is running can occasionally produce a corrupted
copy of the database — use the command above instead.)

You can also download a snapshot of the database from inside the app: go to **Settings → Data Management** and click **Download Database Backup**. This saves a dated copy (e.g. `auc-backup-2026-05-27.db`) to your computer. Note that this covers the database only, not uploaded photos — copy the `data` folder if you need those too.

## File Structure

```
auc/
├── setup.sh          ← run this once to set everything up
├── run.sh            ← created by setup, starts the app
├── README.md         ← you are here
├── SECURITY.md       ← record of security measures + password recovery
├── backend/
│   ├── app.py        ← the Python server
│   ├── auth.py       ← password & login handling
│   ├── reset_password.py  ← last-resort password reset
│   ├── requirements.txt
│   └── venv/         ← created by setup
├── frontend/
│   ├── src/          ← the user interface code
│   └── dist/         ← built by setup, served to your browser
└── data/
    ├── auc.db        ← your database (created on first run)
    └── photos/       ← resident photos
```
