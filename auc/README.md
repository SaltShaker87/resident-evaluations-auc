# AUC — Assessments Under Curve

A local-first residency feedback management tool for internal medicine programs. Built for clinical competency committee (CCC) meetings and ongoing resident development tracking.

## What It Does

- **Browse residents** — see every resident at a glance, with photos, PGY year, and status (photos must be .jpg/.png/.webp, up to 5 MB)
- **Quick-add notes** — jot observations during CCC meetings tagged with ACGME domains, sentiment (strength/concern), and priority
- **Track follow-ups** — keep a checklist of action items per resident, with a dashboard showing all open items
- **AI-generated summaries** — press a button to draft a summary across all 21 ACGME sub-competencies using your local Ollama model, with a suggested milestone level and the supporting quotes for each
- **Grounded in the ACGME milestones** — each note is matched to the sub-competencies it is about, by keyword or by searching the ACGME reference material with the retrieval engine chosen in Settings: NVIDIA Nemotron by default on a DGX Spark, Standard (Ollama) elsewhere (see "The retrieval engine" below)
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
   - After installing, pull a generation model — any will do, e.g.
     `ollama pull qwen3.5:4b` — and the embedding model, whose name must
     match exactly: `ollama pull qwen3-embedding:0.6b`
   - Pick the generation model to match your graphics card's memory: 8–15 GB
     `qwen3.5:4b`, 16–32 GB `qwen3.5:9b`, over 32 GB `nemotron-3.5-lightning`.
     AUC runs best on NVIDIA Nemotron 3.5 Lightning.
5. **On an NVIDIA DGX Spark only:** an NGC API key (free, from ngc.nvidia.com)
   for a one-time `docker login nvcr.io` when setup downloads the NVIDIA
   Nemotron retrieval models. Setup recognises the Spark by itself and asks.
   See *The retrieval engine* below.

## Setup (One Time)

1. Open a terminal
2. Navigate to this folder: `cd /path/to/auc`
3. Run the setup script: `bash setup.sh`
4. Open your browser to: **http://localhost:3000**

That's it. The app will start automatically every time your machine boots.

Setup also builds the ACGME reference index (`rag/build_index.py`), once for
each retrieval engine this machine can run. On a DGX Spark it first starts the
two NVIDIA Nemotron containers with `start-nemotron.sh`. That needs a one-time
`docker login nvcr.io` (username `$oauthtoken`, password your NGC API key),
which setup offers to run, and downloads several GB the first time. None of this
is fatal: if a step fails, setup finishes the rest, Settings shows what is
missing, and the warning says how to retry.

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

1. Each note and MedHub comment is routed to the ACGME sub-competencies it relates to:
   by keyword, or, for a comment that matches no keyword, by searching the ACGME
   reference material with the retrieval engine chosen in Settings (see below).
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

### The retrieval engine

Step 1's search, for the comments no keyword catches, can be done two ways:

- **Standard (Ollama)**: `qwen3-embedding:0.6b` through Ollama, taking the
  closest match. Runs anywhere Ollama does.
- **NVIDIA Nemotron**: NVIDIA's Nemotron embedding model shortlists ten
  candidates and its reranking model, which reads the comment and each candidate
  side by side, picks one. Runs in two local containers (`nim/`), which need an
  NVIDIA GPU that supports them, such as a DGX Spark.

**A DGX Spark uses NVIDIA Nemotron by default; every other machine uses
Standard.** `setup.sh` recognises a Spark by its GB10 GPU and starts the
containers itself. Change the engine in **Settings → Retrieval engine**. The
choice is for the whole app, not one browser. An engine this machine cannot run
right now, or whose index is not built, is greyed out with the reason, and the
server refuses the switch too.

The engine never changes on its own. If the one in use stops, because a
container is down or Ollama has stopped, summaries fail with a message saying
so, and Settings offers the other. Every line of `summary_validation.log`
records which engine routed that summary (`engine=`).

On another NVIDIA machine that can run the containers, `bash start-nemotron.sh`
then `backend/venv/bin/python rag/build_index.py` makes Nemotron selectable
there too. `rag/README.md` has the detail.

## Changing the AI Model

Any model in `ollama list` will do. **Settings → Default Ollama model** picks
the one used from then on, and the dropdown next to the Generate Summary
button overrides it for a single run.

That choice is stored in your **browser**, not on the server, so it does not
travel with the machine — on a new computer you re-pick it once.

Three settings decide which model writes a summary. The first one set wins:

| | Where | Scope |
|---|---|---|
| 1 | The model picked in Settings | Sent with every generation. Normally this is what decides it. |
| 2 | `AUC_SUMMARY_MODEL` | Summaries only |
| 3 | `OLLAMA_MODEL` | Everything, and the last resort |

If a summary ever comes out of a model you did not expect, check them in that
order. To change 2 or 3, edit `~/.config/systemd/user/auc.service`, add or
change the `Environment=` line, then
`systemctl --user daemon-reload && systemctl --user restart auc`. Re-running
`setup.sh` preserves edits you make there.

**Two things any new model must do**, because the generator depends on both:

- **Honour Ollama's JSON-constrained output.** A model that answers in prose
  instead marks every section `generation_failed`.
- **Quote verbatim.** Every quote is checked against the comments routed to
  that sub-competency, and a model that paraphrases has its quotes dropped —
  and a section with no surviving quotes has its narrative discarded, so the
  report comes back empty even though the model was working. Do not relax that
  validation to accommodate a model; it is what keeps invented evidence out of
  a resident's record. Change the model instead.

Neither is on any model card, so test rather than assume:

```bash
bash auc/check-model.sh nemotron:latest
```

It runs three real sub-competency generations through the app's own prompt,
parser and validator, reports on both requirements, times a section and
extrapolates to a full 21-section run. It exits non-zero if the model cannot
be used. Nothing is written to the database or the validation log.

Then confirm on a real resident with real notes before a meeting depends on it.

## Configuration

Every environment variable the app reads is listed, with a comment each, in
**`.env.example`**. Nothing loads `.env` automatically — settings reach the app
through `Environment=` lines in `~/.config/systemd/user/auc.service`, or
exported in the shell before `bash run.sh`.

The two worth knowing about before anything goes wrong:

- **`AUC_EMBED_MODEL`** is *not* interchangeable the way the generation model
  is. It must be the model that built the ACGME index — embeddings from two
  different models are not comparable, so searching with the wrong one returns
  confident nonsense. The index records which model built it and Settings
  reports a mismatch, but if you change this, rebuild the index in the same
  breath.
- **`AUC_NIM_EMBED_URL` / `AUC_NIM_RERANK_URL`** are where the NVIDIA Nemotron
  containers listen, on this machine. Resident comments are sent to them, so
  never point them at NVIDIA's hosted service. The Nemotron embedding model is
  stamped into its own index exactly as `AUC_EMBED_MODEL` is.
- **`AUC_HOST`** defaults to `0.0.0.0`, meaning anyone who can reach this
  machine on the network can reach the app, over plain HTTP. Fine at home. On
  an untrusted network set it to `127.0.0.1` and put Tailscale Serve in front
  — see `SECURITY.md`.

## Checking That Everything Works

Four scripts, each answering a different question. All of them print a line per
check and say what to do about the failures.

```bash
bash auc/preflight.sh             # is this MACHINE healthy?
bash auc/check.sh                 # is this CODE healthy?
bash auc/verify-backup.sh         # would the newest BACKUP actually restore?
bash auc/check-model.sh <model>   # can this MODEL actually write summaries?
```

**`preflight.sh`** checks every environmental assumption the app makes:
processor family, Python and Node versions, whether every package *imports*
(installing and importing are different things, and the difference is where a
new machine bites), the built interface, the bundled fonts, the retrieval
engine in force and why (and on a Spark, the Nemotron containers), Ollama and
its models, that engine's ACGME index and its embedding-model stamp, the database and its
password, photos versus residents claiming one, the PDF fonts, the services,
lingering, and whether the app answers on its port.

It only reads, so it is safe at any time — including mid-meeting. Run it after
setup on a new machine, and first whenever something stops working. "Run
preflight" is a better first instruction than "read the manual again".

**`check.sh`** runs ruff, pytest, shellcheck, ESLint and the frontend build.
It needs the checking tools once per machine:

```bash
auc/backend/venv/bin/pip install -r auc/backend/requirements-dev.txt
```

The tests never touch real data — they point `AUC_DATA_DIR` at a scratch
directory before importing the app, and assert that it worked.

**`verify-backup.sh`** opens the newest backup (or one you name), runs an
integrity check on the database inside it, and prints what is in the backup
beside what is live right now. Rows differing is normal; a backup is a
snapshot. A missing table, a corrupt database, or an archive that will not open
is not, and it exits non-zero. It also says whether the archive was written by
the nightly timer or downloaded by hand, which are different questions — see
`BACKUPS.md`.

The failure worth catching is a database that is truncated inside an archive
that opens perfectly well. You cannot tell by looking at the file.

**`check-model.sh`** is described under *Changing the AI Model* above.

## Writing Down How This Machine Is Set Up

```bash
bash auc/capture-environment.sh
```

Some of what makes this app work is not in the repository: which model Ollama
is running, what is in the unit files, whether lingering is on, where backups
go, the Modelfile behind a fine-tuned model. All of it exists only because
somebody set it by hand, and all of it is easy to forget until it is missing.

This writes it into a dated file in your **home directory** — deliberately
outside the repository, so it cannot be committed by accident, and readable
only by you. Anything shaped like a credential is masked: an API key is
reported as set or unset, never printed, and the password hashes in the
database are never read.

It captures the **Modelfile of every installed model**, and searches the usual
places for `.gguf` files and adapter weights. That matters more than it sounds:
an Ollama model is weights *plus* a Modelfile — the system prompt, temperature
and template. Re-downloading weights from Hugging Face gets you half of it.
Keep the capture file; it holds the other half.

A handful of questions it cannot answer are left as blanks for you.

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
- **Automated daily backup** (recommended) — `setup.sh` installs a background
  backup on a timer, by default at 02:00. Point `AUC_BACKUP_DIR` at a
  OneDrive-synced folder so copies go offsite automatically. Setup and restore
  steps are in **`BACKUPS.md`**.

  ⚠ **On a machine that is switched off overnight, 02:00 never arrives.** The
  timer is set `Persistent=true`, so the missed run fires at the next boot
  instead — which means backups land whenever you next turn the machine on,
  not nightly. Everything still gets backed up eventually, but **the session
  you just finished is unprotected until the next boot.** On an always-on
  machine this does not arise. To close it on a desktop, move the schedule to a
  time the machine is actually on; `BACKUPS.md` has the edit, and `setup.sh`
  preserves it.

Check that any of this is actually working with `bash auc/verify-backup.sh`.
A backup you have never opened is a hope, not a backup — and since the database
is not tracked in git, those archives are the only copy of your data.

## File Structure

```
auc/
├── setup.sh          ← run this once to set everything up
├── run.sh            ← created by setup, starts the app
├── preflight.sh      ← is this MACHINE healthy? models, index, services, disk
├── check.sh          ← is this CODE healthy? lint and tests, both halves
├── verify-backup.sh  ← would the newest backup actually restore?
├── check-model.sh    ← can this model actually write summaries?
├── capture-environment.sh  ← write down how this machine is configured
├── start-nemotron.sh ← start the NVIDIA Nemotron containers (setup runs it on a Spark)
├── nim/              ← those two containers, defined for docker compose
├── .env.example      ← every environment variable, with a comment each
├── VERSION           ← written by the release workflow; absent in a clone
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
│   ├── rag_retrieval.py   ← routes notes to ACGME sub-competencies, with either engine
│   ├── retrieval_engine.py ← which engine is in force, and can this machine run it
│   ├── pdf_export.py ← builds summary PDFs
│   ├── backup.py     ← full backup (db + photos), manual & scheduled
│   ├── reset_password.py  ← last-resort password reset
│   ├── config.py     ← every environment variable is read here, and only here
│   ├── requirements.txt      ← the core app
│   ├── requirements-rag.txt  ← the ACGME index layer (chromadb)
│   ├── requirements-dev.txt  ← pytest and ruff, for check.sh
│   ├── tests/        ← run with check.sh; never touch real data
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
