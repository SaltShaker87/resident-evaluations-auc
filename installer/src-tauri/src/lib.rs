//! AUC Installer — entry point.
//!
//! The same binary runs two ways: with a window (Tauri, the default) or with
//! `--headless` for a machine reached over SSH. Both drive the same engine;
//! see ../../CONTRACT.md for the commands, events and on-disk layout.

mod cli;
mod commands;
mod engine;
mod platform;

pub fn run() {
    // The command line is only read when the user asked for it, so that a
    // desktop launcher passing arguments of its own cannot stop the window
    // from opening.
    if let Some(parsed) = cli::parse_if_headless() {
        if parsed.headless {
            std::process::exit(cli::run(parsed));
        }
    }
    commands::start_gui();
}
