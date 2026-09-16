# AUC Installer — the contract between the Rust engine and the React screens

This file is the single source of truth for how the two halves of the
installer talk to each other, and for where the installer puts things on the
user's machine. Both halves are written against it; if it changes, both change.

The installer is a Tauri v2 application:

- `src-tauri/` — Rust. Detects the machine, downloads and installs AUC, drives
  Ollama, Docker and the NVIDIA Nemotron containers, writes the systemd units,
  and streams progress. Also runs with no window at all (`--headless`) for a
  DGX Spark reached over SSH.
- `src/` — React. Screens only. It never runs a command itself; it calls the
  commands below and renders the events.

Hard rules the engine enforces, whatever the screens ask for:

1. **Local-only.** The Nemotron URLs written to `auc.env` are always
   `http://localhost:8001` and `http://localhost:8002`. The engine refuses to
   continue if it ever finds a non-loopback host in `auc.env` or in
   `nim/docker-compose.yml`. Resident comments must never leave the machine.
2. **Never delete data without a typed confirmation.** `data/`, `backups/`
   and `~/.cache/nim` survive Update and Uninstall unless the user typed
   `DELETE` in the screen, which is passed as an explicit flag.
3. **Credentials never touch disk.** The NGC key is piped to `docker login`
   on stdin and passed to `docker compose` in the process environment only.
   It is never written to `auc.env`, the state file or the log.
4. **Every command's output goes to the log** (`auc://log` events and the
   log file), so "Copy details" always has the real error.
5. **Children get this machine's environment, not the installer's.** An
   AppImage's launcher exports `PYTHONHOME`, `PYTHONPATH`, `LD_LIBRARY_PATH`
   and GTK settings that point into its own temporary mount; every program
   the engine runs has those removed and `$APPDIR` entries taken out of
   `PATH` and `XDG_DATA_DIRS` (`process::scrub_env`). Otherwise `python3`
   and `backend/venv/bin/python` die on startup and the health check reports
   problems the machine does not have.

---

## 1. Where things live on the machine (Linux)

```
$AUC_HOME = ~/.local/share/auc
├── app/
│   ├── 1.4.0/                 one extracted release: backend/, frontend/dist/, rag/, nim/, *.sh, VERSION
│   │   └── backend/venv/      the Python environment, created with uv (scripts expect backend/venv)
│   └── current -> 1.4.0       symlink; swapped atomically on update, pointed back on rollback
├── data/                      AUC_DATA_DIR: auc.db, photos/, logs/
├── backups/                   AUC_BACKUP_DIR default
├── tools/
│   ├── uv                     the uv binary
│   └── python/                UV_PYTHON_INSTALL_DIR: the managed Python 3.12
└── logs/
    └── installer-YYYYMMDD-HHMMSS.log

~/.config/auc/auc.env              KEY=VALUE lines (systemd EnvironmentFile syntax, no `export`)
~/.config/auc/installer-state.json see InstallerState below
~/.config/systemd/user/auc.service
~/.config/systemd/user/auc-backup.service
~/.config/systemd/user/auc-backup.timer
~/.local/share/applications/auc.desktop      an "AUC" menu icon that opens the app URL
~/.local/share/icons/hicolor/256x256/apps/auc.png   the AUC emblem the menu icon and shortcut show
~/Desktop/auc.desktop                        the same entry as a Desktop shortcut (the folder named by
                                             XDG_DESKTOP_DIR in ~/.config/user-dirs.dirs; skipped if
                                             the machine has no Desktop folder)
~/.cache/nim/                                Nemotron model weights (matches start-nemotron.sh)
```

The release tarball has a single top-level directory `auc/`; its contents are
extracted into `app/<version>/`. `run.sh`, `preflight.sh`, `check-model.sh`,
`start-nemotron.sh` and `nim/docker-compose.yml` are all inside it.

### `auc.env`

```
AUC_DATA_DIR=/home/you/.local/share/auc/data
AUC_HOST=127.0.0.1                 # "local" scope; 0.0.0.0 for "lan"
AUC_PORT=3000
AUC_BACKUP_DIR=/home/you/.local/share/auc/backups
AUC_BACKUP_KEEP_DAYS=14
OLLAMA_URL=http://localhost:11434
OLLAMA_MODEL=qwen3.5:9b            # the model chosen in the installer
AUC_EMBED_MODEL=qwen3-embedding:0.6b
AUC_NIM_EMBED_URL=http://localhost:8001
AUC_NIM_RERANK_URL=http://localhost:8002
AUC_RETRIEVAL_ENGINE_DEFAULT=ollama   # ONLY while Nemotron was chosen but is not yet running; removed once it is
```

Lines the user added by hand are preserved across Update (same rule as
`setup.sh`'s `install_unit`: the user's value wins over the generated one).

### systemd units

`auc.service`:

```
[Unit]
Description=AUC — Assessments Under Curve
After=network.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/auc/app/current/backend
ExecStart=%h/.local/share/auc/app/current/run.sh
EnvironmentFile=%h/.config/auc/auc.env
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

`auc-backup.service` (oneshot, `ExecStart=%h/.local/share/auc/app/current/backend/venv/bin/python backup.py`,
same `WorkingDirectory` and `EnvironmentFile`) and `auc-backup.timer`
(`OnCalendar=*-*-* 02:00:00`, `Persistent=true`, `WantedBy=timers.target`),
mirroring `auc/setup.sh`.

Whenever the engine runs one of the repo's scripts (`preflight.sh`,
`build_index.py`, `check-model.sh`) it loads `auc.env` into that process's
environment first, so the script sees the same settings the service does.

### `InstallerState` (`installer-state.json`)

```json
{
  "schema": 1,
  "version": "1.4.0",
  "installed_at": "2026-09-12T23:40:11Z",
  "auc_home": "/home/you/.local/share/auc",
  "choices": {
    "ai_enabled": true,
    "model": "qwen3.5:9b",
    "nemotron_enabled": true,
    "network_scope": "local"
  },
  "nemotron_pending": false,
  "adopted_from": null
}
```

`adopted_from` records the path of a hand-made `setup.sh` install that was
migrated, or `null`.

---

## 2. Release source

Default: the newest GitHub Release of `SaltShaker87/resident-evaluations-auc`,
via `https://api.github.com/repos/SaltShaker87/resident-evaluations-auc/releases/latest`.
Assets: `auc-<version>.tar.gz` and `auc-<version>.tar.gz.sha256`. The engine
verifies the digest before extracting.

Override for development and offline use: environment variable
`AUC_INSTALLER_SOURCE` or headless flag `--source`, pointing at a local
`.tar.gz` path or a direct URL. A local path skips the digest check and says so
in the log.

---

## 3. Tauri commands (React -> Rust)

All commands return `Result<T, String>`; the `String` is a plain-language
message fit to show the user. Long-running work is never done inside a
command — `run_action` starts it and returns immediately; progress arrives as
events.

| command | args | returns |
|---|---|---|
| `detect_system` | — | `SystemInfo` |
| `recommend` | `system: SystemInfo` | `Recommendation` |
| `read_state` | — | `InstallerState \| null` |
| `check_for_update` | — | `UpdateInfo` |
| `run_action` | `action: Action` | `()` — fails if an action is already running |
| `cancel_action` | — | `()` — best effort; the current step finishes or is killed, then `auc://finished` arrives with `ok: false, cancelled: true` |
| `get_log` | — | `string` — the whole log of the current or last run |
| `open_app` | — | `()` — opens the browser at the app URL |
| `open_url` | `url: string` | `()` — allow-listed hosts only: `ngc.nvidia.com`, `github.com`, `ollama.com` |

### `SystemInfo`

```ts
{
  os: "linux" | "macos" | "windows",
  arch: "x86_64" | "aarch64",
  distro: { id: string, version: string, pretty: string } | null,   // from /etc/os-release
  supported: boolean,                 // v1: linux only
  unsupported_reason: string | null,  // plain language, shown on the Welcome screen
  gpu: {
    vendor: "nvidia" | "amd" | "apple" | "none",
    name: string | null,              // e.g. "NVIDIA GB10", "NVIDIA GeForce RTX 3090"
    memory_gb: number | null,         // total across GPUs for NVIDIA; null if unknown
    is_gb10: boolean,                 // same rule as backend/retrieval_engine.py is_spark()
    memory_unified: boolean           // memory_gb is the machine's shared memory (a GB10 reports
                                      // "[N/A]" for its own, so the machine's total stands in,
                                      // rounded up to the size it was sold as, e.g. 128)
  },
  memory_gb: number,
  disk_free_gb: number,               // at $AUC_HOME's filesystem
  internet: boolean,                  // could reach github.com
  tools: {
    ollama:  { present: boolean, version: string | null, running: boolean },
    docker:  { present: boolean, usable_by_user: boolean, nvidia_runtime: boolean, compose: boolean },
    systemd_user: boolean,            // `systemctl --user show-environment` works
    pkexec: boolean
  },
  existing:
    | null
    | { kind: "installer", version: string, state: InstallerState }
    | { kind: "manual", service_path: string, working_directory: string, env: Record<string,string> }
}
```

### `Recommendation`

```ts
{
  ai: { recommended: boolean, reason: string },
  models: Array<{
    name: string,          // exact Ollama name, e.g. "qwen3.5:4b"
    label: string,         // "Qwen 3.5 4B"
    description: string,   // one sentence
    min_memory_gb: number, // 8, 16, 33
    recommended: boolean,  // exactly one true when ai.recommended, else none
    fits: boolean          // memory_gb >= min_memory_gb
  }>,
  best_message: string,    // always starts "AUC runs best on NVIDIA Nemotron 3.5 Lightning."
                           // when it is not the recommendation, continues " It needs more than 32 GB of GPU memory; this machine has N GB."
  nemotron: {
    mode: "default" | "optional" | "unavailable",
    reason: string
  }
}
```

Tiers, by `gpu.memory_gb` (unified memory on a GB10 counts):

| GPU memory | recommendation |
|---|---|
| none / under 8 GB | `ai.recommended = false`; models listed with `recommended: false` |
| 8 – 15 GB | `qwen3.5:4b` |
| 16 – 32 GB | `qwen3.5:9b` |
| over 32 GB | `nemotron-3.5-lightning` |

Models are always listed smallest to largest: `qwen3.5:4b`, `qwen3.5:9b`,
`nemotron-3.5-lightning`. The embedding model `qwen3-embedding:0.6b` is not a
choice; it is always pulled when AI is enabled.

`nemotron.mode`: `default` on a GB10; `optional` on any other NVIDIA GPU with
`memory_gb >= 16`; `unavailable` otherwise (with the reason). Nemotron is
Linux-only.

### `Action`

```ts
type Action =
  | { kind: "install", options: InstallOptions }
  | { kind: "update" }
  | { kind: "repair" }
  | { kind: "finish_nemotron", ngc_key: string | null }
  | { kind: "uninstall", delete_data: boolean, delete_nemotron_cache: boolean }

type InstallOptions = {
  ai_enabled: boolean,
  model: string | null,              // required when ai_enabled
  nemotron_enabled: boolean,
  ngc_key: string | null,            // may be null even with nemotron_enabled: the engine then tries (images may be cached) and falls back
  network_scope: "local" | "lan",
  adopt_existing: boolean            // when existing.kind == "manual": migrate its data and replace its units
}
```

### `UpdateInfo`

```ts
{ installed: string | null, latest: string | null, available: boolean, notes: string | null, error: string | null }
```

---

## 4. Events (Rust -> React)

| event | payload |
|---|---|
| `auc://step` | `StepEvent` |
| `auc://log` | `{ line: string }` |
| `auc://preflight` | `{ lines: Array<{ level: "pass" \| "warn" \| "fail" \| "info", text: string, hint: string \| null }> }` |
| `auc://finished` | `Outcome` |

```ts
type StepEvent = {
  id: StepId,
  label: string,                    // plain language, e.g. "Downloading AI models"
  status: "pending" | "running" | "done" | "warning" | "failed" | "skipped",
  detail: string | null,            // one line under the label, e.g. "qwen3.5:9b — 2.1 GB of 5.6 GB"
  progress: number | null           // 0..1 when known, else null
}

type Outcome = {
  ok: boolean,
  cancelled: boolean,
  summary: string,                  // one or two plain sentences
  warnings: string[],
  app_url: string | null,           // "http://localhost:3000"
  nemotron_pending: boolean,        // true => show "AI summaries use Standard for now; finish Nemotron later"
  error: { message: string, hint: string | null, details: string | null } | null
}
```

### Step ids and order

The engine emits every step for the action up front with `status: "pending"`
so the screen can draw the whole list, then updates them in order. Key rows
by `id`; the same id arrives many times.

Which steps may end as `warning` without stopping the action: `python` (the
ACGME index layer failing to install), `nemotron`, `index`, `autostart` and
`start` (no systemd user session — the app is installed but must be started
by hand, as `setup.sh` also does). Every other step failing ends the action
with `ok: false` — including `models`: an AI install whose model did not
download is not an AI install.

Notes on behaviour the engine settled on:

- The state file is written at the end of `configure` (the version is not
  known until `download` has unpacked it) and again after `preflight`, not
  in `prepare`. The log file is opened before `prepare`.
- On a GB10 machine where the user deliberately turned Nemotron off,
  `AUC_RETRIEVAL_ENGINE_DEFAULT=ollama` is written as well, for the same
  reason as the pending case: the app's hardware rule would otherwise pick an
  engine that is not running.
- When GPU memory is unknown, `best_message` ends "...this machine has no
  graphics card memory to report." instead of naming a number.
- While the user is not yet in the `docker` group of this login session,
  Docker is run with `sg docker` rather than `pkexec`, so the NGC key is never
  piped through a graphical password prompt. The "did a container die" check
  runs every 10 seconds. Readiness polling is plain HTTP.

**install** (and **repair**, which re-runs the same steps idempotently):

| id | label | notes |
|---|---|---|
| `prepare` | Getting ready | create directories, open the log, write state |
| `download` | Downloading AUC | GitHub Release or `--source`; digest check |
| `python` | Setting up Python | uv, Python 3.12, venv, `requirements.txt` then `requirements-rag.txt` (the latter is a warning, not a failure, as in `setup.sh`) |
| `configure` | Writing settings | `auc.env`, data dir; adopt existing data if asked |
| `ollama` | Installing Ollama | skipped when `ai_enabled` is false or Ollama is present |
| `models` | Downloading AI models | chosen model + `qwen3-embedding:0.6b`, with byte progress from Ollama's pull API |
| `docker` | Setting up Docker for NVIDIA Nemotron | skipped unless `nemotron_enabled`; Docker, compose, NVIDIA Container Toolkit, docker group |
| `nemotron` | Starting NVIDIA Nemotron | login, pull (or use cached images), up, wait for `/v1/health/ready`; on failure becomes `warning` with the real reason and sets `nemotron_pending` |
| `index` | Building the ACGME reference index | `rag/build_index.py` (all engines it can reach) |
| `autostart` | Setting up auto-start | units, `daemon-reload`, `enable`, linger, AUC icon, menu entry, Desktop shortcut |
| `start` | Starting AUC | `systemctl --user restart auc`, wait for `/api/auth/status` |
| `preflight` | Checking everything works | runs `preflight.sh`, emits `auc://preflight` |

**update**: `prepare`, `backup` (runs `backup.py`), `download`, `python`,
`configure` (merge), `nemotron` (only if the compose file changed), `index`
(only if an embedding model changed), `switch` ("Switching to the new version":
stop, swap symlink, start; on failure point the symlink back and start the old
one), `preflight`.

**finish_nemotron**: `prepare`, `docker`, `nemotron`, `index`, `configure`
(removes `AUC_RETRIEVAL_ENGINE_DEFAULT`), `start`, `preflight`.

**uninstall**: `stop` (units and containers), `remove_autostart`,
`remove_app` (app/, tools/, menu entry, Desktop shortcut, AUC icon, state), `remove_data` (skipped unless
`delete_data`), `remove_nemotron_cache` (skipped unless `delete_nemotron_cache`).
Ollama and Docker are left installed; the summary says so.

---

## 5. Headless mode

Same binary, no window:

```
auc-installer --headless install [--no-ai] [--model NAME] [--nemotron | --no-nemotron]
                                 [--network local|lan] [--source PATH_OR_URL] [--adopt] [--yes]
auc-installer --headless update
auc-installer --headless repair
auc-installer --headless finish-nemotron
auc-installer --headless uninstall [--delete-data] [--delete-nemotron-cache]
auc-installer --headless status         # prints SystemInfo + state as JSON
```

Without `--yes` it prints the plan (the same `Recommendation`) and asks
`Proceed? [y/N]`. The NGC key is read from `NGC_API_KEY` in the environment or
prompted for on stdin with echo off; it is never a command-line argument.
Exit code 0 on `ok`, 1 otherwise. Steps print as one line each
(`[3/12] Setting up Python ... done`), and log lines print indented beneath the
current step.

---

## 6. Privileged commands

Only these ever run with elevated rights, always through `pkexec` (the
graphical prompt) in the GUI and `sudo` in headless mode:

- Ollama's official install script (`https://ollama.com/install.sh`)
- Installing Docker Engine, the compose plugin and the NVIDIA Container
  Toolkit from the distro's or the vendor's apt repository
- `usermod -aG docker <user>`
- `loginctl enable-linger <user>` when the unprivileged call fails

`docker` / `docker compose` are not run as root. If this login session is not
yet in the `docker` group, they are run with `sg docker` so the NGC key can
stay on stdin and in the process environment.

Steps are batched so one phase asks for the password once: the engine writes
the phase's commands to a temporary script, logs the script's contents, runs
it once under `pkexec`, then deletes it.
