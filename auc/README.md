# AUC — Assessments Under Curve

A local-first residency feedback management tool for internal medicine programs. Built for clinical competency committee (CCC) meetings and ongoing resident development tracking.

## What It Does

- **Browse residents** — see all 35 residents at a glance, with photos, PGY year, and status (photos must be .jpg/.png/.webp, up to 5 MB)
- **Quick-add notes** — jot observations during CCC meetings tagged with ACGME domains, sentiment (strength/concern), and priority
- **Track follow-ups** — keep a checklist of action items per resident, with a dashboard showing all open items
- **AI-generated summaries** — press a button to draft a summary across all 21 ACGME sub-competencies using your local Ollama model, with a suggested milestone level and the supporting quotes for each
- **Evidence-checked** — every quote the AI produces is verified word-for-word against the actual notes before you see it; a section whose quotes don't check out is withheld rather than shown (see "How AI Summaries Work" below)
- **Edit and approve** — review each section, adjust the narrative or level, and save the final version
- **Download a backup** — from the Settings page, download a dated copy of your entire database with one click
- **Capture CCC meetings for study** — start a meeting and a slide-over drawer records what the room recalled unprompted, what you had to surface, and what it changed, with de-identified CSV exports (see "CCC Meeting Capture" below)
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

## How AI Summaries Work

When you press **Generate Summary**, the app does *not* ask the model to write the
whole report in one go. Instead:

1. Each note and MedHub comment is routed to the ACGME sub-competencies it relates to.
2. The app builds the report skeleton itself from the ACGME ontology — always the
   same **21 sub-competencies**, in the same order. The model never decides which
   sub-competencies exist, so it cannot invent one.
3. Each sub-competency that has at least one routed comment gets **its own small
   model call**, containing only that sub-competency's ACGME descriptor and only its
   own comments. The model writes 2–3 sentences in its own words and suggests a level.
4. Sub-competencies with no routed comments are marked "No evidence this cycle"
   without calling the model at all.
5. **Every quote is verified.** A quote that does not appear word-for-word in the
   comments routed to that sub-competency is dropped. If none of a section's quotes
   survive, its narrative and level are thrown away and the card reads "insufficient
   evidence" — unsupported text is never shown to you.

Sections appear one at a time as they finish, with a "7 of 21" counter, so a slow
model shows visible progress instead of leaving you waiting for one big result.

> **Suggested levels are drafts for the Clinical Competency Committee to discuss —
> they are not final determinations.**

### What got dropped, and why

Every dropped quote and every discarded narrative is logged, with its full text, to:

```
auc/data/logs/summary_validation.log
```

Read it any time (`tail -f auc/data/logs/summary_validation.log`) to see exactly what
the model tried to claim and what the app refused to show.

### A note on small models

Smaller models tend to paraphrase instead of quoting exactly, so more of their
sections get withheld as "insufficient evidence." That is the evidence check working
as designed, not a bug. If you see a lot of withheld sections, try a larger model.

## Changing the AI Model

The quickest way is the **model dropdown** next to the Generate Summary button — it
lists every model installed in Ollama and applies to that run only.

To change the default for everyone, edit the service file:

1. Open the file: `nano ~/.config/systemd/user/auc.service`
2. Find the line that says `Environment=OLLAMA_MODEL=qwen3:8b`
3. Change `qwen3:8b` to whatever model you want (e.g., `llama3:8b`)
4. Save and close (Ctrl+X, then Y, then Enter)
5. Restart: `systemctl --user daemon-reload && systemctl --user restart auc`

To point *only* the summary generator at a different model, add
`Environment=AUC_SUMMARY_MODEL=your-model` to the same file. The equivalent code
setting is `SUMMARY_MODEL` at the top of `backend/summary_builder.py`, along with the
request timeout (600 seconds per sub-competency) and context/temperature options.

## CCC Meeting Capture

For measuring what a committee's spontaneous recall misses. Press **Start CCC** in the
header and every resident page you open gains a slide-over drawer (`Ctrl+Shift+L`) where you
log, in order: whether the room remembered last cycle's action items unprompted, what the
room produced before you spoke, what you contributed and what visibly changed, and what was
agreed. Everything autosaves; if the backend goes away mid-meeting the writes queue in the
browser and retry, so nothing typed is lost.

When no meeting is running, the app looks and behaves exactly as it does without this
feature — no banner, no drawer, nothing extra on the resident page.

The **Study Data** page downloads three CSVs covering all meetings to date. Residents appear
only as study codes (`R001`, `R002`, …) — never a name, never an internal id — and free-text
answers are left out of the files unless you explicitly turn them on.

Full detail, including every table and every CSV column, is in **`CCC.md`**.

## Exporting & Backing Up Your Data

All your data lives in one folder: `auc/data/` (`auc.db` plus `photos/`).

Three ways to get data out, all explained in **`BACKUPS.md`**:

- **Export a summary as a PDF** — the **Export PDF** button on each approved
  summary; saves a one-pager to your Downloads folder.
- **Manual full backup** — **Settings → Download Full Backup (.zip)** saves a
  complete copy (database **and** photos) to your Downloads folder.
- **Automated daily backup** (recommended) — `setup.sh` installs a daily
  background backup. Point it at a OneDrive-synced folder so copies go offsite
  automatically. Setup and restore steps are in **`BACKUPS.md`**.

## File Structure

```
auc/
├── setup.sh          ← run this once to set everything up
├── run.sh            ← created by setup, starts the app
├── README.md         ← you are here
├── SECURITY.md       ← record of security measures + password recovery
├── BACKUPS.md        ← exporting PDFs + backup/restore + OneDrive setup
├── CCC.md            ← CCC meeting capture: tables + every CSV export column
├── backend/
│   ├── app.py        ← the Python server
│   ├── auth.py       ← password & login handling
│   ├── ccc.py        ← CCC meeting capture: schema + /api/ccc endpoints
│   ├── ccc_export.py ← CCC study exports (de-identified CSV/JSON)
│   ├── summary_builder.py ← AI summaries: one call per sub-competency + quote checking
│   ├── rag_retrieval.py   ← routes notes to ACGME sub-competencies
│   ├── pdf_export.py ← builds summary PDFs
│   ├── backup.py     ← full backup (db + photos), manual & scheduled
│   ├── reset_password.py  ← last-resort password reset
│   ├── requirements.txt
│   └── venv/         ← created by setup
├── rag/              ← ACGME ontology + reference documents (see rag/README.md)
├── frontend/
│   ├── src/          ← the user interface code
│   └── dist/         ← built by setup, served to your browser
└── data/
    ├── auc.db        ← your database (created on first run)
    ├── photos/       ← resident photos
    └── logs/         ← summary_validation.log: what the AI claimed vs. what was kept
```
