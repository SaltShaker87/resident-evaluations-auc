# Migrating AUC to the NVIDIA DGX Spark (ZGX Nano)

**What this is:** the document to have open on the desk the day the machine
arrives. Read Section 1 once before then. Work through Section 4 on the day.
Keep Section 5 open in another tab.

**The companion document** — [`SPARK_REPO_CHANGES.md`](SPARK_REPO_CHANGES.md) —
lists the changes the code itself needs. **Items 1–10 of it are now built**, so
several checks below are shorter than they were: two scripts now do most of the
work.

| | |
|---|---|
| `bash auc/capture-environment.sh` | On the **old** machine, now: writes down how it is configured (most of Section 3) |
| `bash auc/preflight.sh` | On the **new** machine: every environmental check below, in one command |
| `bash auc/check.sh` | On either: lint and tests, so "does the code work on ARM" takes ten seconds |

The individual checks are kept below anyway — when preflight says a line
failed, this is where you find out what it means.

**Target machine**

| | |
|---|---|
| Chip | NVIDIA GB10 Grace Blackwell |
| Processor family | ARM (`aarch64`) — **not** Intel/AMD |
| Memory | 128 GB, shared between processor and graphics |
| CUDA | Version 13 |
| Operating system | DGX OS (an Ubuntu variant) |
| Access | Headless, reached over Tailscale; physically on hospital wifi |

**Current machine:** Ubuntu, Intel/AMD, 32 GB RAM, three older graphics cards
(one 1080 Ti, two 1080s).

> **Revised after the `study-branch` merge.** This document was first written
> against `main` at `ebf9ad0`. `study-branch` has since been merged in, bringing
> the per-sub-competency summary generator, the CCC study instrumentation, and
> the untracking of the runtime database. Three things changed materially and are
> corrected throughout: the database is **no longer tracked in git**, grounding
> failure is now a **visible error rather than a silent downgrade**, and
> `chromadb` is now **required** for summaries rather than optional. Dependency
> risk is unchanged — the merge added no packages.

---

## The one-paragraph summary

Your Python dependencies are in far better shape than the hardware change
suggests. Five of the seven are pure Python, where processor type is simply
irrelevant. The one package with compiled parts — `chromadb` — publishes an
ARM build, and so does every compiled package it pulls in behind it. **Nothing
in this repo links against CUDA at all.** Your application never speaks to the
graphics card; it sends text over the network to Ollama, and Ollama does all
the graphics work. So the CUDA-12-versus-13 problem you are bracing for
touches exactly one piece of software on that machine, and it is not this one.

The genuine risks are about things git does not track: your custom fine-tuned
model, the search index, and the live database. Plan the day around moving
those carefully, and treat the dependency install as the routine part.

---

# Section 1 — Dependency risk table

## The four things most likely to ruin your day

Read these four even if you read nothing else in this section.

**1. Ollama may not drive the GB10 chip out of the box.** This is the whole AI
feature. If Ollama installs but cannot use the graphics hardware, summaries
will still generate — agonisingly slowly, on the processor — and you may not
immediately realise why. I cannot verify the current state of GB10 support
from here; test it before you trust it (Phase C of the checklist).

**2. ~~Your custom fine-tune exists in exactly one place on Earth.~~ — this
turned out not to be true, and the risk is retired.** Kept here because the
reasoning generalises.

`clinical-reasoning:latest` is reconstructible: the Modelfile is on disk
(`~/Modelfile-clinical`, `~/Modelfile-merged`, and in the capture file), and its
base weights are the public `llama-3.2-3b-instruct`. Account access to Hugging
Face is confirmed. It was only ever chosen because it was small enough to run
on three 1080-class cards; on the Spark the plan is a much larger model.

**The generalisable part:** an Ollama model is weights *plus* a Modelfile — the
system prompt, temperature, context length and template. Re-downloading weights
gets you half of it. Whatever model ends up writing summaries on the Spark, keep
its Modelfile somewhere that is not the Spark. `capture-environment.sh` writes
them all into one file; that file is the thing to keep.

**What replaces this as a risk:** the new model is unproven against the two
requirements in Section 5 (JSON output, verbatim quoting). Phase B now checks
that in one command before anything depends on it. You have said you intend to
move to a larger model such as Nemotron on the Spark — good — but until that
larger model is set up and giving you summaries you are happy with, this
fine-tune is your only proven generator. Copy it across *before* you
experiment, not after.

**3. Summaries now depend on the search index absolutely, not optionally.**
This is the one risk the `study-branch` merge made *worse*, and it is worth
understanding why, because it inverts the earlier advice.

The old generator sent one large prompt. If the ACGME index was unavailable it
quietly fell back to a weaker, ungrounded prompt and wrote one line to a log —
summaries kept appearing, just worse. That silent downgrade was the scariest
failure in the system.

The new generator does the opposite. `summary_builder.generate_report()` routes
evidence through `rag_retrieval` first, and if that raises `RagUnavailable` it
**emits an error to the browser and stops**. You will see a clear failure
message instead of a quietly degraded report.

That is a real improvement — the silent failure is gone from the path the
interface actually uses. But the trade is that **no index means no summaries at
all**. If `chromadb` will not install, or `qwen3-embedding:0.6b` is missing, or
the index was never built, the summary feature is simply down. Everything else
in the app — residents, notes, follow-ups, the CCC study drawer, PDF export of
already-approved summaries — keeps working normally.

So: get the index working in Phase G before you judge anything about summaries,
and read failure mode 4 rather than assuming a quiet degradation.

**4. The embedding model must be the one that built the index.** Still true,
and still silent. A *missing* model now produces a visible error, but a
*different* model does not: embeddings from two different models are not
comparable, so retrieval returns confident nonsense and the summaries that
follow are grounded in ACGME material picked essentially at random. Nothing
errors. Keep it at `qwen3-embedding:0.6b`, or rebuild the index in the same
breath as changing it.

> **No longer a risk:** the stale database committed to git. Commit `f0c3b5a`
> untracked `auc/data/auc.db`, so a fresh clone now arrives with **no** database
> and the app creates an empty one on first run. You must still copy the live
> database across (Phase E), but the trap of a plausible-looking stale file
> silently winning is gone, and `git pull` can no longer fight your data.

Everything below this point is ordinary work.

## How to read the table

- **Compiled components** means part of the package is written in C, C++ or
  Rust and has to be built as machine code for a specific processor family.
  This is the only category where ARM matters. Pure Python packages are just
  text files and run anywhere Python runs.
- **ARM compatibility** — "known-fine" means I checked the package index and
  confirmed a Linux ARM64 build is published. Where I could not confirm
  something, the row says so.
- **CUDA-sensitive** means the package contains or links to NVIDIA graphics
  libraries, and so cares whether the machine has CUDA 12 or CUDA 13.

## Row 1 — the high-risk items

| Name | What it does | Type | ARM | CUDA-sensitive | If it breaks |
|---|---|---|---|---|---|
| **Ollama** | The program that actually runs the AI model on the graphics hardware. Your app sends it text and gets a summary back. | System program (not a Python package) | **Unknown — verify on arrival.** ARM Linux builds have existed for some time, but I could not confirm GB10/Blackwell support from this session. | **Yes — this is the only CUDA-sensitive thing in the whole stack.** | Try NVIDIA's own container image for the Spark instead of the plain installer (the NVIDIA container runtime is preinstalled on DGX OS). Failing that, run it on the processor only — slow, but the app still works. As a last resort, keep pointing `OLLAMA_URL` at the old machine over the network while you sort it out. |
| **`clinical-reasoning:latest`** | Your custom Llama-3.2-3B fine-tune — the model that currently writes the summaries. | Model file, not software | Model files themselves are processor-independent. Whether Ollama can *run* it on GB10 is the row above. | Indirectly — via Ollama | Copy `~/.ollama` wholesale from the old machine. If that is lost, it cannot be recreated without the original training output. Treat it as irreplaceable until proven otherwise. |
| **`qwen3-embedding:0.6b`** | Turns text into numbers so the ACGME reference material can be searched by meaning. Used both to build the index and to look things up in it. | Model file, not software | Same as above | Indirectly — via Ollama | Pull it with `ollama pull qwen3-embedding:0.6b`. **It must be the same model that built the index** — a different one produces numbers on a different scale and retrieval quietly returns nonsense. If you ever change it, rebuild the index in the same breath. |
| **`chromadb` 1.5.9** | Stores the ACGME reference material in a form that can be searched by meaning. The only Python package here with compiled parts. | **Includes compiled components** (a Rust core) | **Known-fine.** Confirmed published: `chromadb-1.5.9-cp39-abi3-manylinux_2_17_aarch64.whl`. | No — it is processor-only, never touches the graphics card | **Since the `study-branch` merge this is required for summaries, not optional.** Without it the summary feature returns a visible error; the rest of the app is unaffected. Next options: a newer chromadb version, or run chromadb in a container. Do not plan around graceful degradation — that path is gone. |

## Row 2 — compiled packages pulled in behind chromadb

You never asked for these; `chromadb` requires them. They matter because they
are the compiled ones, and compiled means processor-specific. I confirmed an
ARM Linux build exists for every one.

| Name | What it does | Type | ARM | CUDA-sensitive | If it breaks |
|---|---|---|---|---|---|
| `onnxruntime` | Runs small machine-learning models on the processor. chromadb requires it even though this app never uses that feature. | Compiled | **Known-fine, with a caveat.** ARM builds exist for Python 3.10 and newer only. There is **no source fallback** — if no matching build exists, installation simply fails. | No — this is the processor-only build, not the CUDA one | Check your Python version first (`python3 --version`). On 3.11+ anything installs. On 3.10 the installer will step back to an older onnxruntime by itself. On 3.9 or older, install a newer Python. |
| `tokenizers` | Splits text into chunks for the model. | Compiled (Rust) | Known-fine | No | Newer version, or build from source (needs the Rust compiler) |
| `grpcio` | A way for programs to talk to each other over a network. Unused here. | Compiled | Known-fine | No | Newer version |
| `numpy` | Fast number handling. | Compiled | Known-fine | No | Newer version |
| `pydantic-core` | The engine behind data validation. Also required by FastAPI. | Compiled (Rust) | Known-fine | No | Newer version |
| `orjson`, `pybase64`, `mmh3`, `bcrypt`, `pyyaml`, `rpds-py` | Small utility packages — fast text handling, hashing, config files. | Compiled | Known-fine (all confirmed) | No | Newer versions; all have source fallbacks |
| `uvloop`, `httptools`, `watchfiles` | Speed-ups for the web server. Pulled in because chromadb asks for the "standard" flavour of uvicorn. | Compiled | Known-fine | No | These are optional accelerators — the web server runs fine without them |

## Row 3 — the packages you actually listed (all low risk)

| Name | What it does | Type | ARM | CUDA-sensitive | If it breaks |
|---|---|---|---|---|---|
| `fastapi` 0.115.0 | The web framework — defines every address the app answers on. | **Pure Python** | Known-fine (processor-independent build) | No | Won't break for ARM reasons |
| `uvicorn` 0.30.0 | The web server that actually listens on port 3000. | **Pure Python** | Known-fine | No | Won't break for ARM reasons |
| `httpx` 0.27.0 | Makes outgoing web requests — this is how the app talks to Ollama. | **Pure Python** | Known-fine | No | Won't break for ARM reasons |
| `python-multipart` 0.0.9 | Handles file uploads (resident photos, MedHub CSV files). | **Pure Python** | Known-fine | No | Won't break for ARM reasons |
| `anyio` (not pinned) | Runs blocking work off the main thread; `summary_builder` imports it. **It is not listed in `requirements.txt`** — it arrives indirectly with FastAPI. | **Pure Python** | Known-fine | No | Won't break for ARM reasons. Noted in the change plan as a declaration gap, not a risk. |
| `fpdf2` 2.8.1 | Builds the summary PDFs. | **Pure Python** | Known-fine | No | Won't break for ARM reasons. Its only environmental dependency is fonts — see the next table. |
| `aiofiles` 24.1.0 | **Nothing. It is listed in `requirements.txt` but imported nowhere in the codebase.** | **Pure Python** | Known-fine | No | Harmless either way. Noted as a cleanup item in the change plan. |

## Row 4 — frontend (browser) packages

These only matter while *building* the web interface. Once built, the result
is plain files that any machine can serve.

| Name | What it does | Type | ARM | CUDA-sensitive | If it breaks |
|---|---|---|---|---|---|
| `vite` / `esbuild` / `rollup` | The tools that compile the web interface into its final form. Each ships a small processor-specific program. | Compiled helper programs | **Known-fine.** Your `package-lock.json` already lists `@esbuild/linux-arm64` and `@rollup/rollup-linux-arm64-gnu`, so `npm install` will select them automatically on the Spark. | No | Delete `node_modules` and `package-lock.json`, then `npm install` again to re-resolve |
| `react`, `react-dom`, `react-router-dom`, `lucide-react` | The web interface itself — layout, navigation, icons. | Pure JavaScript | Known-fine | No | Won't break for ARM reasons |
| Node.js 18+ | Runs the build tools. | System program | Known-fine — ARM Linux builds are standard | No | Use `nodejs`/`npm` from DGX OS's package manager, or NodeSource |

## Row 5 — system-level things

| Name | What it does | Type | ARM | CUDA-sensitive | If it breaks |
|---|---|---|---|---|---|
| Python 3.11+ | Runs the whole backend. | System package | Known-fine | No | **Check this first.** 3.11 or newer is the comfortable floor (onnxruntime ARM builds need 3.10+; your code needs 3.9+ for its path handling). |
| NVIDIA driver + CUDA 13 | Lets software use the graphics hardware. | System | Preinstalled on DGX OS | Yes, by definition | Do not install a driver yourself. DGX OS ships a matched set; replacing it is how people break these machines. |
| DejaVu fonts | Gives the PDF export proper quote marks and dashes. | System package | Known-fine | No | Without it, PDFs still generate — the code falls back to a basic font and converts fancy characters to plain ones. Cosmetic only. `sudo apt install fonts-dejavu-core` fixes it. |
| `systemd` user services | Starts the app automatically and runs the nightly backup. | System | Known-fine | No | See Section 5 — there is a well-known gotcha on headless machines. |
| Tailscale | Lets you reach the machine from elsewhere. | System program | Known-fine — ARM Linux builds are standard | No | See Section 5 for the hospital-wifi specifics. |
| ~~Google Fonts~~ | **No longer applies.** The two typefaces are now bundled in the repository and served by the app, so a page load makes no outbound request at all. | — | N/A | No | Nothing to do. Verified with every non-local request blocked. |

---

# Section 2 — Things that are NOT in this repo

Everything the running application needs that git does not carry. I found these
by reading what the code opens, writes and expects.

## Sensitive — move these deliberately, not casually

> **Handle these with a direct encrypted transfer between the two machines you
> control** — `scp` or `rsync` over SSH (ideally over Tailscale, once both
> machines are on it), or a USB drive that stays in your possession. Do not
> route them through personal cloud storage, email, or a chat app. Institutional
> OneDrive may be acceptable under your organisation's agreements; a personal
> Dropbox or Google Drive generally is not.

| What | Where it probably lives on the old machine | Why it is sensitive | How to move it |
|---|---|---|---|
| **The live database** | `auc/data/auc.db` — plus possibly `auc.db-wal` and `auc.db-shm` alongside it | Real resident names, and once you are using it for real, actual committee notes and summaries. Also holds your password hash, active login sessions, and **all CCC study data** — the five `ccc_*` tables and each resident's `study_code`. |  **Stop the app first** (`systemctl --user stop auc`) so nothing is mid-write, then copy all three files if they exist. Better still, use the app's own **Settings → Download Full Backup** button, which makes a consistent copy safely even while running. |
| **Resident photos** | `auc/data/photos/` | Identifiable photographs of named individuals. | Same transfer. The full-backup zip already includes these — it is the simplest correct route. |
| **Your app password and recovery key** | Not in any file — in your head, or wherever you wrote the recovery key down | They unlock everything above. | These travel with the database. The hashes are inside `auc.db`, so once you have moved the database, the same password works on the Spark. **Bring the recovery key with you.** |
| **Backup archives** | `~/auc-backups/`, or wherever `AUC_BACKUP_DIR` points — possibly inside a synced OneDrive folder | Each zip is a complete copy of the database and every photo. | Decide whether these move at all. You may prefer to leave the history on the old machine and start a fresh backup series on the Spark. |
| **OneDrive or rclone credentials**, if you set up offsite backups | `~/.config/onedrive/`, or `~/.config/rclone/rclone.conf` | These contain live access tokens to your institutional storage. | **Do not copy these files.** Re-run `onedrive` or `rclone config` on the Spark and sign in freshly. Copied tokens are a security smell and often stop working anyway. |
| **MedHub CSV exports**, if you keep any | Wherever you downloaded them — probably `~/Downloads` | Evaluation data about named residents. | Only move them if you still need to import them. Otherwise leave them behind. |

## Not sensitive, but the app will not work without them

| What | Where it lives on the old machine | How to move it |
|---|---|---|
| **Ollama's model store — including your fine-tune** | `~/.ollama/` (models under `~/.ollama/models/`) | **Copy the whole directory.** It will be large — tens of gigabytes. This is the only copy of `clinical-reasoning:latest`. Verify afterwards with `ollama list` on the Spark. |
| **The summary validation log** | `auc/data/logs/summary_validation.log` — created by the new generator | **Not covered by the backup**, which copies only `auc.db` and `photos/`. It records every quote dropped for failing verbatim validation and every narrative discarded as unsupported. For a QI study that is research provenance, so decide deliberately: copy it across, or accept starting a fresh log. Either is defensible; losing it without noticing is not. |
| **The ACGME search index** | `auc/rag/chroma_db/` — deliberately excluded from git as a build artifact | **Do not copy it. Rebuild it** with `python auc/rag/build_index.py`. It contains a binary index; rebuilding on the target machine avoids any question of format or processor compatibility, takes about a minute, and is the supported path. Requires `qwen3-embedding:0.6b` to be pulled first. |
| **The Python environment** | `auc/backend/venv/` | **Never copy this.** It contains programs compiled for Intel/AMD. Copying it onto ARM produces `exec format error`. `setup.sh` recreates it. |
| **The installed browser packages** | `auc/frontend/node_modules/` and `auc/frontend/dist/` | **Never copy these either**, for the same reason. `setup.sh` rebuilds them. |
| **The service definitions** | `~/.config/systemd/user/auc.service`, `auc-backup.service`, `auc-backup.timer` | Do not copy — they contain the old machine's file paths baked in, which will differ. `setup.sh` regenerates them. **But read them first** and write down what `OLLAMA_MODEL` and `AUC_BACKUP_DIR` are set to, because those settings live nowhere else. |
| **Your default model choice in the app** | Your browser's local storage, under the key `defaultOllamaModel` | This is stored in the *browser*, not on the server, so it does not migrate with the machine. You will simply re-pick it in **Settings** on first use. Worth knowing so it does not look like a fault. |

## Already in the repo — no action needed

For completeness, so you do not go hunting: the ACGME reference documents
(`auc/rag/documents/`), the ontology (`auc/rag/ontology/acgme_ontology.json`),
`setup.sh`, and all application code are tracked in git and arrive with the
clone.

## The trap in this section — now largely disarmed

This used to read as a warning. Commit `f0c3b5a` fixed it, so it is now a note.

`auc/data/auc.db` **was** tracked in git, which meant a fresh clone handed you a
plausible-looking but stale database — one predating the password feature, with
none of your real content — and meant `git pull` could fight your live data.
That file is now untracked, and the ignore rules were widened to cover the other
artifacts that carry resident data in real use: MedHub CSV exports, backup
`.zip` archives, exported summary PDFs, stray database copies, and the SQLite
`-wal`/`-shm` sidecars that can hold rows not yet written to the main file.

What this means on arrival day: **a fresh clone arrives with no database at
all.** The app creates an empty one on first run. That is the correct behaviour
and it makes Phase E unambiguous — if you skip copying the live database, you
get an empty app asking you to create a password, which is obvious, rather than
a populated-looking app running on stale data, which is not.

One consequence worth knowing: because the database is no longer tracked,
nothing in git will ever remind you it exists. Your backup is now the only thing
standing between you and losing it. Section 6's restore drill is not optional.

---

# Section 3 — Things configured outside the repo

Every one of these exists only as a setting made by hand, and every one is easy
to forget until the moment it is missing.

**Run `bash auc/capture-environment.sh` first** — it answers most of them
automatically into a file in your home directory, including the contents of
every Modelfile on the machine. The blanks still shown below are the ones that script
answers for itself — `ollama list`, the unit files, timers, `nvidia-smi`,
lingering — so their answers live in your capture file rather than in this
repository, where they do not belong. What is written in below is the part only
a person can answer.

**Still outstanding: Q31 and Q32.** Q31 in particular — it contains the real
deadline for the Spark being ready.

### Ollama

1. How did you install Ollama? (official script / apt package / container / other)
   → `______________________________`
2. Does it run as a system service or a user service, or do you start it by hand?
   → `______________________________`
3. Run `ollama list` and paste the full output — every model, with sizes:
   → `______________________________`
4. Is `OLLAMA_HOST` set to anything? (`systemctl show ollama -p Environment`, or check `~/.bashrc`)
   → `______________________________`
5. Is `OLLAMA_MODELS` set, i.e. are models stored somewhere other than `~/.ollama`?
   → `______________________________`

### The custom fine-tune

6. Do you still have the Modelfile used to create `clinical-reasoning:latest`? Where?
   → **Yes — `~/Modelfile-clinical` and `~/Modelfile-merged`**, in the home
   directory. `capture-environment.sh` now copies their contents into the
   capture file.
7. Do you still have the underlying model file it was built from (a `.gguf`, or adapter weights)? Where?
   → **Yes — `~/clinical-reasoning-merg…​.gguf`**, also in the home directory.
   Not inside `~/.ollama`: Ollama stores content-addressed blobs under
   `models/blobs/` with sha256 filenames, never a named `.gguf`.
8. Is any of that backed up anywhere other than the old machine?
   → **Intended plan: re-download from Hugging Face**, including the fine-tune,
   rather than copying `~/.ollama` across.

   ⚠ **This only half works, and the half it misses is the one that matters.**
   An Ollama model is weights *plus* a Modelfile — system prompt, temperature,
   context length, template. Hugging Face has the weights. It does not have the
   Modelfile, and a fine-tune running with a different system prompt or
   temperature is not the model that was validated.

   So the plan is sound **provided** the Modelfiles travel separately. They are
   in the capture file; keep it. Two things still to confirm while the old
   machine is here: that the model really is uploaded, and that the account can
   still be signed into.

   The weights being re-downloadable does downgrade this from "exists in
   exactly one place on Earth" — but only for the weights.

### The app's own service

9. Run `cat ~/.config/systemd/user/auc.service`. What is `OLLAMA_MODEL` set to?
   → `______________________________`
10. Have you ever run `loginctl enable-linger`? (Check: `loginctl show-user $USER -p Linger`)
    → `______________________________`
11. Is the repo checked out somewhere other than your home directory? Full path:
    → `______________________________`
12. Have you hand-edited either service file since `setup.sh` created it?
    → `______________________________`

### Backups

13. Run `cat ~/.config/systemd/user/auc-backup.service`. What is `AUC_BACKUP_DIR`?
    → `______________________________`
14. Is OneDrive or rclone actually set up and syncing? Which one?
    → `______________________________`
15. Run `systemctl --user list-timers`. Is the backup timer actually firing, and when did it last run?
    → `______________________________`
16. When did you last confirm a backup could be *restored* — not just that a file appeared?
    → **Never, as of 2026-09-12.** This is the largest open risk in the
    migration: the database left git in `f0c3b5a`, so these zips are the only
    copy. `bash auc/verify-backup.sh` now does the drill in one command — it
    opens the newest archive, integrity-checks the database inside it, and
    compares it against live.

### Network and access

17. Is Tailscale installed on the old machine? Under which account?
    → Yes — it is how the machine is reached remotely. Account recorded in the
    capture file's `tailscale status` output.
18. Is a firewall active? (`sudo ufw status`) What is allowed?
    → `______________________________`
19. Does the hospital network require registering a device's hardware address, or signing in through a web page, before it grants access?
    → **Credentialed wifi login, no hardware registration.** The credentials are
    to hand and will be used on the Spark. So Phase A should expect a sign-in
    step before `curl -sI https://pypi.org` will succeed.
20. How do you reach the app today — `localhost:3000`, a hostname, a fixed address?
    → **Always `localhost:3000`.** Either sitting at the machine, or over SSH
    through Tailscale and then opening `localhost:3000`. Never by the machine's
    network address.

    **This simplifies item 11 considerably.** Nothing ever connects to the app
    from another host, so `AUC_HOST=127.0.0.1` is sufficient on its own and
    Tailscale Serve becomes optional convenience rather than a requirement —
    SSH already encrypts the tunnel. One line in the service file, no reverse
    proxy to set up on arrival day.
21. Does anyone other than you use the app? From what device?
    → **No one else.** Single user, which is what makes the single shared
    password and the localhost-only binding above adequate.

### Graphics and system

22. What does `nvidia-smi` report on the old machine — driver and CUDA version?
    → `______________________________`
23. Is the old machine's disk encrypted?
    → `______________________________`
24. Any other scheduled jobs — `crontab -l`, or other systemd timers?
    → `______________________________`
25. Is anything else using port 3000, or any other service you would need to move?
    → `______________________________`

### Data

26. Have you imported MedHub CSV files? Where are the originals?
    → `______________________________`
27. Roughly how large is `auc/data/photos/`? (`du -sh auc/data/photos`)
    → `______________________________`
28. Do you have the app password and recovery key written down somewhere you can reach on arrival day?
    → **Yes, both.** Bring them; the database carries the hash, so the same
    password works on the Spark.

### The summary generator and the study

29. Is `AUC_SUMMARY_MODEL` set anywhere? (Check the service file and `~/.bashrc`.) If unset, summaries use whatever `OLLAMA_MODEL` is.
    → `______________________________`
30. Which model have you actually been generating summaries with since the rewrite, and roughly how long does a full 21-section run take?
    → `______________________________`
31. Is the recall QI study still collecting data? If so, when is the next CCC meeting — i.e. what is your real deadline for the Spark being ready?
    → **Still collecting. No hard deadline; meetings are monthly, and the study
    should finish around June 2027.**

    Two consequences. First, there is no rush, so Section 6's advice to run both
    machines in parallel until the Spark is proven costs nothing — take it.
    Second, **cut over immediately after a meeting**, not before one: that gives
    roughly four weeks before anything depends on the new machine.

    It also means the `ccc_*` tables accumulate real research data every month,
    which is what makes the backup drill below non-optional.
32. Have you exported the study CSVs yet, and where did you put them?
    → **Not yet.** Plan: a folder outside the repository, uploaded manually.
    That is the right instinct — `*.csv` is gitignored, but outside the repo is
    safer still.

    ⚠ **Export them now rather than at the end.** Two reasons that have nothing
    to do with the migration: it is the only way to find out the exporter works
    on your real data rather than on test rows, and it gives months of research
    data a copy that does not depend on the app or its database. Doing it after
    each meeting is the right cadence. Check by eye that the files carry
    `study_code` and no names — the exporter is written to raise rather than
    emit an identifying file, but confirm it on real data.
33. Does `auc/data/logs/summary_validation.log` exist on the old machine, and do you want its history kept?
    → `______________________________`


---

# Section 4 — Arrival-day checklist

Every item has something you can observe. Where a command is the action, it is
given. Tick as you go.

---

## Phase 0 — Before the machine arrives (do this now)

- [ ] **Capture the old machine's configuration.** `bash auc/capture-environment.sh`
      *Observable:* a file appears in your home directory answering roughly 25 of Section 3's
      33 questions, with the rest left as blanks. It also captures the **Modelfile of every
      installed model** — for `clinical-reasoning:latest` that is the only written record of
      how it was made, so copy it somewhere that is not this machine.
- [ ] **Answer the blanks it leaves.** The ones only you know: the hospital network, where the
      `.gguf` came from, when you last restored a backup.
- [ ] **Prove a backup restores.** `bash auc/verify-backup.sh`
      *Observable:* it opens the newest archive, integrity-checks the database inside it, and
      prints what is in the backup beside what is live. Rows differing is normal — the backup
      is a snapshot. A missing table, a corrupt database, or an archive that will not open is
      not, and it exits non-zero.
      **As of 2026-09-12 this had never been done.** Since the database left git, these zips
      are the only copy of your data. The failure worth catching is a truncated database
      inside an archive that opens perfectly well; you cannot tell by looking at the file.
- [ ] **Record the exact model names** you are using: `ollama list > ~/ollama-inventory.txt`.
- [ ] **Copy the Modelfile for your fine-tune somewhere safe**, if you still have it.
- [ ] ~~**Do the "can be done now" items**~~ — done. Items 1–10 of
      [`SPARK_REPO_CHANGES.md`](SPARK_REPO_CHANGES.md) are built. Pull them onto the old
      machine and run `bash auc/check.sh` and `bash auc/preflight.sh` there **first**, so you
      arrive knowing what healthy looks like.
- [ ] **Find out about the hospital network** before you need it. Ask IT: does a new device need registering? Is there a sign-in page? Are devices on the wifi allowed to talk to each other?

---

## Phase A — First boot and network

- [ ] **Unbox, connect power, connect a monitor and keyboard for now.** Do the first boot with a screen attached even though the machine will end up headless; first-boot setup is much easier to diagnose with a display.
- [ ] **Complete DGX OS first-boot setup.** Create your user account.
      *Observable:* you reach a desktop or a login prompt.
- [ ] **Confirm the processor family.** `uname -m`
      *Observable:* prints `aarch64`. If it prints `x86_64`, stop — something is very unexpected.
- [ ] **Confirm the memory.** `free -h`
      *Observable:* total shows roughly 120 GB or more.
- [ ] **Confirm the graphics hardware and CUDA version.** `nvidia-smi`
      *Observable:* a table appears naming the GB10 hardware, with a CUDA version in the top-right. Write that version down. If this command is not found, stop and resolve it before anything else.
- [ ] **Check the Python version.** `python3 --version`
      *Observable:* 3.11 or higher is ideal. 3.10 is fine. **3.9 or lower — install a newer Python before continuing**, or the search index will not install.
- [ ] **Check Node.js.** `node --version`. If missing: `sudo apt install nodejs npm`
      *Observable:* prints v18 or higher.
- [ ] **Get on the network.** Wired if at all possible — it sidesteps most of the wifi complications below.
      *Observable:* `ping -c3 1.1.1.1` succeeds.
- [ ] **If on hospital wifi:** connect, and if a sign-in page is expected, open a browser and complete it.
      *Observable:* `curl -sI https://pypi.org | head -1` returns `HTTP/2 200`. If it returns something that looks like a login page, you are still behind the portal.
- [ ] **Install Tailscale.** `curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`
      *Observable:* it prints a sign-in link; after signing in, `tailscale ip -4` prints an address starting `100.`.
- [ ] **Find out whether you got a direct connection or a relay.** `tailscale ping <your-laptop-name>`
      *Observable:* it says either `via DERP` (relayed through Tailscale's servers — normal on restrictive networks, works fine, just slower) or `direct` (better). **Either is acceptable.** You only need to know which, so that later slowness is not a mystery.
- [ ] **Prove you can reach the machine from your laptop.** From the laptop: `ssh <user>@<spark-tailscale-name>`
      *Observable:* you get a shell. From here on you can unplug the monitor.
- [ ] **Turn on linger**, so services start at boot without anyone logging in: `loginctl enable-linger $USER`
      *Observable:* `loginctl show-user $USER -p Linger` prints `Linger=yes`. **Do not skip this.** On a headless machine, without it, the app only starts when someone logs in — which nobody ever does.

---

## Phase B — Ollama and the graphics hardware

This phase is the one with real uncertainty in it. Work through it before
touching the application.

- [ ] **Install Ollama.** `curl -fsSL https://ollama.com/install.sh | sh`
      *Observable:* `ollama --version` prints a version.
- [ ] **Confirm the service is running.** `systemctl status ollama`
      *Observable:* shows `active (running)`.
- [ ] **Pull a small model as a test — do not start with a large one.**
      `ollama pull llama3.2:3b`
      *Observable:* download completes and `ollama list` shows it.
- [ ] **Run it.** `ollama run llama3.2:3b "Say hello in one sentence."`
      *Observable:* a sentence comes back within a few seconds.
- [ ] **Confirm it used the graphics hardware, not the processor.** While a generation is running, in another terminal: `ollama ps`
      *Observable:* the `PROCESSOR` column says **100% GPU**. If it says CPU, the graphics path is not working — see Section 5. Cross-check with `nvidia-smi` showing memory in use.
- [ ] **Only now, try a larger model.** Watch memory as it loads: `watch -n1 free -h` in a second terminal.
      *Observable:* available memory drops but does not approach zero. **If the machine becomes unresponsive, you have hit the memory problem described in Section 5.** Note the size of the largest model that loads comfortably; on 128 GB shared memory, leave generous headroom — the graphics hardware and the operating system are drawing from the same pool.
- [ ] **Pull the embedding model — exact name matters.** `ollama pull qwen3-embedding:0.6b`
      *Observable:* `ollama list` shows `qwen3-embedding:0.6b`.
- [ ] **⚠ Check the generation model can actually write summaries.**
      `bash auc/check-model.sh <the model you intend to use>`
      *Observable:* it reports ✓ on both hard requirements — JSON-constrained output and
      verbatim quoting — and prints how long a full 21-section report would take. It exits
      non-zero if the model is unusable.
      **Do this before anything depends on the new model.** Neither requirement appears on
      any model card, and the failure mode of the second one is a report that comes back
      empty while the model appears to be working perfectly. If it fails, try another model;
      do not weaken the validation.

---

## Phase C — Bring across the models

- [ ] **Stop Ollama on the Spark before copying into its directory.** `sudo systemctl stop ollama`
- [ ] **Copy the model store from the old machine.** From the old machine:
      `rsync -avP ~/.ollama/ <spark-tailscale-name>:~/.ollama/`
      *Observable:* rsync completes without error. This may take a long while.
- [ ] **Start Ollama again.** `sudo systemctl start ollama`
- [ ] **Confirm your fine-tune arrived.** `ollama list`
      *Observable:* `clinical-reasoning:latest` appears in the list.
- [ ] **Confirm it actually runs.** `ollama run clinical-reasoning:latest "Summarise in one sentence: the resident was punctual."`
      *Observable:* a sensible sentence comes back. **This is your safety net** — if the larger model you want to move to does not work out, this is what you fall back on.

---

## Phase D — Get the code

- [ ] **Clone the repository.** `git clone https://github.com/SaltShaker87/resident-evaluations-auc.git ~/resident-evaluations-auc`
      *Observable:* the directory exists and `ls ~/resident-evaluations-auc/auc` shows `backend`, `frontend`, `rag`.
- [ ] **Note the full path.** It gets written into the service files, so it must not move afterwards.
      *Observable:* `pwd` inside the repo — write it down.
- [ ] **Confirm no Intel/AMD leftovers came along.** `ls ~/resident-evaluations-auc/auc/backend/venv 2>/dev/null; ls ~/resident-evaluations-auc/auc/frontend/node_modules 2>/dev/null`
      *Observable:* both say "No such file or directory". If either exists, delete it — you have accidentally copied instead of cloned.

---

## Phase E — Bring across the data

- [ ] **Stop the app on the old machine** so nothing is written mid-copy: `systemctl --user stop auc`
- [ ] **Copy the live database and photos.** From the old machine:
      `rsync -avP ~/path/to/auc/data/ <spark-tailscale-name>:~/resident-evaluations-auc/auc/data/`
      *Observable:* `auc.db` and `photos/` land on the Spark.
- [ ] **Confirm you got the live database, not the stale one from git.** On the Spark:
      `sqlite3 auc/data/auc.db "select count(*) from auth_config"`
      *Observable:* prints `1`. **If it prints 0, or errors saying no such table, you are looking at the stale copy from git** — the live one has a password configured. Re-copy.
- [ ] **Sanity-check the contents.** `sqlite3 auc/data/auc.db "select count(*) from residents; select count(*) from notes;"`
      *Observable:* numbers matching what the old machine had.
- [ ] **Check the photos came across.** `ls auc/data/photos | wc -l`
      *Observable:* matches the old machine's count.
- [ ] **Restart the old machine's app.** `systemctl --user start auc` — it stays live as your fallback.

---

## Phase F — Dependencies

- [ ] **Run the setup script.** `cd ~/resident-evaluations-auc/auc && bash setup.sh`
      *Observable:* it prints five ticked steps and finishes with the "Setup complete" box. This is where an ARM problem would show itself — watch for errors rather than letting it scroll past.
- [ ] **Confirm the Python packages installed.** `auc/backend/venv/bin/pip list | grep -Ei "chromadb|fastapi|uvicorn|fpdf2|onnxruntime"`
      *Observable:* all five appear with versions.
- [ ] **Confirm chromadb actually loads** — installing and importing are different things:
      `auc/backend/venv/bin/python -c "import chromadb; print(chromadb.__version__)"`
      *Observable:* prints `1.5.9` with no error. **An error mentioning a missing `libsomething.so` file is the failure mode described in Section 5.**
- [ ] **Confirm the web interface was built.** `ls auc/frontend/dist/index.html`
      *Observable:* the file exists.
- [ ] **Install the PDF fonts.** `sudo apt install fonts-dejavu-core`
      *Observable:* `ls /usr/share/fonts/truetype/dejavu/DejaVuSans.ttf` finds the file.
      (The *web* fonts are now bundled in the repo — nothing to install, and no outbound
      request when a page loads.)
- [ ] **Run the code checks.** `bash auc/check.sh`
      *Observable:* ruff, pytest, shellcheck, ESLint and the frontend build all pass. First
      time on a machine this needs
      `auc/backend/venv/bin/pip install -r auc/backend/requirements-dev.txt`.
      **This is the fastest answer to "does the code work on ARM".**

---

## Phase G — Build the search index

- [ ] **Build it.** `auc/backend/venv/bin/python auc/rag/build_index.py`
      *Observable:* prints `Indexed 42 chunks into collection 'acgme_guidelines'` — expect 21 per source file, 42 total. A much smaller number means the reference documents were not read properly.
- [ ] **Test that retrieval works.** `auc/backend/venv/bin/python auc/rag/test_query.py "missed a posterior circulation stroke"`
      *Observable:* three results come back, and the top one is plausibly about clinical reasoning or patient care — not a random professionalism entry. If results look arbitrary, the embedding model is probably not the one the index was built with.
- [ ] **Do not proceed to Phase H expecting summaries to work until both of the above pass.** Since the `study-branch` merge, the summary generator treats a missing index as a hard error rather than falling back. No index means no summaries.

---

## Phase H — First run

- [ ] **Check the service is running.** `systemctl --user status auc`
      *Observable:* `active (running)`.
- [ ] **Check it answers locally.** `curl -s localhost:3000/api/auth/status`
      *Observable:* returns JSON containing `"setup_required":false` — meaning it found your migrated database and its existing password.
- [ ] **Open it from your laptop.** `http://<spark-tailscale-name>:3000`
      *Observable:* the login page appears.
- [ ] **Log in with your existing password.**
      *Observable:* you get in. If it asks you to *create* a password instead, you are on the wrong database — go back to Phase E.

---

## Phase I — Verification

**Start with `bash auc/preflight.sh`.** It covers the architecture, Python, the
virtual environment (checking every package *imports*, not just installs — which
is exactly where an ARM problem shows up), the built interface, the bundled
fonts, Ollama and its models, the ACGME index and its embedding-model stamp, the
database and its password, photos versus residents claiming one, DejaVu, the
services, lingering, and whether the app answers on its port. Every failure line
tells you what to do about it.

What preflight cannot check is whether the app is any *good*. That is the rest of
this list, and none of it can be skipped.

- [ ] **Resident list is complete.** Open the main page.
      *Observable:* all your residents appear, at the right PGY levels, with their photos.
- [ ] **Photos are actually served.** Look at any resident with a photo.
      *Observable:* the image renders — not a broken-image icon. (This proves the photos directory copied correctly, not just the database rows.)
- [ ] **Writing works.** Add a note to any resident, then reload the page.
      *Observable:* the note is still there. (Proves the database is writable, not read-only from the copy.)
- [ ] **The app can see Ollama.** Go to Settings.
      *Observable:* the "Default Ollama model" dropdown lists your models. **Not** "Ollama not reachable". Pick your model here — remember this setting lives in the browser, so do it on whichever browser you will actually use.
- [ ] **Summary generation streams, section by section.** Open a resident with notes and generate a summary.
      *Observable:* the report appears as **structured cards grouped by the six ACGME domains**, filling in one sub-competency at a time, with a progress counter climbing toward 21. Not a wall of markdown — that was the old generator. Expect a few minutes for a full run; you measured 3m11s on a 27-billion-parameter model.
- [ ] **⚠ The grounding check.** The log to watch has moved. The new generator writes to its own file, not the service journal:
      `tail -f auc/data/logs/summary_validation.log`
      *Observable:* a line reading
      `RUN START model=<model> resident=<name> comments=N total_subcompetencies=21 routing={...}`
      The `routing=` map is the thing to read — it names each sub-competency that received evidence and how many comments went to it. **An empty or near-empty routing map means retrieval found nothing**, even though the run technically succeeded.
      If retrieval is unavailable entirely you will now see `RETRIEVAL UNAVAILABLE` here **and a visible error in the browser** — the silent downgrade is gone. Go back to Phase G.
- [ ] **Quote validation is working.** In the same log, look for the validation decisions.
      *Observable:* dropped quotes and discarded narratives are logged in full. Seeing a few drops is healthy — it means unsupported text is being caught before it reaches you. Seeing *every* section discarded means the model is not returning usable JSON; see failure mode 12.
- [ ] **Sections match the ontology exactly.** Count the cards in the finished report.
      *Observable:* exactly 21 sub-competencies, every one from `acgme_ontology.json`, with no invented ones. Sections without evidence read "No evidence this cycle" rather than being absent or fabricated.
- [ ] **Summary quality is comparable.** Generate a summary for the same resident on the old machine and read both side by side.
      *Observable:* the new one is at least as specific and as well-organised by competency domain. If you have switched to a larger model, it should be better — but confirm, do not assume.
- [ ] **Approve and export a PDF.** Approve a summary, then use Export PDF.
      *Observable:* the PDF opens, and any dashes and curly quotes render properly rather than as `?` characters. (If they are wrong, the DejaVu font step did not take.)
- [ ] **The CCC study instrumentation works.** Start a meeting from "Start CCC" in the header.
      *Observable:* an amber banner appears at the top of every page. Open a resident and a pill appears bottom-right; `Ctrl+Shift+L` toggles the drawer. Tap **Spontaneous input** and reload the page — the value persists.
- [ ] **The study queue survives the backend going away.** With a meeting open, stop the app (`systemctl --user stop auc`), record something in the drawer, then start the app again.
      *Observable:* the pill shows writes waiting, and they land once the backend returns. Nothing is lost.
- [ ] **Study exports are de-identified.** Download each of the three CSVs from the Study Data page.
      *Observable:* they contain `study_code` values and **no resident names and no resident ids**. Open one and confirm by eye. The exporter is written to raise rather than emit an identifying file, but confirm it on the real data.
- [ ] **Close the test meeting** so it does not pollute study data, and delete the test rows if you created any.
- [ ] **Manual backup works.** Settings → Download Full Backup.
      *Observable:* a zip downloads; `unzip -l` shows `auc.db` and photos inside. The CCC study tables live inside `auc.db`, so they are covered — but note the validation log is **not** in the zip.
- [ ] **The nightly backup is scheduled.** `systemctl --user list-timers | grep auc`
      *Observable:* `auc-backup.timer` appears with a next-run time.
- [ ] **Run a backup now to prove it works, rather than waiting for 2 AM.** `systemctl --user start auc-backup.service && journalctl --user -u auc-backup.service -n 5`
      *Observable:* the log says `Backup written: ... (N MB)`. If it says `AUC_BACKUP_DIR is not set`, set it — see `BACKUPS.md`.
- [ ] **Point backups offsite.** Edit `AUC_BACKUP_DIR` to a synced folder, per `BACKUPS.md`, then `systemctl --user daemon-reload`.
      *Observable:* a test backup lands in the synced folder and appears in OneDrive.
- [ ] **⚠ Survives a reboot.** `sudo reboot`. Wait. Then, without logging in at the console, from your laptop open `http://<spark-tailscale-name>:3000`.
      *Observable:* the login page loads. **If it does not, the linger setting from Phase A did not take** — this is the single most common headless-machine surprise, and finding it now is much better than finding it a month from now.
- [ ] **Reachable from every device you actually use.** Try your laptop, your phone, and any colleague's machine that needs access.
      *Observable:* each one loads the login page.

---

# Section 5 — Known failure modes

For each: what you will see, what it actually means, what to do.

---

### 1. The CUDA 12 versus CUDA 13 library error

**What you see**

```
ImportError: libcudart.so.12: cannot open shared object file: No such file or directory
```

or one of its relatives — `libcublas.so.12`, `libcudnn.so.8`, `libnvrtc.so.12`.

**What it means in plain English**

A piece of software was built expecting CUDA version 12's support files. Your
machine has CUDA 13's. The file names contain the version number, so the
program looks for `...so.12`, finds only `...so.13`, and gives up. It is
strictly a name-and-version mismatch, not a broken installation — and it fails
at the moment the program starts, not when it was installed, which is why it
can install "successfully" and then refuse to run.

**What to do**

First, work out what is actually asking for it — because **nothing in this
repo should ever produce this error.** I checked every dependency: none of them
link to CUDA. So if you see this, one of three things is true:

1. **It came from Ollama.** By far the most likely. Ollama ships its own copy
   of the CUDA support files. Reinstall it with the official script so it picks
   up a build matched to your hardware, or use NVIDIA's container image for the
   Spark instead — DGX OS has the NVIDIA container runtime preinstalled.
2. **You installed something extra** — PyTorch, a GPU build of onnxruntime, or
   similar — while experimenting. Install the version built for CUDA 13, or the
   processor-only version. For PyTorch specifically, that means selecting the
   correct index URL rather than plain `pip install torch`.
3. **You copied a Python environment from the old machine.** Delete
   `auc/backend/venv` and let `setup.sh` rebuild it.

Useful diagnosis: `ldd <path-to-the-failing-file> | grep "not found"` lists
every support file it wants but cannot find.

---

### 2. The whole machine freezes when a model loads

**What you see**

Nothing. The screen stops responding, or your SSH session hangs. Possibly the
fans spin up. Eventually it may recover on its own, or it may not.

**What it means in plain English**

The processor and the graphics hardware share one 128 GB pool of memory. On a
conventional machine with separate graphics memory, an oversized model gets
killed and everything else survives. Here, a model that does not fit starves
the operating system itself, so instead of one program dying, everything stops
at once — including whatever you would normally use to fix it.

**What to do**

- Hold the power button. There is usually no graceful option once it reaches
  this state.
- On restart, load a smaller model, and watch memory while it loads:
  `watch -n1 free -h` in a second terminal.
- Build up gradually rather than jumping straight to the largest model you are
  curious about. Note the largest size that loads with room to spare.
- Leave real headroom. The operating system, the web server and your desktop
  session all draw from the same pool.
- Prevention: keep only one model loaded. `ollama ps` shows what is currently
  resident; `ollama stop <model>` unloads one.

---

### 3. Summaries generate, but very slowly

**What you see**

Text appears a word at a time, far slower than on the old machine. No error.

**What it means**

The model is running on the processor instead of the graphics hardware.
Everything works — it is just doing the arithmetic the slow way.

**What to do**

- Confirm it: `ollama ps` while generating. The `PROCESSOR` column will say CPU
  or show a split like "40%/60% CPU/GPU".
- Check the graphics hardware is visible at all: `nvidia-smi`.
- Check Ollama's own view: `journalctl -u ollama -n 50` — it logs at startup
  what hardware it found, and often says plainly why it fell back.
- If the model is simply too large to fit, a partial offload happens silently.
  Try a smaller one and see whether it reports 100% GPU.
- If no model reports GPU use, Ollama's graphics support is not working on this
  chip — reinstall it, or use NVIDIA's container image.

---

### 4. Summary generation fails with "Could not route evidence"

**What you see**

An error in the browser where the report should be, saying
`Could not route evidence: ...`. No sections render at all.

**What it means in plain English**

The ACGME search index is unavailable, so the generator stopped rather than
producing an ungrounded report. **This is the new, better behaviour** — before
the `study-branch` merge it would have quietly given you a weaker summary
instead, which was far more dangerous. The error text after the colon names the
real cause.

**What to do**

Read the rest of the message, and check the log for `RETRIEVAL UNAVAILABLE`:
`grep RETRIEVAL auc/data/logs/summary_validation.log`. Then, in order of
likelihood:

- The index was never built → `auc/backend/venv/bin/python auc/rag/build_index.py`
- `qwen3-embedding:0.6b` is not pulled → `ollama pull qwen3-embedding:0.6b`
- `chromadb` did not install → see failure 5
- The collection exists but is empty → rebuild the index

Verify the fix by generating a summary and watching for the `RUN START` line
with a populated `routing=` map.

---

### 4b. Summaries generate, but the ACGME grounding is subtly wrong

**What you see**

The report renders, all 21 sections appear, but the assessments do not match the
evidence — a comment about communication reasoned about under a patient-care
sub-competency, milestone language that does not fit what was written.

**What it means**

Retrieval is working mechanically but returning the wrong material. The usual
cause is an embedding model mismatch: the index was built with one model and is
being searched with another. Numbers from two different models are not
comparable, so the "nearest" match is effectively arbitrary. **Nothing errors** —
this is the one silent failure that survives the merge.

**What to do**

- Confirm which model is configured versus which built the index. It is named in
  `config.py`, `rag_retrieval.py`, `build_index.py` and `test_query.py` — change
  plan item 5 exists to collapse those to one place.
- Sanity-check retrieval directly:
  `auc/backend/venv/bin/python auc/rag/test_query.py "missed a posterior circulation stroke"` —
  the top hit should be plausibly about clinical reasoning, not a random
  professionalism entry.
- If in any doubt, rebuild: `ollama pull qwen3-embedding:0.6b` then
  `python auc/rag/build_index.py`. Rebuilding costs a minute and removes the
  question entirely.

---

### 5. `chromadb` will not install

**What you see**

```
ERROR: Could not find a version that satisfies the requirement onnxruntime>=1.14.1
ERROR: No matching distribution found for onnxruntime
```

**What it means**

`onnxruntime` is the one dependency published only as prebuilt files, with no
source fallback — so if no file exists for your exact combination of processor
and Python version, installation stops dead. ARM builds exist for Python 3.10
and up only.

**What to do**

- Check your version: `python3 --version`.
- **3.11 or newer:** this should not happen; read the full error, as the real
  cause is likely different.
- **3.10:** the installer should step back to an older onnxruntime by itself.
  If it does not, ask for one explicitly: `pip install "onnxruntime<1.24"`.
- **3.9 or older:** install a newer Python and rebuild the environment.
- **If you cannot make it work at all:** the application still runs without it.
  You lose ACGME grounding, not the app. Install everything except chromadb and
  carry on; the fallback prompt takes over. Sort it out later.

---

### 6. `exec format error`

**What you see**

```
bash: .../venv/bin/python: cannot execute binary file: Exec format error
```

or from npm:

```
Error: Cannot find module @rollup/rollup-linux-arm64-gnu
```

**What it means**

You have brought programs compiled for Intel/AMD onto an ARM machine. They are
valid files; they are simply instructions this processor does not speak. This
is the single most common ARM migration mistake, and it is entirely benign once
you spot it.

**What to do**

Delete and rebuild — never copy these directories between machines:

```bash
rm -rf auc/backend/venv auc/frontend/node_modules auc/frontend/dist
bash auc/setup.sh
```

---

### 7. The app is unreachable after a reboot

**What you see**

It worked yesterday. After a restart, the page will not load. But if you plug
in a monitor and log in, it springs to life.

**What it means**

The app runs as a *user* service, which by default only starts once that user
logs in. On a headless machine nobody ever logs in, so it never starts. Logging
in at the console is what starts it — which is exactly why it looks like
plugging in the monitor "fixed" it.

**What to do**

```bash
loginctl enable-linger $USER
```

Confirm with `loginctl show-user $USER -p Linger` → `Linger=yes`, then reboot
and test again without logging in.

---

### 8. Tailscale connects but the app is unreachable over hospital wifi

**What you see**

`tailscale status` looks healthy, `tailscale ip -4` gives an address, but
`http://<spark-name>:3000` times out from your laptop.

**What it means**

Several possibilities, worth separating:

- **Client isolation.** Many hospital networks stop devices on the wifi talking
  directly to each other. Tailscale routes around this by relaying through its
  own servers, which works — just more slowly.
- **The machine never fully joined the network.** A sign-in page may be waiting.
- **The app is listening but a firewall is blocking the port.**

**What to do**

- `tailscale ping <spark-name>` from the laptop. `via DERP` means relayed;
  `direct` means peer-to-peer. Both should work for a text-based app like this.
- On the Spark, confirm the app answers locally: `curl -s localhost:3000/api/auth/status`.
  If that works, the app is fine and the problem is purely network.
- Check the firewall: `sudo ufw status`. If active, allow the Tailscale
  interface: `sudo ufw allow in on tailscale0`.
- **Consider Tailscale Serve**, which gives you proper HTTPS with no
  certificate work: `tailscale serve --bg 3000`. The app then answers on
  `https://<spark-name>.<your-tailnet>.ts.net` with a real certificate, and you
  can stop exposing port 3000 to the hospital wifi entirely. *I have not tested
  this against your setup — verify on arrival.* If it works, it is the single
  best security improvement available for the effort involved, because right now
  the app listens on every network interface over plain unencrypted HTTP.

---

### 9. The page loads but looks wrong, or hangs while loading

**What you see**

The layout appears in the wrong typeface, or the page pauses for several
seconds before rendering.

**What it means**

`frontend/index.html` fetches two typefaces from `fonts.googleapis.com` every
time the page loads. On a network that blocks or slows outside connections, the
browser waits, then gives up and uses a fallback.

**What to do**

Cosmetic — the app works regardless. The permanent fix is to bundle the fonts
with the app; see the change plan.

---

### 10. Login succeeds, then immediately bounces back to the login page

**What you see**

You enter the password, the page reloads, and you are asked to log in again.

**What it means**

The login cookie is not being kept. Either the browser is refusing it, or you
are reaching the app by an address that differs from the one the cookie was
issued for.

**What to do**

- Use one consistent address — do not mix `localhost`, the Tailscale name and a
  raw IP address in the same session.
- Clear cookies for the site and try once more.
- Check the server actually issued a session: `sqlite3 auc/data/auc.db "select count(*) from sessions"` should be non-zero after a successful login.
- If you added HTTPS via a proxy, make sure it forwards cookie headers intact.

---

### 11. `git pull` refuses to run, or the database reverts

**What you see**

```
error: Your local changes to the following files would be overwritten by merge:
        auc/data/auc.db
```

**What it means**

The database file was tracked in git, and your live one had diverged from the
committed copy, so git would not proceed. **Do not "fix" this by discarding your
changes** — your changes are your data.

Since commit `f0c3b5a` the database is untracked, so this should no longer
happen. It is kept here because the instinct it warns against — reaching for
`git checkout .` or `git stash` to clear a complaint about a data file — is the
dangerous part, and that instinct outlives the specific bug.

**What to do**

Back up first, always:

```bash
cp auc/data/auc.db ~/auc-db-safety-copy.db
```

Then resolve it.

**This should no longer happen.** Commit `f0c3b5a` untracked the database, so git
has no opinion about it any more. If you do see this error, something has
re-added the file — check with
`git ls-files auc/data/`, which should return nothing.

---

### 12. Every section comes back "generation failed" or "insufficient evidence"

**What you see**

The report renders all 21 cards, but most or all of them say
`generation_failed`, or the narratives vanish and sections are marked
insufficient evidence.

**What it means in plain English**

The model is not returning usable JSON. The new generator asks the model for a
structured answer — a level, a narrative, and quotes — and validates it. Two
things can cause wholesale failure:

- **The model does not honour Ollama's JSON-constrained mode.** The generator
  sets `format: json` (`USE_OLLAMA_JSON_FORMAT = True` in `summary_builder.py`).
  Most models handle this; some do not, and produce prose or malformed output
  instead.
- **The model is quoting loosely.** Every quote must appear *verbatim*
  (case-insensitive) in the comments routed to that sub-competency. A model that
  paraphrases has all its quotes dropped, and when none survive, the narrative is
  discarded too. That is the safety mechanism doing its job — unsupported text
  never reaches you — but it looks like total failure.

**This matters specifically for your plan to move to a larger model.** Nemotron
or anything else new must be verified against both behaviours before you trust
it in a meeting.

**What to do**

- Read the log — it records the actual model output:
  `tail -50 auc/data/logs/summary_validation.log`
- If the output is prose rather than JSON, set `USE_OLLAMA_JSON_FORMAT = False`
  in `summary_builder.py` and retry; the defensive parser may cope where the
  constraint does not.
- If quotes are being dropped because the model paraphrases, that is a model
  choice problem, not a bug. Try a different model. Do **not** relax the
  validation — it is what keeps invented evidence out of a resident's record.
- Fall back to `clinical-reasoning:latest`, which you have already verified
  works with this pipeline.

---

### 13. A summary run takes far longer than expected, or appears to stall

**What you see**

The progress counter creeps — "3 of 21" — then seems to stop for minutes.

**What it means**

The generator makes **one model call per sub-competency with evidence**, up to
21 of them, sequentially. Each call has a 600-second timeout
(`OLLAMA_TIMEOUT_SECONDS` in `summary_builder.py`). A slow model, or one running
on the processor rather than the graphics hardware, multiplies that across every
section. It is not stalled; it is working through them.

For reference: you measured a full 21-section run at 3 minutes 11 seconds on a
27-billion-parameter model with graphics acceleration working.

**What to do**

- Confirm the graphics hardware is actually being used: `ollama ps` should say
  100% GPU. If it says CPU, see failure 3 — that alone can turn three minutes
  into an hour.
- Confirm the model stays loaded between calls rather than being unloaded and
  reloaded 21 times. `ollama ps` during a run should show it resident
  throughout.
- `num_ctx` is set to 8192. A larger context costs memory per call; on shared
  memory that is worth watching with `free -h` during a run.

---

# Section 6 — Rollback

The old machine stays running and untouched until the Spark is proven. This
section defines "proven" concretely, so the decision is a checklist rather than
a feeling.

## The rule while both machines are alive

**Only one machine may accept new data at a time.**

This is the part that goes wrong quietly. If you enter notes on the Spark on
Monday and on the old machine on Tuesday, you end up with two databases that
have both moved forward in different directions. There is no merge tool for
this — the application has no concept of reconciling two histories. You would
have to re-type one side by hand.

**For study data this is worse than inconvenient.** CCC session rows, the
retrieval timings and the action-item recall checks are measurements of a
specific meeting at a specific moment. Notes can be re-typed; a split
measurement cannot be reconstructed, and half a cycle recorded on each machine
may not be analysable at all. Decide which machine runs a given CCC meeting
*before* the meeting starts, not during it.

So: from the moment you start entering real notes on the Spark, treat the old
machine as **read-only** — running, reachable, ready to take over, but not
receiving new work. If you do need to fall back, you fall back to the database
as it was at cutover, plus whatever you can carry back by hand.

## What "proven" means for this project

The Spark is proven when **all** of the following hold:

**Function — everything the app does, actually done**

- [ ] All residents present, at correct PGY levels, with photos rendering
- [ ] A note added on the Spark survives a restart of the app
- [ ] A full 21-section summary completes, in time comparable to or better than the old machine, with the progress counter reaching 21
- [ ] The validation log shows a `RUN START` line with a populated `routing=` map on every summary generated during the trial, and no `RETRIEVAL UNAVAILABLE`
- [ ] Section ids match `acgme_ontology.json` exactly — 21 sections, none invented
- [ ] Quote validation is dropping unsupported quotes rather than discarding every section wholesale (failure mode 12)
- [ ] A summary approved and exported to PDF, with correct characters
- [ ] The advancement wizard runs end to end (and its undo works)
- [ ] A MedHub CSV import completes, if you use that feature
- [ ] Every device that needs the app can reach it — your laptop, your phone, any colleague

**Durability — it survives the things that happen to computers**

- [ ] Survives a reboot with nobody logged in, twice on separate days
- [ ] The nightly backup has run **seven consecutive nights** without failure (`journalctl --user -u auc-backup.service` shows seven successes)
- [ ] Backups are landing somewhere off the machine, and you have confirmed they appear there
- [ ] **A restore drill has been done:** take a backup zip, restore it into a scratch copy, open it, confirm resident and note counts match. A backup you have never restored is not a backup.

**Real use — the only test that counts**

- [ ] **Two complete CCC meetings run on the Spark**, start to finish, with no fallback to the old machine. Not a rehearsal — actual use, with real notes, under real time pressure.
- [ ] During those meetings, nothing needed a terminal to fix.
- [ ] **If the recall QI study is still collecting:** both meetings captured study data correctly — sessions opened and closed, spontaneous-input tapped per resident, the retrieval clock recorded, and the three de-identified CSVs exported and inspected afterwards. A migration that silently breaks study capture costs you a data collection cycle you cannot re-run, so this is a hard gate, not a nice-to-have.

**Quality — the silent-failure guard**

- [ ] For at least three residents, you have compared a Spark-generated summary against an old-machine summary and judged the Spark's to be as good or better. This guards specifically against failure mode 4, where everything appears to work but the output has quietly degraded.

## Only then

When every box above is ticked:

1. Take a final full backup from the old machine and keep it somewhere separate — independent of both machines.
2. Take a full backup from the Spark and confirm it restores.
3. Stop the app on the old machine: `systemctl --user stop auc && systemctl --user disable auc`
4. **Leave the old machine's data in place for at least another month.** Disabling the service costs nothing and is instantly reversible. Wiping the disk is not.
5. Only after that month, with the Spark's backups running reliably throughout, consider the old machine free.

## If you need to roll back

Because the old machine has been left running and untouched, rollback is:

1. `systemctl --user start auc` on the old machine
2. Point your browser back at the old address
3. Re-enter by hand whatever was recorded on the Spark since cutover

That last step is the cost of the split-brain problem, and it is precisely why
the read-only rule above matters. The shorter the trial period, and the more
disciplined you are about where notes get typed, the smaller that cost.
