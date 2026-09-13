//! Windows — and anything else that is neither Linux nor a Mac.
//!
//! Same shape as the Mac version: `detect` explains, everything else refuses.

use std::path::Path;

use anyhow::{anyhow, Result};

use crate::engine::types::SystemInfo;
use crate::engine::Ctx;
use crate::platform::Platform;

pub struct WindowsPlatform {
    /// What this computer actually is, so the message can name it.
    os: String,
}

impl WindowsPlatform {
    pub fn for_os(os: &str) -> WindowsPlatform {
        WindowsPlatform { os: os.to_string() }
    }

    fn reason(&self) -> String {
        let name = match self.os.as_str() {
            "windows" => "Windows".to_string(),
            other => other.to_string(),
        };
        format!(
            "AUC installs on Linux only — an Ubuntu computer, or an NVIDIA DGX Spark. \
             This computer is running {name}, so the installer cannot set it up here. You can \
             still use AUC from this computer's browser once it is installed on the Linux machine."
        )
    }
}

impl Platform for WindowsPlatform {
    fn detect(&self) -> SystemInfo {
        let mut system = SystemInfo::blank();
        system.supported = false;
        system.unsupported_reason = Some(self.reason());
        system
    }

    fn install_python(&self, _ctx: &mut Ctx, _app: &Path) -> Result<()> {
        Err(anyhow!(self.reason()))
    }

    fn install_ollama(&self, _ctx: &mut Ctx) -> Result<()> {
        Err(anyhow!(self.reason()))
    }

    fn install_docker_stack(&self, _ctx: &mut Ctx, _system: &SystemInfo) -> Result<()> {
        Err(anyhow!(self.reason()))
    }

    fn install_autostart(&self, _ctx: &mut Ctx) -> Result<()> {
        Err(anyhow!(self.reason()))
    }

    fn open_browser(&self, url: &str) -> Result<()> {
        Err(anyhow!(
            "This installer cannot open a browser on this kind of computer. The address is {url}."
        ))
    }

    fn privileged_run(
        &self,
        _ctx: &mut Ctx,
        _label: &str,
        _script: &str,
        _stdin: Option<&str>,
    ) -> Result<()> {
        Err(anyhow!(self.reason()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn windows_is_named_in_the_message() {
        let system = WindowsPlatform::for_os("windows").detect();
        assert!(!system.supported);
        assert!(system
            .unsupported_reason
            .expect("a reason")
            .contains("running Windows"));
    }

    #[test]
    fn something_we_have_never_heard_of_still_gets_a_sensible_message() {
        let system = WindowsPlatform::for_os("freebsd").detect();
        assert!(system
            .unsupported_reason
            .expect("a reason")
            .contains("running freebsd"));
    }
}
