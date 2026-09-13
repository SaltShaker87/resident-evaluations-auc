//! What differs between one kind of computer and another.
//!
//! Version 1 of AUC installs on Linux only — Ubuntu on an ordinary desktop,
//! and the NVIDIA DGX Spark. The Mac and Windows versions of this trait exist
//! so that the installer opens, says so in plain language, and stops, rather
//! than failing halfway through with an error about apt.

pub mod linux;
pub mod macos;
pub mod windows;

use std::path::Path;

use anyhow::Result;

use crate::engine::types::SystemInfo;
use crate::engine::Ctx;

pub trait Platform: Send + Sync {
    /// Look at this machine. Never fails: anything we cannot find out is
    /// reported as unknown, which the screens can say something sensible about.
    fn detect(&self) -> SystemInfo;

    /// A Python of AUC's own, and the packages it needs.
    fn install_python(&self, ctx: &mut Ctx, app: &Path) -> Result<()>;

    /// Ollama, which writes the summaries whichever retrieval engine is used.
    fn install_ollama(&self, ctx: &mut Ctx) -> Result<()>;

    /// Docker, the compose plugin and the NVIDIA Container Toolkit: everything
    /// the Nemotron containers need before they can start.
    fn install_docker_stack(&self, ctx: &mut Ctx, system: &SystemInfo) -> Result<()>;

    /// Make AUC start with the machine.
    fn install_autostart(&self, ctx: &mut Ctx) -> Result<()>;

    fn open_browser(&self, url: &str) -> Result<()>;

    /// Run one batch of commands as the administrator. The script's text is
    /// logged first, so there is never any doubt about what was asked for.
    /// `stdin` is for credentials, which must not become arguments.
    fn privileged_run(
        &self,
        ctx: &mut Ctx,
        label: &str,
        script: &str,
        stdin: Option<&str>,
    ) -> Result<()>;
}

/// The platform this copy of the installer is running on.
pub fn current() -> Box<dyn Platform> {
    match std::env::consts::OS {
        "linux" => Box::new(linux::LinuxPlatform),
        "macos" => Box::new(macos::MacPlatform),
        // Windows, and anything else: the same answer, with the name of
        // whatever this actually is.
        other => Box::new(windows::WindowsPlatform::for_os(other)),
    }
}
