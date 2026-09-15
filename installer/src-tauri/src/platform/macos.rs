//! macOS: the installer opens, explains itself, and stops.
//!
//! AUC's service, its containers and its auto-start are all Linux things.
//! Rather than pretend otherwise and fail somewhere confusing, everything here
//! except `detect` refuses, and `detect` says why in words a user can act on.

use std::path::Path;

use anyhow::{anyhow, Result};

use crate::engine::layout::Layout;
use crate::engine::types::{GpuInfo, GpuVendor, SystemInfo};
use crate::engine::{detect, Ctx};
use crate::platform::Platform;

pub struct MacPlatform;

const REASON: &str = "AUC installs on Linux only — an Ubuntu computer, or an NVIDIA DGX Spark. \
                      This is a Mac, so the installer cannot set it up here. You can still use \
                      AUC from this Mac's browser once it is installed on the Linux machine.";

impl Platform for MacPlatform {
    fn detect(&self) -> SystemInfo {
        let mut system = SystemInfo::blank();
        system.supported = false;
        system.unsupported_reason = Some(REASON.to_string());
        system.gpu = GpuInfo {
            vendor: GpuVendor::Apple,
            name: None,
            memory_gb: None,
            is_gb10: false,
            memory_unified: false,
        };
        if let Ok(layout) = Layout::detect() {
            system.disk_free_gb = detect::disk_free_gb(&layout.auc_home);
        }
        system
    }

    fn install_python(&self, _ctx: &mut Ctx, _app: &Path) -> Result<()> {
        Err(anyhow!(REASON))
    }

    fn install_ollama(&self, _ctx: &mut Ctx) -> Result<()> {
        Err(anyhow!(REASON))
    }

    fn install_docker_stack(&self, _ctx: &mut Ctx, _system: &SystemInfo) -> Result<()> {
        Err(anyhow!(REASON))
    }

    fn install_autostart(&self, _ctx: &mut Ctx) -> Result<()> {
        Err(anyhow!(REASON))
    }

    fn open_browser(&self, url: &str) -> Result<()> {
        // Worth having even here: the Welcome screen's links should open.
        std::process::Command::new("open")
            .arg(url)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map(|_| ())
            .map_err(|err| anyhow!("Could not open a browser ({err}). The address is {url}."))
    }

    fn privileged_run(
        &self,
        _ctx: &mut Ctx,
        _label: &str,
        _script: &str,
        _stdin: Option<&str>,
    ) -> Result<()> {
        Err(anyhow!(REASON))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_mac_is_turned_away_with_a_reason_a_person_can_act_on() {
        let system = MacPlatform.detect();
        assert!(!system.supported);
        let reason = system.unsupported_reason.expect("there must be a reason");
        assert!(reason.contains("Linux only"));
        assert!(reason.contains("browser"), "it should say what they can do");
    }

    #[test]
    fn nothing_else_pretends_to_work() {
        let mut ctx = crate::engine::test_support::ctx();
        assert!(MacPlatform
            .install_python(&mut ctx, Path::new("/tmp"))
            .is_err());
        assert!(MacPlatform.install_ollama(&mut ctx).is_err());
        assert!(MacPlatform
            .install_docker_stack(&mut ctx, &SystemInfo::blank())
            .is_err());
        assert!(MacPlatform.install_autostart(&mut ctx).is_err());
        assert!(MacPlatform
            .privileged_run(&mut ctx, "anything", "true", None)
            .is_err());
    }
}
