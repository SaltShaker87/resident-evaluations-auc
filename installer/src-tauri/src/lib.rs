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

    // WebKitGTK's GPU-accelerated drawing path (the DMA-BUF renderer) fails
    // silently on NVIDIA's driver and leaves the window blank. Every DGX
    // Spark / ZGX Nano is affected, so it is turned off unless the user has
    // already decided for themselves. The cost is a little drawing speed,
    // which does not matter for an installer. It has to happen before the
    // window exists, which is why it is here and not in start_gui().
    #[cfg(target_os = "linux")]
    if std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none() {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    commands::start_gui();
}
