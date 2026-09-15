//! Linux: the machine AUC actually runs on.

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::engine::layout::Layout;
use crate::engine::process::Cmd;
use crate::engine::steps::{autostart, nemotron, ollama, python};
use crate::engine::types::SystemInfo;
use crate::engine::{detect, Ctx};
use crate::platform::Platform;

pub struct LinuxPlatform;

impl Platform for LinuxPlatform {
    fn detect(&self) -> SystemInfo {
        let mut system = SystemInfo::blank();
        system.distro = detect::read_os_release();
        system.gpu = detect::detect_gpu();
        system.memory_gb = detect::read_memory_gb();
        detect::fill_unified_memory(&mut system.gpu, system.memory_gb);
        system.tools = detect::detect_tools();
        system.internet = detect::has_internet();

        // Free space is asked of the disk that will hold $AUC_HOME, which on a
        // first install does not exist yet.
        if let Ok(layout) = Layout::detect() {
            system.disk_free_gb = detect::disk_free_gb(&layout.auc_home);
            system.existing = detect::detect_existing(&layout);
        }

        // 64-bit Intel/AMD and 64-bit ARM are the two shapes AUC is built for;
        // the second is what a DGX Spark is.
        let arch_supported = matches!(system.arch.as_str(), "x86_64" | "aarch64");
        system.supported = arch_supported;
        system.unsupported_reason = if arch_supported {
            None
        } else {
            Some(format!(
                "AUC is built for 64-bit Intel, AMD and ARM computers. This one is a {}.",
                system.arch
            ))
        };
        system
    }

    fn install_python(&self, ctx: &mut Ctx, app: &Path) -> Result<()> {
        python::run(ctx, app)
    }

    fn install_ollama(&self, ctx: &mut Ctx) -> Result<()> {
        // Ollama's own installer, which is the supported way to install it and
        // needs root to put the binary in /usr/local/bin and add its service.
        let script = format!(
            "#!/bin/bash\n\
             # AUC installer — installing Ollama with its own official installer.\n\
             set -euo pipefail\n\
             {}\n",
            ollama::INSTALL_SCRIPT
        );
        self.privileged_run(ctx, "Installing Ollama", &script, None)
    }

    fn install_docker_stack(&self, ctx: &mut Ctx, system: &SystemInfo) -> Result<()> {
        // The group file, not this session: usermod has already been done on a
        // previous run, even if this login is older than that change.
        let in_group = detect::in_docker_group() || detect::in_docker_group_file();
        let needs = nemotron::needs_from(system, in_group);
        if !needs.anything() {
            ctx.log("Docker, the compose plugin and the NVIDIA runtime are all already here.");
            return Ok(());
        }
        let user = autostart::current_user();
        let script = nemotron::docker_setup_script(&needs, &user);
        self.privileged_run(
            ctx,
            "Setting up Docker for the NVIDIA Nemotron containers",
            &script,
            None,
        )?;
        if needs.add_group {
            ctx.log(
                "You have been added to the docker group. That only takes effect at your next \
                 login, so for today the installer starts the containers with `sg docker` rather \
                 than asking for the administrator password again.",
            );
        }
        Ok(())
    }

    fn install_autostart(&self, ctx: &mut Ctx) -> Result<()> {
        autostart::write_and_enable_units(ctx)?;
        autostart::enable_linger(self, ctx)
    }

    fn open_browser(&self, url: &str) -> Result<()> {
        // xdg-open, so it is whichever browser the user actually uses. Not
        // waited on: the browser outlives the installer.
        std::process::Command::new("xdg-open")
            .arg(url)
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .with_context(|| {
                format!("Could not open a browser. AUC is at {url} — open it by hand.")
            })?;
        Ok(())
    }

    fn privileged_run(
        &self,
        ctx: &mut Ctx,
        label: &str,
        script: &str,
        stdin: Option<&str>,
    ) -> Result<()> {
        ctx.log(format!(
            "{label} needs the administrator password. This is exactly what will run:"
        ));
        for line in script.lines() {
            ctx.log(format!("    {line}"));
        }

        // A temporary file, so the whole phase is one password prompt instead
        // of one per command. It is removed when this function returns.
        let mut file = tempfile::Builder::new()
            .prefix("auc-installer-")
            .suffix(".sh")
            .tempfile()
            .context("Could not create the temporary script for the administrator commands.")?;
        file.write_all(script.as_bytes())
            .context("Could not write the temporary script for the administrator commands.")?;
        file.flush().ok();
        let path = file.path().to_path_buf();
        // Readable by root, which is all that has to read it.
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700));
        }

        let program = nemotron::privileged_program(ctx.privilege);
        let mut command = Cmd::new(program);
        if program == "sudo" {
            command = command.arg("--");
        }
        command = command.args(["/bin/bash", &path.display().to_string()]);
        if let Some(secret) = stdin {
            command = command.stdin_secret(secret).redact(secret);
        }
        let result = command.timeout(Duration::from_secs(60 * 60)).run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            label,
        );

        drop(file);
        result.map(|_| ()).map_err(|err| {
            err.context(format!(
                "{label} could not be completed. If the password prompt was cancelled, try again."
            ))
        })
    }
}
