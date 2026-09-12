# AUC — Assessments Under Curve

A local-first residency feedback management tool for internal medicine programs. Built for clinical competency committee (CCC) meetings and ongoing resident development tracking.

## What It Does

- **Browse residents** — see all 35 residents at a glance, with photos, PGY year, and status
- **Quick-add notes** — jot observations during CCC meetings tagged with ACGME domains, sentiment (strength/concern), and priority
- **Track follow-ups** — keep a checklist of action items per resident, with a dashboard showing all open items
- **AI-generated summaries** — press a button to draft a summary across all 21 ACGME sub-competencies using your local Ollama model, with a suggested milestone level and supporting quotes for each
- **Evidence-checked** — every quote the AI produces is verified word-for-word against the actual notes before you see it; sections whose quotes don't check out are withheld
- **Edit and approve** — review each section, adjust the narrative or level, and save the final version

> Suggested levels are drafts for Clinical Competency Committee discussion, not final
> determinations. See `auc/README.md` for how summary generation and the evidence
> check work.

## Requirements

Before running setup, make sure you have:

1. **Linux** (Ubuntu, Fedora, etc.)
2. **Python 3.10 or newer** — check by opening a terminal and typing: `python3 --version`
3. **Node.js 18 or newer** — check by typing: `node --version`
   - If you don't have it: `sudo apt install nodejs npm`
4. **Ollama** (optional, for AI summaries) — install from https://ollama.ai
   - After installing, pull a generation model — any will do, e.g.
     `ollama pull qwen3:8b` — and the embedding model, whose name must
     match exactly: `ollama pull qwen3-embedding:0.6b`

## Setup (One Time)

1. Open a terminal
2. Navigate to this folder: `cd /path/to/auc`
3. Run the setup script: `bash setup.sh`
4. Open your browser to: **http://localhost:3000**

That's it. The app will start automatically every time your machine boots.

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

Any model in `ollama list` will do. Pick one in **Settings → Default Ollama
model**; that choice is used from then on, and the dropdown beside the
Generate Summary button overrides it for one run. The choice lives in your
browser, so on a new computer you re-pick it once.

`auc/README.md` explains the fallbacks, and the two things any replacement
model has to do before you trust it with a summary.

## Checking That Everything Works

```bash
bash auc/preflight.sh   # the machine: models, index, services, disk, fonts
bash auc/check.sh       # the code: lint and tests
```

Both print a line per check and say what to do about failures. `preflight.sh`
only reads, so it is safe to run at any time.

## Backing Up Your Data

All your data lives in one folder: `auc/data/`

- `auc.db` — the database with all residents, notes, follow-ups, and summaries
- `photos/` — uploaded resident photos
- `logs/summary_validation.log` — what the AI claimed vs. what was kept

The database is **not** in git, so a fresh clone starts empty and nothing in
the repository will ever remind you the database exists. Your backup is the
only thing protecting it.

Settings → **Download Full Backup** makes a consistent copy safely even while
the app is running, and a nightly backup runs on a timer — point
`AUC_BACKUP_DIR` at a cloud-synced folder so it lands off this machine. See
`auc/BACKUPS.md`.

Note the validation log is **not** in the backup zip. For a QI study that log
is research provenance, so copy it deliberately.

A backup you have never restored is a hope, not a backup. Unzip one and open
`auc.db` from it at least once.

## File Structure

```
auc/
├── setup.sh          ← run this once to set everything up
├── run.sh            ← created by setup, starts the app
├── preflight.sh      ← is this MACHINE healthy?
├── check.sh          ← is this CODE healthy?
├── .env.example      ← every environment variable, with a comment each
├── README.md         ← the fuller manual
├── backend/
│   ├── app.py        ← the Python server
│   ├── config.py     ← every environment variable is read here
│   ├── requirements.txt
│   ├── tests/        ← run with check.sh
│   └── venv/         ← created by setup
├── frontend/
│   ├── src/          ← the user interface code
│   └── dist/         ← built by setup, served to your browser
└── data/
    ├── auc.db        ← your database (created on first run)
    └── photos/       ← resident photos
```
