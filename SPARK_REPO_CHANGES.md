# Repo change plan for the DGX Spark migration

Companion to [`SPARK_MIGRATION.md`](SPARK_MIGRATION.md). That document is about
the *machine*. This one is about the *code* — the changes the repository itself
needs so it runs well on the Spark.

> **Revised after the `study-branch` merge.** This plan was first written against
> `main` at `ebf9ad0`. `study-branch` has since been merged, which **completed
> item 1 outright** and changed the reasoning behind items 2 and 4. Those three
> entries are rewritten below; the rest stand as originally written.

> **Revised again: items 1–10 are now built.** Everything in the DO NOW group
> has been implemented and verified on a machine, plus a test suite, linting,
> and two scripts the original plan did not call for — `check.sh` and
> `capture-environment.sh`. Each entry below records what was actually done
> and where it differed from the proposal. **Items 11–13 remain open**; they
> need the Spark in front of you.

## How this is ordered

**Items 1–10 can be done now, before the hardware arrives.** They are marked
**DO NOW**. Every one of them is designed to keep working on your current Ubuntu
machine — you are not making a one-way jump, you are making the repo able to run
on both. Several of them exist specifically to make arrival day shorter or to
turn a silent failure into a visible one.

**Items 11–13 need the Spark in front of you** and are marked **ON ARRIVAL**.

Within the DO NOW group, items were ordered by value-per-effort. All of them
are now done, so that ordering is history.

### Reading the entries

- **Goal** — what changes, in one sentence
- **Why** — what breaks, or stays broken, without it
- **Files touched**
- **Done when** — a check you can run yourself and see the result of
- **Risk to the current machine** — whether this can disturb the working deployment

---

# DO NOW — before the hardware arrives

---

## 1. ~~Stop tracking the database file in git~~ — ✅ DONE

**Completed by commit `f0c3b5a` on `study-branch`, now merged into `main`.**
Nothing left to do. Kept here because the reasoning explains a class of bug
worth recognising again, and because what was actually built went further than
this plan proposed.

**What was done, beyond the original proposal:**

- `auc/data/auc.db` untracked with `git rm --cached`, so the existing `data/`
  ignore rule finally applies. `git ls-files auc/data/` now returns nothing.
- A **root** `.gitignore` was added. The original plan missed that
  `auc/.gitignore` only governs paths under `auc/`, leaving the repository root
  uncovered entirely.
- The ignore rules were widened to the artifacts that actually carry resident
  data in real use, which the original plan did not enumerate: MedHub CSV
  exports, backup `.zip` archives, exported summary PDFs, stray database copies,
  and the SQLite `-wal`/`-shm` sidecars that can hold rows not yet flushed to
  the main file.
- `.claude/settings.local.json` is now ignored in-repo rather than relying on a
  machine-global ignore file, so it is covered on any other checkout.
- Both ignore files carry the command that detects this class of bug:
  `git ls-files | while read f; do git check-ignore -q "$f" && echo "$f"; done`

**Verify it on your machine:** `git ls-files auc/data/` returns nothing, and
`ls auc/data/auc.db` still finds the file.

**One consequence to absorb:** a fresh clone now arrives with **no database**,
and the app creates an empty one on first run. That is correct, and it makes
arrival day less ambiguous — but it also means nothing in git will ever remind
you the database exists. Your backup is now the only thing protecting it.

<details>
<summary>Original entry, kept for the reasoning</summary>

**Goal.** Remove `auc/data/auc.db` from git's tracking so that git stops trying
to manage your live database, while leaving the file itself exactly where it is
on disk.

**Why.** *(As it stood before the merge:)* git tracked a stale snapshot of your
database — 44 residents, placeholder notes, no password configured. Your live database has diverged from
it completely. Three separate problems follow from this:

- On the Spark, a fresh clone hands you the stale file. It looks like a working
  database. It is not, and the mistake is easy to make and hard to spot.
- On either machine, `git pull` will refuse to run while the live file differs
  from the committed one — and the obvious-looking way out of that error
  (`git checkout .`, `git stash`) **destroys your data**.
- Every commit stores another full copy of whatever the database held at that
  moment, inside a repository pushed to GitHub.

You have confirmed the resident names are public information and the notes and
summaries are fake, so this is not a disclosure problem today. It becomes one
the moment you start entering real committee notes on the Spark — and by then
the habit is set and the file is still tracked. Fix it while it is harmless.

Note that `auc/.gitignore` already contains `data/`. The file is tracked only
because it was committed *before* that rule was added, and gitignore does not
apply to files git is already tracking. So this is finishing a job that was
started, not introducing a new policy.

**Files touched.** Git's index only. `auc/.gitignore` already covers it. No
source file changes. Optionally a note in `auc/README.md` explaining that a
fresh install starts with an empty database.

**The change.** `git rm --cached auc/data/auc.db`, then commit. The file stays
on disk on whatever machine you run it on.

**Done when.**
- `git ls-files auc/data/` returns nothing
- `ls auc/data/auc.db` still finds the file
- `git status` shows a clean tree — the database no longer appears as modified

**Risk to the current machine. ⚠ Real, but easily managed — read this.** When
the old machine pulls this commit, git sees a tracked file being deleted. Because
your local copy differs from the committed one, git will most likely refuse and
print an error rather than delete anything. But the safe sequence is not
optional:

```bash
# on the old machine, BEFORE pulling
systemctl --user stop auc
cp auc/data/auc.db ~/auc-db-safety-copy.db
cp -r auc/data/photos ~/auc-photos-safety-copy
git pull
# confirm the database is still there and intact:
sqlite3 auc/data/auc.db "select count(*) from residents"
systemctl --user start auc
```

If the file did vanish, copy the safety copy back. Take the safety copy even if
you are confident. Especially if you are confident.

</details>

---

## 2. ~~Split the dependencies so the core app installs without the index layer~~ — ✅ DONE

**Built as proposed.** `chromadb` moved to `auc/backend/requirements-rag.txt`;
`setup.sh` installs the core first and the index layer second, and a failure of
the second is a warning naming the lost feature rather than an aborted install.
`aiofiles` dropped and `anyio` added in the same commit (item 9).

<details>
<summary>The original proposal</summary>

## 2. Split the dependencies so the core app installs without the index layer

**DO NOW**

**Goal.** Move `chromadb` out of `requirements.txt` into a second file,
`requirements-rag.txt`, and have `setup.sh` install the core first and the
index layer second — treating a failure of the second as a warning rather than
a fatal error.

**Why — and note this reasoning changed with the merge.** `chromadb` is the only
Python package here with compiled parts, which makes it the only one that can
plausibly fail on ARM. I confirmed an ARM build is published, so this probably
will not happen — but the consequence of being wrong is out of proportion to the
cause. Today, `setup.sh` runs with `set -e`, so a single failing package aborts
the entire install: no web server, no database, no app at all.

**The original justification no longer holds.** I first argued this was safe
because the app degrades gracefully without the index — `rag_retrieval.py` raises
`RagUnavailable` and the old generator fell back to an ungrounded prompt. The
`study-branch` merge removed that fallback from the path the interface uses:
`summary_builder.generate_report()` now emits an error and stops. So without
`chromadb` you do not get weaker summaries, you get **no summaries**.

That makes the split *more* worth doing, not less, but for a different reason.
The point is no longer "summaries keep working" — it is that a failure in the
index layer should cost you **one feature, visibly**, rather than the entire
installation. Residents, notes, follow-ups, the CCC study drawer and PDF export
of already-approved summaries do not need `chromadb` at all, and none of them
should be collateral damage.

It also makes the dependency list honest about what is core and what is an
enhancement — useful well beyond this migration.

**Files touched.** `auc/backend/requirements.txt` (remove chromadb), new
`auc/backend/requirements-rag.txt`, `auc/setup.sh` (second install step, failure
tolerated and reported), `auc/rag/README.md` (update the install line).

**Done when.**
- Temporarily rename `requirements-rag.txt`, run `setup.sh`, and the app still
  starts and serves the login page
- In that state, residents, notes, follow-ups and the CCC drawer all work
- Generating a summary in that state returns a **clear error naming the missing
  index** — not a crash, not a blank page, and not a silently worse summary
- Restore the file, re-run `setup.sh`, and summaries work again

**Risk to the current machine. None.** Same packages, installed in two steps
instead of one. Re-running `setup.sh` on the old machine is a no-op for anything
already installed.


</details>

---

## 3. ~~Add a preflight script that checks everything in one command~~ — ✅ DONE

**Built, with one deliberate change.** `auc/preflight.sh` checks everything the
entry listed, but **does not check for a named generation model**. The browser
sends the model picked in Settings with every generation request, so any model
in `ollama list` works — preflight lists what is installed and fails only if
there is nothing at all. The *embedding* model is still checked by exact name,
because that one must match the index.

It calls the app's own `index_status()` rather than reimplementing the check, so
preflight and Settings can never disagree. It reads the database with Python's
`sqlite3` rather than the `sqlite3` command, which is not installed by default
on a fresh machine.

Verified by breaking things one at a time: renaming `rag/chroma_db` makes that
check alone fail with the rebuild command.

<details>
<summary>The original proposal</summary>

## 3. Add a preflight script that checks everything in one command

**DO NOW · saves the most time on arrival day**

**Goal.** A single script — say `auc/preflight.sh` — that checks every
environmental assumption the app makes and prints a clear pass/fail line for
each.

**Why.** Section 4 of the migration document is a long list of individual checks.
Most of them are one command each, and on arrival day you will be typing them
while also unboxing hardware and arguing with hospital wifi. A script does them
all in two seconds and tells you which one failed.

It also pays for itself afterwards. Whenever something stops working, "run
preflight" is a far better first instruction than "read the manual again".

What it should check:

- Processor family (`uname -m`) and report it plainly
- Python version, with an explicit warning below 3.11
- Node version
- Whether the virtual environment exists and every required package imports —
  `chromadb` in particular, since installing and importing are different things
- Whether `frontend/dist/index.html` exists
- Whether Ollama answers, and list the models it has
- Whether the two required models are present by exact name: the generation
  model, and `qwen3-embedding:0.6b`
- Whether the ACGME index directory exists and holds the expected 42 entries
- Whether the embedding model configured matches the one that built the index
  (once item 5 stamps it in)
- Whether `auc/data/logs/` is writable, since the summary validator logs there
- Whether the database exists, is readable, and has a password configured
- How many photos exist, versus how many residents claim to have one
- Whether DejaVu fonts are installed
- Whether the systemd services and the backup timer are enabled
- Whether lingering is enabled
- Whether the app answers on its port

**Files touched.** New `auc/preflight.sh`. A line in `auc/README.md`.

**Done when.** Running `bash auc/preflight.sh` on the *current* machine prints a
list of checks, all passing. Then deliberately break one — `ollama stop` your
model, or rename `chroma_db` — and confirm that check alone reports a failure
with a message explaining what to do.

**Risk to the current machine. None.** It only reads; it changes nothing.


</details>

---

## 4. ~~Surface index health *before* a generation is attempted~~ — ✅ DONE

**Built as proposed.** `GET /api/rag/status`, backed by
`rag_retrieval.index_status()`, shown in Settings beside the Ollama line.

Five states, all exercised by tests: ready; index missing; index empty; index
incomplete; and the embedding-model mismatch, reported as fatal because it is
the one failure that produces no error of its own.

One state the plan did not anticipate: an index built *before* the stamping in
item 5 carries no model name. That reports as a **warning saying to rebuild
once**, rather than as a mismatch — which is the state your current Ubuntu
machine's index is in, so expect to see it.

The expected entry count is derived from the ontology rather than hard-coded at
42, so editing the ontology cannot make the status line lie.

Two UI defects turned up while looking at the real page and were fixed: the
"Ollama not reachable" message overran the label beside it, and the index
message wrapped mid-model-name.

<details>
<summary>The original proposal</summary>

## 4. Surface index health *before* a generation is attempted

**DO NOW · scope reduced — the merge fixed most of this**

**Goal.** Add an endpoint — `GET /api/rag/status` — reporting whether the ACGME
index is usable, and show it in Settings next to the existing Ollama status.

**Why — most of the original problem is now solved.** I originally described the
silent grounding downgrade as the single worst failure in the system: summaries
kept appearing, quietly worse, with one line in a log as the only notice.

`study-branch` fixed that. `generate_report()` now raises on `RagUnavailable` and
emits an error event, so a broken index produces a visible failure in the
browser, and the run logs `RETRIEVAL UNAVAILABLE` to
`auc/data/logs/summary_validation.log`. Per-section status
(`ok` / `no_evidence` / `insufficient_evidence` / `generation_failed`) is already
recorded and rendered. The per-summary provenance I proposed is effectively
there.

**What remains is narrower but still worth having:** you currently only discover
the index is broken *by trying to generate a summary* — which, in a committee
meeting, is the worst possible moment. A status line in Settings tells you
beforehand, the same way the Ollama status line already does. On arrival day it
also gives you a one-glance check that does not require generating a report.

**One silent failure does survive** and this endpoint should cover it: an
embedding-model **mismatch**. A missing model now errors, but a *different* model
returns confident nonsense with no error at all. If item 5 stamps the model name
into the index, this endpoint can compare it against the configured one and say
so.

**Files touched.** `auc/backend/rag_retrieval.py` (a status-check function),
`auc/backend/app.py` (the endpoint), `auc/frontend/src/api.js`,
`auc/frontend/src/pages/Settings.jsx` (a status line). No database change needed
any more — the merge already records per-section status.

**Done when.**
- `curl -s localhost:3000/api/rag/status` reports healthy, naming the collection
  and entry count
- Settings shows a green line saying the ACGME index is ready
- Rename `auc/rag/chroma_db` temporarily: the endpoint reports unavailable with
  the reason and Settings shows a clear warning — **without** having to attempt
  a generation to find out
- With item 5 done, pointing `AUC_EMBED_MODEL` at a different model makes the
  endpoint report a mismatch

**Risk to the current machine. Very low.** Purely additive: one new endpoint and
one read-only display. No existing path changes behaviour. Worth running on the
old machine for a week first, so you arrive knowing what healthy looks like.


</details>

---

## 5. ~~Make the embedding model configurable, and stamp it into the index~~ — ✅ DONE

**Built, going slightly further than proposed.** `config.EMBED_MODEL`
(`AUC_EMBED_MODEL`, default unchanged), read by `build_index.py`,
`rag_retrieval.py` and `test_query.py`. The name is stamped into the Chroma
collection's own metadata alongside `hnsw:space`.

The plan's "done when" allowed a fallback default in each file. That was
dropped: a second default per file is exactly how the two drift apart, so each
file puts `backend/` on the path and imports config for real. `grep -rn
"qwen3-embedding" auc/ --include=*.py` now returns `config.py` and nothing else.

**Something the plan missed, found while testing:** a running app holds the
index it opened at startup, so rebuilding the index while the app is up leaves
it searching — and reporting on — the old one. `build_index.py` now says to
restart, and `rag/README.md` records it.

<details>
<summary>The original proposal</summary>

## 5. Make the embedding model configurable, and stamp it into the index

**DO NOW**

**Goal.** Read the embedding model name from configuration instead of hard-coding
it in three separate files, and store the name used inside the index itself so a
mismatch can be detected.

**Why.** `qwen3-embedding:0.6b` is written literally in `build_index.py`,
`rag_retrieval.py` and `test_query.py`. Two problems follow.

First, if you change it in one place and not the others, the index gets built
with one model and searched with another. Embeddings from different models are
not comparable — searching one with the other returns arbitrary results. There
is no error. Retrieval simply becomes noise, and the summaries that follow are
grounded in ACGME material chosen at random. That is arguably worse than no
grounding at all, because it looks right.

Second, you have said you plan to move to a much larger generation model on the
Spark. That is the generation model rather than the embedding model, but it is
exactly the kind of moment when someone reasonably thinks "while I'm here, let me
upgrade the embedding model too" — and that is when this bites.

Storing the model name in the index's own metadata means the app can compare
what it is about to search with what the index was built with, and say so.

**Files touched.** `auc/backend/config.py` (add `EMBED_MODEL`),
`auc/backend/rag_retrieval.py`, `auc/rag/build_index.py`, `auc/rag/test_query.py`,
`auc/rag/README.md`.

**Done when.**
- `grep -rn "qwen3-embedding" auc/` returns hits only in `config.py` and
  documentation
- Rebuilding the index still produces 42 entries
- Setting `AUC_EMBED_MODEL` to a different name and restarting causes the status
  endpoint from item 4 to report a mismatch, rather than silently returning
  nonsense

**Risk to the current machine. Low**, provided the default stays
`qwen3-embedding:0.6b`. Behaviour is unchanged unless the variable is set.


</details>

---

## 6. ~~Bundle the web fonts instead of fetching them from Google~~ — ✅ DONE

**Built as proposed.** Both families are SIL Open Font License 1.1 — confirmed
against the upstream projects, not assumed — and the full licence text ships in
`frontend/public/fonts/LICENSE.md`.

Google's per-weight URLs turn out to be byte-identical: these are variable
fonts. So this ships one file per family/style/subset with a weight range
rather than four identical copies — 8 files, 460 KB, latin and latin-ext.

Verified in a browser with every non-local request aborted: the page renders
with no external request attempted at all, and measured glyph widths differ
from the Georgia and system-sans fallbacks, so the real faces are in use.

Worth knowing: in a network that blackholes rather than refuses the request,
the blocked font fetch also stalls the page's own load event. That was visible
in this container.

<details>
<summary>The original proposal</summary>

## 6. Bundle the web fonts instead of fetching them from Google

**DO NOW**

**Goal.** Download the two typefaces into the repository and serve them from the
app, rather than loading them from `fonts.googleapis.com` on every page view.

**Why.** Three reasons, in increasing order of importance:

- **It may be blocked.** A hospital network may not permit the request, or may
  slow it enough that the page visibly stalls before falling back.
- **It is an outbound request from a clinical tool.** Every page load tells
  Google's servers that a browser at your hospital's address opened this
  application. Nothing confidential leaves — but on a tool that handles resident
  evaluations, a dependency on an outside service that earns you nothing is
  worth removing on principle.
- **Local is simply faster**, and the machine is meant to be self-contained.

The app is described in its own documentation as local-first. This is the one
place it is not.

**Files touched.** `auc/frontend/index.html` (drop the three external links),
new font files under `auc/frontend/public/fonts/`, `auc/frontend/src/styles.css`
(font-face declarations).

**Done when.**
- `grep -rn "googleapis\|gstatic" auc/frontend/` returns nothing
- Rebuild, then load the app with the network disconnected (or with the browser's
  developer tools set to block outside requests) — the page renders in the correct
  typefaces with no failed requests

**Risk to the current machine. None.** Purely cosmetic and purely local.
Check the fonts' licences permit redistribution — both of these are open
licensed, but confirm rather than assume.


</details>

---

## 7. ~~Make the address and port configurable~~ — ✅ DONE

**Built, resolved slightly differently.** `config.HOST` / `config.PORT`
(`AUC_HOST` / `AUC_PORT`), defaults unchanged. Rather than repeating the
defaults in the shell scripts, `run.sh` asks `config.py`, and the systemd unit
now runs `run.sh` — so the address is resolved the same way whether you start
it by hand or through systemd, and there is one place to change it.

Verified: `AUC_PORT=3001` answers on 3001; nothing set still answers on 3000;
and `AUC_HOST=127.0.0.1` answers on loopback while refusing on the machine's
network address.

<details>
<summary>The original proposal</summary>

## 7. Make the address and port configurable

**DO NOW · prerequisite for item 11**

**Goal.** Read the listening address and port from environment variables —
`AUC_HOST` and `AUC_PORT` — defaulting to today's `0.0.0.0` and `3000`.

**Why.** The address `0.0.0.0` means "accept connections on every network
interface". On the Spark, sitting on hospital wifi, that means anyone on that
wifi can reach the app. It is password-protected, but over plain unencrypted
HTTP, so the password crosses the network in a form that can be read.

The right arrangement is for the app to listen only on `127.0.0.1` — reachable
only from the machine itself — with Tailscale Serve providing HTTPS in front of
it (item 11). That is a small change and a large improvement.

But it is impossible today, because `--host 0.0.0.0 --port 3000` is written
literally in three places: `run.sh`, the generated systemd service inside
`setup.sh`, and the message `setup.sh` prints at the end. Making it configurable
now means item 11 becomes a one-line environment change on arrival day rather
than a code edit under time pressure.

**Files touched.** `auc/run.sh`, `auc/setup.sh` (both the generated `run.sh` and
the service definition), `auc/backend/config.py`, `auc/README.md`.

**Done when.**
- `AUC_PORT=3001 bash auc/run.sh` serves on 3001 — `curl -s localhost:3001/api/auth/status` answers
- With nothing set, it still serves on 3000 exactly as before
- `AUC_HOST=127.0.0.1` makes the app answer on localhost but *not* on the
  machine's network address — confirm from another computer

**Risk to the current machine. Low**, as long as the defaults are unchanged.
Re-running `setup.sh` rewrites the service file, so restart the service
afterwards and confirm it still answers.


</details>

---

## 8. ~~Make `setup.sh` safer to re-run and clearer when it fails~~ — ✅ DONE

**Built, and the service-file handling goes further than "warn clearly".**
Rather than warning that your edits will be replaced, `install_unit` carries
every `Environment=` line forward — your value beats the generated default, and
variables you added that the script does not know about are kept too — while
regenerating the paths, which have to change on a new machine. The previous
file is kept alongside and the script prints what it carried across. Verified
with a hand-edited `OLLAMA_MODEL` and an added `AUC_SUMMARY_MODEL`.

Also: Python version gate at step 1 (verified against a stub reporting 3.9),
Node version gate, disk space check, `loginctl enable-linger`, `npm ci` with
errors no longer discarded, and a clean exit with instructions on a machine
that has no systemd user session — where it previously aborted as if the
install had broken.

One more: the service is now **restarted** rather than started, so re-running
picks up new code instead of leaving the old process running.

<details>
<summary>The original proposal</summary>

## 8. Make `setup.sh` safer to re-run and clearer when it fails

**DO NOW**

**Goal.** Have the setup script check its assumptions up front, avoid silently
overwriting things you have customised, and enable lingering itself.

**Why.** You will run this script on a new machine, under time pressure, on an
unfamiliar architecture. Right now it has several sharp edges:

- **No Python version check.** It accepts any `python3`. On a version below 3.10,
  the chromadb install fails deep into step 2 with an error about `onnxruntime`
  that gives no hint the real problem is the Python version.
- **It overwrites your service files unconditionally.** If you have hand-edited
  `auc.service` — to change the model, or `AUC_BACKUP_DIR` — re-running the
  script silently reverts it. Since those settings exist nowhere else, that is a
  genuine loss.
- **It never enables lingering.** Your own `SECURITY.md` already flags this as
  needed for the headless Spark, and it is the most common headless failure
  there is. The script is the right place for it.
- **`npm install --silent 2>/dev/null` discards error output.** On an
  architecture where npm might legitimately complain, hiding the complaint is
  the opposite of helpful.
- **No disk space check** before downloading an environment and building a
  frontend.

**Files touched.** `auc/setup.sh` only.

**Done when.**
- Running it twice in a row produces no errors and no lost settings
- Hand-edit `OLLAMA_MODEL` in the service file, re-run, and your edit survives
  (or you are warned clearly that it will be replaced)
- On a machine with old Python, it fails at step 1 with a message naming the
  version problem — not at step 2 with a package error
- `loginctl show-user $USER -p Linger` prints `Linger=yes` afterwards

**Risk to the current machine. Low, but this is the one item that touches your
working deployment's startup path.** Re-run it on the old machine, then confirm
with `systemctl --user status auc` and by loading the app. Do it on a day when
you are not about to need the app, not the morning of a committee meeting.


</details>

---

## 9. ~~Remove the unused dependency~~ — ✅ DONE

Done in the same commit as item 2. `aiofiles` dropped, `anyio` added.

<details>
<summary>The original proposal</summary>

## 9. Remove the unused dependency

**DO NOW · two minutes**

**Goal.** Delete `aiofiles==24.1.0` from `requirements.txt`, and add `anyio`.

**Why.** Two opposite errors in the same file.

`aiofiles` is pinned but **imported nowhere** — I checked every Python file. It
is a leftover. It installs cleanly on ARM so it does no harm, but every unused
entry is one more thing to investigate when something breaks on an unfamiliar
machine.

`anyio` is the reverse: `summary_builder.py` imports it directly (for
`anyio.to_thread.run_sync`), but it is **not listed**. It works today only
because FastAPI pulls it in as its own dependency. That is fragile in principle —
a future FastAPI that stopped needing it would break summary generation with an
import error that looks unrelated to anything you changed. It is pure Python, so
there is no ARM risk; this is a correctness fix, not a migration risk.

**Files touched.** `auc/backend/requirements.txt`.

**Done when.** `grep -rn "aiofiles" auc/` returns nothing; `anyio` appears in
`requirements.txt`; and the app starts, serves, and completes a full summary run
from a freshly built environment.

**Risk to the current machine. None**, on the evidence — nothing imports it. To
be certain, remove it from a fresh environment rather than uninstalling it from
the running one, and confirm photo upload and backup download still work, since
those are the file-handling paths where a hidden dependency would surface.


</details>

---

## 10. ~~Document the environment variables in one place~~ — ✅ DONE

**Built, and the conclusion about the model changed.** `auc/.env.example`
documents all eleven variables, and a test fails if the code reads one that the
file does not mention.

The plan treated the `clinical-reasoning:latest` / `qwen3:8b` disagreement as
something to settle on a single value. Reading the code, that was the wrong
diagnosis: the model is chosen **per generation**, sent by the browser from
Settings, with `AUC_SUMMARY_MODEL` and then `OLLAMA_MODEL` as fallbacks. Any
model in `ollama list` works. So what needed writing down was the precedence
chain, not a winner — and that the choice lives in the browser, so it does not
travel with the machine.

<details>
<summary>The original proposal</summary>

## 10. Document the environment variables in one place

**DO NOW**

**Goal.** Add a `.env.example` file listing every environment variable the app
reads, with a comment for each, and reference it from the README.

**Why.** There are eight of them — `OLLAMA_URL`, `OLLAMA_MODEL`,
`OLLAMA_MAX_TOKENS`, `AUC_SUMMARY_MODEL`, `MEDHUB_API_URL`, `MEDHUB_API_KEY`,
`AUC_BACKUP_DIR`, `AUC_BACKUP_KEEP_DAYS` — plus any added by items 5 and 7. They
are documented across `config.py` comments, two READMEs, `BACKUPS.md`, the top of
`summary_builder.py` and the generated systemd files. On arrival day you want one
list.

`AUC_SUMMARY_MODEL` deserves particular attention: it is read in
`summary_builder.py`, **not** `config.py`, and it silently overrides
`OLLAMA_MODEL` for summary generation only. So the model writing your summaries
can differ from the one named in the service file, with nothing pointing that
out. That is exactly the kind of thing to have written down before you start
swapping in a larger model on the Spark.

It also makes a real discrepancy visible: `config.py` defaults `OLLAMA_MODEL` to
`clinical-reasoning:latest`, while `setup.sh` writes `qwen3:8b` into the service
file, and both READMEs say `qwen3:8b`. Whichever is right, the repository
currently disagrees with itself, and on the Spark you will want that settled —
particularly as you move to a larger model.

`.env.example` should contain **no real values** — placeholders only. It is
committed; it must never carry a credential. Your `.gitignore` already excludes
`.env` itself, which is correct.

**Files touched.** New `auc/.env.example`, `auc/README.md`, and a corrected
default or comment in `auc/backend/config.py`.

**Done when.** `.env.example` lists every variable that
`grep -rn "environ.get" auc/backend/` finds, and someone reading only that file
could configure the app.

**Risk to the current machine. None.** Documentation.


</details>

---

# ON ARRIVAL — needs the Spark in front of you

---

## 11. Put HTTPS in front of the app and stop listening on the open network

**ON ARRIVAL · depends on item 7**

**Goal.** Set `AUC_HOST=127.0.0.1` so the app accepts connections only from the
machine itself, and use Tailscale Serve to publish it over HTTPS on your
tailnet.

**Why.** Today the app listens on every interface over plain HTTP. On hospital
wifi that means the login page — and the password typed into it — are reachable
and readable by anyone on that network. Your own `SECURITY.md` lists HTTPS as a
recommended-but-not-done item, and notes the app is currently fine only because
it runs on a trusted network. The Spark on hospital wifi is not that.

Tailscale Serve is the least-effort route by a wide margin: one command,
a real certificate, no certificate renewal to manage, and the app becomes
reachable only to devices on your tailnet. The alternative — running Caddy or
nginx as a reverse proxy — also works and is well documented, but is more moving
parts for the same result.

**I have not verified Tailscale Serve against your setup.** Treat the command
below as a starting point to test, not an instruction that is known to work:

```bash
tailscale serve --bg 3000
tailscale serve status
```

**Files touched.** No source changes if item 7 is done — this is a setting in
the systemd service file. Update `auc/SECURITY.md` and `auc/README.md` to record
what was done.

**Done when.**
- `https://<spark-name>.<tailnet>.ts.net` loads the app with a valid certificate
  and no browser warning
- From another computer on the hospital wifi that is **not** on your tailnet,
  `curl http://<spark-lan-ip>:3000` refuses to connect
- Logging in over HTTPS works and the session persists

**Risk to the current machine. None** — this is a Spark-only configuration
change. Do not apply `AUC_HOST=127.0.0.1` to the old machine unless you also put
a proxy in front of it there, or you will lock yourself out of it.

---

## 12. Settle the model configuration

**ON ARRIVAL**

**Goal.** Once you have chosen and tested the larger model on the Spark
(Nemotron or otherwise), set it as the default in one place and update the
documentation to match.

**Why.** Several files name several different models, and `AUC_SUMMARY_MODEL`
can override all of them for summaries alone. After the migration you will have
another in mind. Leaving that unresolved means that in six months, when
something is generating summaries with a model you did not expect, you will have
no way to tell which setting won.

**Two things any replacement model must be verified against**, because the new
generator depends on both and neither is guaranteed:

1. **It must honour Ollama's JSON-constrained output** (`format: json`, set by
   `USE_OLLAMA_JSON_FORMAT` in `summary_builder.py`). A model that returns prose
   instead will mark every section `generation_failed`.
2. **It must quote verbatim.** Every quote is checked against the comments
   routed to that sub-competency; a model that paraphrases has its quotes
   dropped and its narratives discarded. Do not relax that validation to
   accommodate a model — it is what keeps invented evidence out of a resident's
   record. Change the model instead.

Verify both on a real resident with real notes before a meeting depends on it,
and keep `clinical-reasoning:latest` as the documented fallback.

Also worth recording in the README: which model was used, why it was chosen, and
what the fallback is if it stops working. Your fine-tune remains the known-good
fallback — write down that it exists and how to switch back to it.

**Files touched.** `auc/backend/config.py`, `auc/setup.sh`, `auc/README.md`,
`README.md`, `auc/.env.example`.

**Done when.** `grep -rn "OLLAMA_MODEL\|clinical-reasoning\|qwen3:8b" .` shows a
single consistent story, and a fresh `setup.sh` produces a service file naming
the model you actually intend to use.

**Risk to the current machine. Low**, but do not change the default to a model
the old machine does not have while the old machine is still your fallback.
Change it after cutover, or set it only through the environment.

---

## 13. Consider loosening the version pins

**ON ARRIVAL · optional, and only if something forces it**

**Goal.** If a pinned version turns out to have no ARM build, relax that one pin
to a compatible range.

**Why.** Every version is currently pinned with `==`, which is good practice —
it means both machines run identical code. Based on my checks, every pin
installs cleanly on ARM, so **the expected outcome is that this item is never
needed.** It is listed so that if you do hit a wall, you know that loosening a
pin is a legitimate response and not a desperate one.

If you do change a pin, change one, record why in a comment, and re-run the full
verification in Section 4 of the migration document. Do not loosen everything at
once because one thing failed — that turns one known problem into an unknown
number of them.

**Files touched.** `auc/backend/requirements.txt`, or
`requirements-rag.txt` after item 2.

**Done when.** A clean environment builds on the Spark, and every Phase I check
in the migration document still passes.

**Risk to the current machine. Medium if done carelessly** — different pins mean
the two machines are no longer running identical code, which undermines the
comparison that Section 6 relies on to decide the Spark is proven. Prefer to
finish proving the migration first and tidy pins afterwards.

---

# Summary table

| # | Change | Status |
|---|---|---|
| 1 | Stop tracking the database in git | ✅ Done (`f0c3b5a`, by the study-branch merge) |
| 2 | Split out the index dependency | ✅ Done |
| 3 | Preflight check script | ✅ Done — `auc/preflight.sh` |
| 4 | Surface index health before generating | ✅ Done — `GET /api/rag/status`, shown in Settings |
| 5 | Configurable embedding model | ✅ Done — stamped into the index |
| 6 | Bundle the web fonts | ✅ Done — 8 files, 460 KB, SIL OFL |
| 7 | Configurable host and port | ✅ Done — `AUC_HOST` / `AUC_PORT` |
| 8 | Safer `setup.sh` | ✅ Done — your unit-file edits now survive a re-run |
| 9 | Fix `requirements.txt` | ✅ Done |
| 10 | Document environment variables | ✅ Done — `auc/.env.example` |
| 11 | HTTPS via Tailscale, bind to localhost | **Open** — on arrival. Code side done: set `AUC_HOST=127.0.0.1` |
| 12 | Settle model configuration | **Open** — on arrival, and see the note below |
| 13 | Loosen version pins | **Open** — only if something forces it |

Not in the original plan, added because the migration made the case for them:

| | What | Why |
|---|---|---|
| — | `auc/check.sh` | One command for ruff, pytest, shellcheck, ESLint and the build |
| — | `auc/backend/tests/` | 29 tests, no Ollama needed. Turns "does the code work on ARM" into ten seconds |
| — | ruff + ESLint | Both clean. ESLint immediately caught an import I removed that was still used |
| — | `auc/capture-environment.sh` | Answers ~25 of Section 3's 33 questions, and captures your Modelfiles |
| — | `AUC_DATA_DIR` | The data directory was hardcoded, so nothing could be tested without touching real data |

**On item 12, the plan's diagnosis was wrong.** It treated "which model writes
the summaries" as something to settle on a single value. Reading the code, the
model is chosen per generation — sent by the browser from Settings, with
`AUC_SUMMARY_MODEL` and then `OLLAMA_MODEL` as fallbacks — so any model in
`ollama list` works and there is nothing to settle. What item 12 is really for
is recording, once you have chosen a model on the Spark, that it honours
Ollama's JSON-constrained output and quotes verbatim. Both are now written down
in `auc/.env.example` and `auc/README.md`.

## What the merge changed in this plan

| | |
|---|---|
| Item 1 | Done, and done more thoroughly than proposed |
| Item 2 | Still worth doing, opposite reasoning — no graceful degradation to rely on any more |
| Item 4 | Scope cut; the silent downgrade is fixed, so this is now about knowing *before* you generate, plus catching an embedding mismatch |
| Item 9 | Grew a second half: `anyio` is imported but undeclared |
| Item 10 | Gained `AUC_SUMMARY_MODEL`, which overrides the model for summaries only |
| Item 12 | Gained two hard verification requirements for any replacement model |
| Everything else | Unchanged. The merge added **no** dependencies, so the ARM risk picture in `SPARK_MIGRATION.md` Section 1 stands exactly as written. |
