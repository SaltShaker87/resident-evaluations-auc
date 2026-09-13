# AUC Installer

A small desktop program that installs, updates and removes AUC for people who
would rather not open a terminal. Download one file, double-click it, answer
three questions, and AUC is running and set to start with the computer.

This document is for the people maintaining it. The user-facing instructions
are in the top-level `README.md` under *Easiest way: use the installer*.

## What it does, in order

1. Looks at the computer: operating system, processor, graphics card and its
   memory, free disk, internet, whether Ollama and Docker are already there,
   and whether AUC is already installed (by this installer or by `setup.sh`).
2. Recommends what fits: whether AI summaries are worth turning on, which
   model to use by graphics-card memory, and whether the NVIDIA Nemotron
   retrieval engine applies (it is the default on a DGX Spark / HP ZGX Nano).
3. Downloads the newest AUC release from GitHub, verifies it, and sets it up
   under `~/.local/share/auc/` with its own private Python (via `uv`), so the
   user's machine needs neither Python nor Node.js.
4. Installs Ollama if missing, pulls the chosen model and the embedding model,
   and, where chosen, sets up Docker and the Nemotron containers and builds the
   ACGME reference index for every engine it can reach.
5. Writes the systemd user units, enables lingering, adds an "AUC" entry to
   the app menu, starts the app, and runs `preflight.sh` to show the result.

The same program, opened again later, offers Update, Repair, Finish Nemotron
setup (when that could not be completed the first time) and Uninstall.

Everything the installer touches on disk, every command and event between the
window and the engine, and the headless command line are specified in
[`CONTRACT.md`](CONTRACT.md). Change that file first, then both halves.

## Two hard rules

- **Local-only.** The Nemotron URLs are always `localhost`. The engine refuses
  to continue if it finds anything else in `auc.env` or the compose file.
  Resident comments never leave the machine.
- **Data survives.** `data/`, `backups/` and the Nemotron weights cache are
  never deleted by Update, and only by Uninstall when the user typed `DELETE`.

## Layout

```
installer/
├── CONTRACT.md          the specification both halves are written against
├── package.json         React + Vite + @tauri-apps/api
├── src/                 the screens (JavaScript, JSX)
│   ├── backend.js       calls Tauri commands; falls back to mock.js in a plain browser
│   ├── mock.js          simulated engine, for previewing screens without Linux
│   └── screens/         Welcome, Checking, Choices, Installing, Done, Error, Manage
└── src-tauri/           the engine (Rust)
    ├── src/lib.rs       entry: --headless -> CLI, otherwise the window
    ├── src/engine/      detect, recommend, steps, envfile, release, state
    └── src/platform/    Linux implemented; macOS and Windows are stubs for later
```

## Developing

Prerequisites: Node 18+, Rust stable (`brew install rust` or rustup). On Linux
also the Tauri build dependencies (`libwebkit2gtk-4.1-dev` and friends; see
`.github/workflows/installer-ci.yml` for the exact apt list).

```bash
cd installer
npm ci

# Screens only, in a normal browser, with a simulated engine:
npm run dev            # then open http://localhost:1420/?scenario=spark
                       # scenarios: spark, pc, nogpu, existing, manual, unsupported; add &fail=models

# The real thing (Linux):
npm run tauri dev

# Checks:
npm run lint && npm run build
cd src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test
```

## Headless (for a Spark reached over SSH)

```bash
auc-installer --headless install --model qwen3.5:9b --network local
auc-installer --headless status
auc-installer --headless update
auc-installer --headless finish-nemotron      # asks for the NGC key on stdin, or reads NGC_API_KEY
auc-installer --headless uninstall            # keeps the data; add --delete-data to remove it
```

`--source /path/to/auc-1.4.0.tar.gz` installs from a local tarball instead of
GitHub, which is how CI tests it.

## Releasing

Tag the repository `vX.Y.Z` and push the tag. `.github/workflows/release.yml`
builds the frontend, packages `auc/` as `auc-X.Y.Z.tar.gz` (with a `VERSION`
file and a `.sha256`), builds the installer for Linux x86_64 and aarch64
(`.deb` and `.AppImage`), and attaches everything to a draft GitHub Release.
Review the draft, then publish it. The installer's Update button reads the
newest published release.

## Adding macOS or Windows later

Implement `Platform` for the OS in `src-tauri/src/platform/`: how to install
Python (uv works on all three), Ollama (its installer differs), auto-start
(launchd agent / Task Scheduler instead of systemd), and how to open the
browser. Nemotron is Linux-only by nature (NVIDIA containers) and stays
`unavailable` elsewhere. Then add the OS to the release matrix.
