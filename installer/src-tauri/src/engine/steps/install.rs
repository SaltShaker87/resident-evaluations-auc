//! Installing AUC, from an empty machine to an app answering on port 3000.
//!
//! Repair runs exactly these steps again: everything here is written so that
//! running it twice is harmless, because "run it again" is the first thing
//! anyone tries when something has gone wrong.

use std::path::Path;

use anyhow::{Context, Result};

use crate::engine::layout::{ensure_dir, Layout};
use crate::engine::release;
use crate::engine::state;
use crate::engine::steps::{
    autostart, configure, download, index, nemotron, ollama, preflight, service,
};
use crate::engine::types::{Choices, InstallOptions, StepId, SystemInfo};
use crate::engine::{app_url, Ctx, FlowResult};
use crate::platform::Platform;

pub fn run(
    platform: &dyn Platform,
    ctx: &mut Ctx,
    options: InstallOptions,
    repairing: bool,
) -> Result<FlowResult> {
    let system = prepare(platform, ctx, repairing)?;

    // Before anything is written: if the user asked us to take over a
    // hand-made install, its data and settings come across first.
    let adopted = if options.adopt_existing {
        match system.existing.as_ref() {
            Some(existing) => super::adopt::adopt(ctx, existing)?,
            None => None,
        }
    } else {
        None
    };

    let downloaded = download::run(ctx, &release::source_from(None))?;
    let app = downloaded.dir.clone();

    platform.install_python(ctx, &app)?;

    let configured = configure::run(
        ctx,
        configure::Mode::Chosen {
            scope: options.network_scope,
            model: options.model.as_deref(),
        },
        adopted.as_ref().map(|a| &a.env),
    )?;
    let port = configured.port.clone();

    // The units point at app/current, so the new version has to be what
    // `current` means before anything is started.
    point_current_at(&ctx.layout, &downloaded.version)?;
    ctx.log(format!(
        "app/current now points at AUC {}.",
        downloaded.version
    ));

    // A GB10 machine uses the Nemotron engine by AUC's own rule, so a machine
    // where it was deliberately not set up has to be told to use the Standard
    // engine, or every summary would fail on an engine that is not running.
    if system.gpu.is_gb10 && !options.nemotron_enabled {
        ctx.log(
            "This machine would default to the NVIDIA Nemotron engine, which was not set up, \
             so AUC is pointed at the Standard engine instead.",
        );
        configure::set_engine_default(ctx, Some("ollama"))?;
    }

    let choices = Choices {
        ai_enabled: options.ai_enabled,
        model: options.model.clone(),
        nemotron_enabled: options.nemotron_enabled,
        network_scope: options.network_scope,
    };
    save_state(ctx, &downloaded.version, choices.clone(), adopted.as_ref())?;

    if options.ai_enabled {
        ollama::install(platform, ctx, system.tools.ollama.present)?;
        let base_url = configured
            .env
            .get("OLLAMA_URL")
            .unwrap_or(ollama::DEFAULT_OLLAMA_URL)
            .to_string();
        let models = ollama::models_to_pull(options.model.as_deref());
        ollama::pull_models(ctx, &base_url, &models)?;
    } else {
        ctx.progress.start(StepId::Ollama);
        ctx.progress
            .skipped(StepId::Ollama, "The AI summary features were not turned on");
        ctx.progress.start(StepId::Models);
        ctx.progress
            .skipped(StepId::Models, "The AI summary features were not turned on");
    }

    nemotron::docker_and_start(
        platform,
        ctx,
        &app,
        &system,
        options.ngc_key.as_deref(),
        options.nemotron_enabled,
    )?;

    if options.ai_enabled {
        index::run(ctx, &app)?;
    } else {
        index::skip(ctx, index::Skip::AiDisabled);
    }

    autostart::install(platform, ctx, &port, system.tools.systemd_user)?;
    service::start_and_wait(ctx, &port, system.tools.systemd_user)?;
    preflight::run(ctx, &app)?;

    // Written again now that we know whether Nemotron came up, so the screens
    // can offer to finish it later.
    save_state(ctx, &downloaded.version, choices, adopted.as_ref())?;

    Ok(FlowResult {
        summary: summarise(ctx, &downloaded.version, &port, repairing),
        app_url: Some(app_url(&port)),
    })
}

/// The `prepare` step: check this machine will do, and make the folders.
pub fn prepare(platform: &dyn Platform, ctx: &mut Ctx, repairing: bool) -> Result<SystemInfo> {
    ctx.progress.start(StepId::Prepare);
    let system = platform.detect();
    if !system.supported {
        anyhow::bail!(
            "{}",
            system.unsupported_reason.clone().unwrap_or_else(|| {
                "AUC cannot be installed on this kind of computer.".to_string()
            })
        );
    }

    for dir in [
        ctx.layout.auc_home.clone(),
        ctx.layout.app_dir(),
        ctx.layout.data_dir(),
        ctx.layout.backups_dir(),
        ctx.layout.tools_dir(),
        ctx.layout.logs_dir(),
        ctx.layout.config_dir.clone(),
    ] {
        ensure_dir(&dir)?;
    }

    ctx.log(format!(
        "{} AUC into {}.",
        if repairing { "Repairing" } else { "Installing" },
        ctx.layout.auc_home.display()
    ));
    if let Some(distro) = &system.distro {
        ctx.log(format!(
            "This machine: {} on {}, {} GB of memory, {} GB free.",
            distro.pretty,
            system.arch,
            crate::engine::recommend::format_gb(system.memory_gb),
            crate::engine::recommend::format_gb(system.disk_free_gb)
        ));
    }
    if let Some(name) = &system.gpu.name {
        ctx.log(format!("Graphics card: {name}."));
    }
    let leaked = crate::engine::process::leaked_vars_present();
    if !leaked.is_empty() {
        ctx.log(format!(
            "This installer was started from an AppImage, whose launcher sets {} for its own \
             use. Programs the installer runs are given this machine's own settings instead.",
            leaked.join(", ")
        ));
    }

    ctx.progress.done(StepId::Prepare);
    Ok(system)
}

/// Point `app/current` at a version, atomically.
///
/// The symlink is made under a temporary name and then renamed over the old
/// one, so there is no moment where `current` does not exist — a service that
/// restarts at the wrong instant would otherwise find nothing there.
pub fn point_current_at(layout: &Layout, version: &str) -> Result<()> {
    let target = layout.version_dir(version);
    let link = layout.current_link();
    let staging = layout.app_dir().join(".current.new");
    if staging.exists() || std::fs::symlink_metadata(&staging).is_ok() {
        let _ = std::fs::remove_file(&staging);
    }
    make_symlink(&target, &staging)?;
    std::fs::rename(&staging, &link).with_context(|| {
        format!(
            "Could not point {} at {}.",
            link.display(),
            target.display()
        )
    })?;
    Ok(())
}

/// Which version `app/current` points at, if any.
pub fn current_version(layout: &Layout) -> Option<String> {
    let target = std::fs::read_link(layout.current_link()).ok()?;
    target
        .file_name()
        .and_then(|name| name.to_str())
        .map(str::to_string)
}

#[cfg(unix)]
fn make_symlink(target: &Path, link: &Path) -> Result<()> {
    std::os::unix::fs::symlink(target, link)
        .with_context(|| format!("Could not create the link {}.", link.display()))
}

#[cfg(not(unix))]
fn make_symlink(_target: &Path, _link: &Path) -> Result<()> {
    anyhow::bail!("AUC can only be installed on Linux.")
}

fn save_state(
    ctx: &mut Ctx,
    version: &str,
    choices: Choices,
    adopted: Option<&super::adopt::Adopted>,
) -> Result<()> {
    let mut record = state::new_state(&ctx.layout, version, choices);
    record.nemotron_pending = ctx.nemotron_pending;
    record.adopted_from = adopted.map(|a| a.from.clone());
    // Keep the original install date if we have one: it is the only record of
    // when this machine first ran AUC.
    if let Some(previous) = state::read(&ctx.layout) {
        record.installed_at = previous.installed_at;
    }
    state::write(&ctx.layout, &record)
}

fn summarise(ctx: &Ctx, version: &str, port: &str, repairing: bool) -> String {
    let opening = if repairing {
        format!("AUC {version} has been checked over and put back in order.")
    } else {
        format!("AUC {version} is installed.")
    };
    let where_it_is = format!(
        " It is at {} and will start with this machine.",
        app_url(port)
    );
    let nemotron = if ctx.nemotron_pending {
        " AI summaries use the Standard engine for now; you can finish setting up NVIDIA Nemotron \
         later."
    } else {
        ""
    };
    format!("{opening}{where_it_is}{nemotron}")
}
