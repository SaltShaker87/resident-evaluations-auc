# AUC — Assessments Under Curve

A local-first residency feedback management tool for internal medicine programs. Built for clinical competency committee (CCC) meetings and ongoing resident development tracking.

## What It Does

- **Browse residents** — see every resident at a glance, with photos, PGY year, and status
- **Quick-add notes** — jot observations during CCC meetings tagged with ACGME domains, sentiment (strength/concern), and priority
- **Track follow-ups** — keep a checklist of action items per resident, with a dashboard showing all open items
- **AI-generated summaries** — press a button to draft a summary across all 21 ACGME sub-competencies using your local Ollama model, with a suggested milestone level and supporting quotes for each
- **Grounded in the ACGME milestones** — each note is matched to the sub-competencies it is about, by keyword or by searching the ACGME reference material; on an NVIDIA DGX Spark that search uses NVIDIA's Nemotron models, elsewhere Ollama (see *Retrieval Engine* below)
- **Evidence-checked** — every quote the AI produces is verified word-for-word against the actual notes before you see it; sections whose quotes don't check out are withheld
- **Edit and approve** — review each section, adjust the narrative or level, and save the final version

> Suggested levels are drafts for Clinical Competency Committee discussion, not final
> determinations. See `auc/README.md` for how summary generation and the evidence
> check work.

## Easiest Way: Use the Installer

If you would rather not type commands, download the installer from the
[Releases page](https://github.com/SaltShaker87/resident-evaluations-auc/releases)
and double-click it:

| Your machine | Download |
|---|---|
| An Ubuntu PC | `AUC-Installer-…-linux-x86_64.deb` |
| NVIDIA DGX Spark or HP ZGX Nano | `AUC-Installer-…-linux-aarch64.deb` |
| If the `.deb` does not open | the matching `.AppImage` |

It checks the computer, asks three questions (AI summaries on or off and
which model; whether other computers on the network may connect; on a Spark,
the free NVIDIA account key needed to download the Nemotron models once),
installs everything, puts an AUC icon on your Desktop and in the app menu,
and opens AUC in your browser. Python and Node.js do **not** need to be
installed first — the installer brings its own. Open the installer again
later to update, repair or remove AUC.

If the installer window opens but stays blank (seen with installer 1.0.1 on
NVIDIA machines), update to 1.0.2 or newer, or start it from a terminal with
`WEBKIT_DISABLE_DMABUF_RENDERER=1 auc-installer`.

Everything below is the manual route, which still works and is what the
installer does under the hood.

## Requirements

Before running setup, make sure you have:

1. **Linux** (Ubuntu, Fedora, etc.)
2. **Python 3.10 or newer** — check by opening a terminal and typing: `python3 --version`
3. **Node.js 18 or newer** — check by typing: `node --version`
   - If you don't have it: `sudo apt install nodejs npm`
4. **Ollama** (optional, for AI summaries) — install from https://ollama.ai
   - After installing, pull a generation model — any will do, e.g.
     `ollama pull qwen3.5:4b` — and the embedding model, whose name must
     match exactly: `ollama pull qwen3-embedding:0.6b`
   - Pick the generation model to match your graphics card's memory: 8–15 GB
     `qwen3.5:4b`, 16–32 GB `qwen3.5:9b`, over 32 GB `nemotron-3.5-lightning`.
     AUC runs best on NVIDIA Nemotron 3.5 Lightning. The installer picks this
     for you.
5. **On an NVIDIA DGX Spark only:** an NGC API key, for a one-time login when
   setup downloads NVIDIA's Nemotron retrieval models (free, from
   ngc.nvidia.com). Setup recognises the Spark by itself and asks for it. See
   *Retrieval Engine* below.

## Setup (One Time)

1. Open a terminal
2. Navigate to this folder: `cd /path/to/auc`
3. Run the setup script: `bash setup.sh`
4. Open your browser to: **http://localhost:3000**

That's it. The app will start automatically every time your machine boots.

Setup also builds the ACGME reference index the summaries are grounded in. On a
DGX Spark it first starts the two NVIDIA Nemotron containers, which means a
one-time `docker login nvcr.io` (your NGC API key) and a download of several GB
the first time. If any of that fails, setup carries on and says how to finish
it later. The rest of the app works regardless.

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

Not every model works, and neither requirement is on any model card, so test
before trusting one with a meeting:

```bash
bash auc/check-model.sh nemotron:latest
```

`auc/README.md` explains what it checks and why.

## Retrieval Engine

Before a summary is written, each note is matched to the ACGME
sub-competencies it is about. Most are matched by keyword; for the rest the app
searches the ACGME reference material, and there are two ways to do that
search:

- **Standard (Ollama)** — works on any machine that runs Ollama.
- **NVIDIA Nemotron** — NVIDIA's embedding and reranking models, running in two
  local containers. More careful matching, but it needs an NVIDIA GPU that can
  run them, such as a DGX Spark.

A DGX Spark uses NVIDIA Nemotron by default and every other machine uses
Standard; setup takes care of either. Change it in **Settings → Retrieval
engine**. An engine this machine cannot run is greyed out with the reason, so
you can only switch to one that will work. The choice is for the whole app, not
just your browser.

If the engine in use stops working, summaries say so rather than quietly
switching to the other one. `bash auc/preflight.sh` checks whichever is in use.

## Checking That Everything Works

```bash
bash auc/preflight.sh             # is this MACHINE healthy?
bash auc/check.sh                 # is this CODE healthy?
bash auc/verify-backup.sh         # would the newest BACKUP actually restore?
bash auc/check-model.sh <model>   # can this MODEL actually write summaries?
```

Each prints a line per check and says what to do about the failures.
`preflight.sh` and `verify-backup.sh` only read, so they are safe to run at any
time — including mid-meeting. `auc/README.md` explains what each one covers.

There is also `bash auc/capture-environment.sh`, which writes down how this
particular machine is configured — the models, the service files, the
Modelfile behind a fine-tuned model — into a file in your home directory. Worth
running before moving to a new computer.

## Backing Up Your Data

All your data lives in one folder: `auc/data/`

- `auc.db` — the database with all residents, notes, follow-ups, and summaries
- `photos/` — uploaded resident photos
- `logs/summary_validation.log` — what the AI claimed vs. what was kept

The database is **not** in git, so a fresh clone starts empty and nothing in
the repository will ever remind you the database exists. Your backup is the
only thing protecting it.

Settings → **Download Full Backup** makes a consistent copy safely even while
the app is running, and a backup also runs on a timer — point `AUC_BACKUP_DIR`
at a cloud-synced folder so it lands off this machine. See `auc/BACKUPS.md`.

⚠ **If this machine is switched off overnight, the 02:00 timer never fires.**
The missed run happens at the next boot instead, so backups land when you turn
the machine on rather than nightly — and the session you just finished stays
unprotected until then. `auc/BACKUPS.md` explains how to move the schedule to a
time the machine is actually on.

Note the validation log is **not** in the backup zip. For a QI study that log
is research provenance, so copy it deliberately.

A backup you have never restored is a hope, not a backup — run
`bash auc/verify-backup.sh`, which opens the newest one and checks it.

## Where an Installer Install Lives

The installer keeps AUC apart from your data so that updating can never touch
the database:

```
~/.local/share/auc/
├── app/current/      ← the running version (a link to app/<version>/)
├── data/             ← auc.db, photos/, logs/ — never removed by an update
├── backups/          ← the nightly backup's default destination
└── tools/            ← the installer's private Python and uv
~/.config/auc/auc.env ← every setting, one KEY=VALUE per line
```

The manual route below installs into the folder you cloned instead. Both use
the same systemd units, so `systemctl --user status auc` works either way.

## File Structure

```
installer/            ← the graphical installer (see installer/README.md)
.github/workflows/    ← builds the release archive and the installer on each tag
auc/
├── setup.sh          ← run this once to set everything up
├── run.sh            ← created by setup, starts the app
├── preflight.sh      ← is this MACHINE healthy?
├── check.sh          ← is this CODE healthy?
├── verify-backup.sh  ← would the newest backup actually restore?
├── check-model.sh    ← can this model actually write summaries?
├── capture-environment.sh  ← write down how this machine is configured
├── start-nemotron.sh ← start the NVIDIA Nemotron containers (setup runs it on a Spark)
├── nim/              ← those two containers, defined for docker compose
├── .env.example      ← every environment variable, with a comment each
├── VERSION           ← written by the release workflow; absent in a clone
├── README.md         ← the fuller manual
├── backend/
│   ├── app.py        ← the Python server
│   ├── config.py     ← every environment variable is read here
│   ├── fonts/        ← DejaVu, bundled so PDFs look the same on every machine
│   ├── requirements.txt
│   ├── tests/        ← run with check.sh
│   └── venv/         ← created by setup
├── frontend/
│   ├── src/          ← the user interface code
│   └── dist/         ← built by setup, served to your browser
└── data/
    ├── auc.db        ← your database (created on first run)
    ├── photos/       ← resident photos
    └── logs/         ← what the AI claimed vs. what survived validation
```

## License

AUC is released under the [MIT License](LICENSE). The bundled fonts keep their
own licenses (see the `fonts/` folders), and the AI models AUC downloads,
through Ollama and NVIDIA NGC, are covered by their own terms.
