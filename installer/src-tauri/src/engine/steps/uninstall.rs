//! Removing AUC.
//!
//! The rule that matters: `data/`, `backups/` and the Nemotron model cache are
//! only deleted when the user typed DELETE in the screen, which reaches us as
//! an explicit flag. Ollama and Docker are left installed — they are other
//! people's software and may well be used for something else.

use std::path::Path;
use std::time::Duration;

use anyhow::Result;

use crate::engine::process::{have, succeeds, Cmd};
use crate::engine::state;
use crate::engine::steps::autostart::{self, UNITS};
use crate::engine::steps::service;
use crate::engine::types::StepId;
use crate::engine::{Ctx, FlowResult};

pub fn run(ctx: &mut Ctx, delete_data: bool, delete_nemotron_cache: bool) -> Result<FlowResult> {
    stop(ctx);
    remove_autostart(ctx);
    remove_app(ctx)?;
    let data_removed = remove_data(ctx, delete_data);
    let cache_removed = remove_nemotron_cache(ctx, delete_nemotron_cache);

    let kept = if data_removed {
        String::new()
    } else {
        format!(
            " Your database, photos and backups were left where they are, in {} and {}.",
            ctx.layout.data_dir().display(),
            ctx.layout.backups_dir().display()
        )
    };
    let cache = if cache_removed || !ctx.layout.nim_cache_dir().exists() {
        String::new()
    } else {
        format!(
            " The Nemotron model files are still in {}.",
            ctx.layout.nim_cache_dir().display()
        )
    };

    Ok(FlowResult {
        summary: format!(
            "AUC has been removed.{kept}{cache} Ollama and Docker were left installed, since \
             other things on this machine may use them."
        ),
        app_url: None,
    })
}

/// The `stop` step: the service, the timer, and the Nemotron containers.
fn stop(ctx: &mut Ctx) {
    ctx.progress.start(StepId::Stop);
    service::stop_everything(ctx);

    let compose_file = ctx.layout.current_link().join("nim/docker-compose.yml");
    if compose_file.is_file() && have("docker") && succeeds("docker", &["info"]) {
        ctx.log("Stopping the Nemotron containers.");
        let _ = Cmd::new("docker")
            .args(["compose", "-f"])
            .arg(compose_file.display().to_string())
            .args(["down"])
            // The compose file insists on these being set, even to take the
            // containers down again.
            .env("NIM_UID", super::nemotron::current_uid().to_string())
            .env(
                "NIM_CACHE_DIR",
                ctx.layout.nim_cache_dir().display().to_string(),
            )
            .timeout(Duration::from_secs(5 * 60))
            .run(ctx.emitter.as_ref(), &ctx.cancel);
    }
    ctx.progress.done(StepId::Stop);
}

/// The `remove_autostart` step: the units, and systemd's memory of them.
fn remove_autostart(ctx: &mut Ctx) {
    ctx.progress.start(StepId::RemoveAutostart);
    autostart::systemctl_quiet(ctx, &["disable", autostart::SERVICE_UNIT]);
    autostart::systemctl_quiet(ctx, &["disable", autostart::BACKUP_TIMER_UNIT]);

    for unit in UNITS {
        let path = ctx.layout.unit_path(unit);
        if path.exists() {
            match std::fs::remove_file(&path) {
                Ok(()) => ctx.log(format!("Removed {}.", path.display())),
                Err(err) => ctx.log(format!("Could not remove {}: {err}", path.display())),
            }
        }
    }
    autostart::systemctl_quiet(ctx, &["daemon-reload"]);
    autostart::systemctl_quiet(ctx, &["reset-failed"]);
    ctx.progress.done(StepId::RemoveAutostart);
}

/// The `remove_app` step: the program, the tools, the menu icon and our record
/// of the install. Not the data.
fn remove_app(ctx: &mut Ctx) -> Result<()> {
    ctx.progress.start(StepId::RemoveApp);
    for dir in [ctx.layout.app_dir(), ctx.layout.tools_dir()] {
        remove_tree(ctx, &dir);
    }
    let desktop = ctx.layout.desktop_file();
    if desktop.exists() {
        let _ = std::fs::remove_file(&desktop);
        ctx.log(format!("Removed the menu icon {}.", desktop.display()));
        if have("update-desktop-database") {
            if let Some(dir) = desktop.parent() {
                let _ = Cmd::new("update-desktop-database")
                    .arg(dir.display().to_string())
                    .quiet()
                    .run(ctx.emitter.as_ref(), &ctx.cancel);
            }
        }
    }
    state::remove(&ctx.layout)?;
    ctx.progress.done(StepId::RemoveApp);
    Ok(())
}

fn remove_data(ctx: &mut Ctx, delete_data: bool) -> bool {
    ctx.progress.start(StepId::RemoveData);
    if !delete_data {
        ctx.progress.skipped(
            StepId::RemoveData,
            "Your database, photos and backups were kept",
        );
        return false;
    }
    // Only reachable when the user typed DELETE in the screen.
    for dir in [ctx.layout.data_dir(), ctx.layout.backups_dir()] {
        remove_tree(ctx, &dir);
    }
    let env_file = ctx.layout.env_file();
    if env_file.exists() {
        let _ = std::fs::remove_file(&env_file);
        ctx.log(format!("Removed {}.", env_file.display()));
    }
    ctx.progress
        .done_with(StepId::RemoveData, "Database, photos and backups deleted");
    true
}

fn remove_nemotron_cache(ctx: &mut Ctx, delete_cache: bool) -> bool {
    ctx.progress.start(StepId::RemoveNemotronCache);
    if !delete_cache {
        ctx.progress.skipped(
            StepId::RemoveNemotronCache,
            "The downloaded Nemotron model files were kept",
        );
        return false;
    }
    let dir = ctx.layout.nim_cache_dir();
    remove_tree(ctx, &dir);
    ctx.progress
        .done_with(StepId::RemoveNemotronCache, "Nemotron model files deleted");
    true
}

fn remove_tree(ctx: &mut Ctx, dir: &Path) {
    if !dir.exists() {
        return;
    }
    match std::fs::remove_dir_all(dir) {
        Ok(()) => ctx.log(format!("Removed {}.", dir.display())),
        Err(err) => ctx.warn(format!(
            "Could not remove {}: {err}. You can delete it by hand.",
            dir.display()
        )),
    }
}
