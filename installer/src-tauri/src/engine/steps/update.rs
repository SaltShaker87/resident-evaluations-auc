//! Updating to a newer AUC.
//!
//! The new version is unpacked beside the old one and only becomes `current`
//! once it is ready. If the switch goes wrong, `current` is pointed back at
//! the version that was working and that one is started again — an update that
//! fails should leave a working AUC behind, not a broken one.

use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::engine::process::Cmd;
use crate::engine::release;
use crate::engine::state;
use crate::engine::steps::autostart::{systemctl, systemctl_quiet, SERVICE_UNIT};
use crate::engine::steps::{configure, download, index, install, nemotron, preflight, service};
use crate::engine::types::StepId;
use crate::engine::{app_url, Ctx, FlowResult};
use crate::platform::Platform;

pub fn run(platform: &dyn Platform, ctx: &mut Ctx) -> Result<FlowResult> {
    let system = install::prepare(platform, ctx, false)?;

    let Some(existing) = state::read(&ctx.layout) else {
        anyhow::bail!(
            "There is no record of AUC having been installed by this installer, so there is \
             nothing to update. Use Install instead."
        );
    };
    let installed_version =
        install::current_version(&ctx.layout).unwrap_or_else(|| existing.version.clone());
    let installed_app = ctx.layout.version_dir(&installed_version);

    back_up(ctx, &installed_app)?;

    let downloaded = download::run(ctx, &release::source_from(None))?;
    if downloaded.version == installed_version {
        ctx.log(format!(
            "AUC {installed_version} is already the newest version; its files have been \
             replaced with a fresh copy."
        ));
    }
    let new_app = downloaded.dir.clone();
    if let Some(notes) = &downloaded.notes {
        ctx.log(format!("What is new in {}:", downloaded.version));
        for line in notes.lines() {
            ctx.log(format!("  {line}"));
        }
    }

    platform.install_python(ctx, &new_app)?;

    let configured = configure::run(ctx, configure::Mode::Merge, None)?;
    let port = configured.port.clone();

    // The containers only need touching if the way they are started changed.
    let compose_changed = files_differ(
        &installed_app.join("nim/docker-compose.yml"),
        &new_app.join("nim/docker-compose.yml"),
    );
    if existing.choices.nemotron_enabled && (compose_changed || existing.nemotron_pending) {
        ctx.log("The Nemotron containers are being restarted because their setup changed.");
        nemotron::start_or_defer(ctx, &new_app, None, true)?;
    } else {
        ctx.progress.start(StepId::Nemotron);
        ctx.progress.skipped(
            StepId::Nemotron,
            if existing.choices.nemotron_enabled {
                "The Nemotron containers are unchanged, so they were left running"
            } else {
                "The NVIDIA Nemotron engine is not in use on this machine"
            },
        );
    }

    // Rebuilding the index takes a while and is only necessary when the
    // embedding model changed — an index built with one model cannot be
    // searched with another.
    if configured.embed_model_changed {
        ctx.log("The embedding model changed, so the ACGME index is being rebuilt.");
        index::run(ctx, &new_app)?;
    } else {
        index::skip(ctx, index::Skip::NothingChanged);
    }

    switch(
        ctx,
        &installed_version,
        &downloaded.version,
        &port,
        system.tools.systemd_user,
    )?;

    let mut record = existing.clone();
    record.version = downloaded.version.clone();
    record.nemotron_pending = ctx.nemotron_pending;
    state::write(&ctx.layout, &record)?;

    keep_only_previous(ctx, &downloaded.version, &installed_version);

    preflight::run(ctx, &new_app)?;

    Ok(FlowResult {
        summary: format!(
            "AUC is now version {}. Your data and settings were kept, and a backup was taken \
             first. It is at {}.",
            downloaded.version,
            app_url(&port)
        ),
        app_url: Some(app_url(&port)),
    })
}

/// The `backup` step. An update that cannot back up first does not happen.
fn back_up(ctx: &mut Ctx, installed_app: &Path) -> Result<()> {
    ctx.progress.start(StepId::Backup);
    let python = ctx.layout.venv_python(installed_app);
    let backend = installed_app.join("backend");
    let script = backend.join("backup.py");
    if !python.is_file() || !script.is_file() {
        ctx.progress.skipped(
            StepId::Backup,
            "The installed version has nothing to back up with",
        );
        return Ok(());
    }

    let env = crate::engine::env_for_scripts(&ctx.layout)?;
    Cmd::new(python.display().to_string())
        .arg("backup.py")
        .cwd(&backend)
        .envs(env)
        .timeout(Duration::from_secs(60 * 60))
        .run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            "Backing up your database and photos before updating",
        )?;
    ctx.progress.done_with(
        StepId::Backup,
        format!("Backed up to {}", ctx.layout.backups_dir().display()),
    );
    Ok(())
}

/// The `switch` step: stop, swap the link, start, wait — and put it all back
/// if the new version will not run.
fn switch(
    ctx: &mut Ctx,
    from_version: &str,
    to_version: &str,
    port: &str,
    systemd_user: bool,
) -> Result<()> {
    ctx.progress.start(StepId::Switch);
    if systemd_user {
        systemctl_quiet(ctx, &["stop", SERVICE_UNIT]);
    }
    install::point_current_at(&ctx.layout, to_version)?;
    ctx.log(format!("app/current now points at AUC {to_version}."));

    if !systemd_user {
        ctx.progress.done_with(
            StepId::Switch,
            format!("Version {to_version} is in place; start it by hand"),
        );
        return Ok(());
    }

    let started = systemctl(ctx, &["restart", SERVICE_UNIT], "Starting the new version")
        .and_then(|()| service::wait_until_answering(ctx, port));
    match started {
        Ok(()) => {
            ctx.progress
                .done_with(StepId::Switch, format!("Now running AUC {to_version}"));
            Ok(())
        }
        Err(err) if crate::engine::cancel::is_cancelled_error(&err) => Err(err),
        Err(err) => {
            ctx.log(format!("{err:#}"));
            ctx.log(format!(
                "Putting AUC {from_version} back, because the new version would not start."
            ));
            install::point_current_at(&ctx.layout, from_version)?;
            let _ = systemctl(
                ctx,
                &["restart", SERVICE_UNIT],
                "Starting the previous version",
            );
            let back = service::wait_until_answering(ctx, port);
            ctx.progress
                .failed(StepId::Switch, format!("Went back to AUC {from_version}"));
            match back {
                Ok(()) => Err(err).context(format!(
                    "AUC {to_version} would not start, so version {from_version} was put back and \
                     is running again. Your data was not touched."
                )),
                Err(_) => Err(err).context(format!(
                    "AUC {to_version} would not start, and version {from_version} did not come \
                     back either. Your data was not touched. Try: systemctl --user restart auc"
                )),
            }
        }
    }
}

/// Keep the version we came from, so a problem noticed tomorrow can still be
/// rolled back to it, and remove anything older to save disk space.
fn keep_only_previous(ctx: &mut Ctx, current: &str, previous: &str) {
    let app_dir = ctx.layout.app_dir();
    let Ok(entries) = std::fs::read_dir(&app_dir) else {
        return;
    };
    for entry in entries.flatten() {
        let Some(name) = entry.file_name().to_str().map(str::to_string) else {
            continue;
        };
        if name == current || name == previous || name == "current" || name.starts_with('.') {
            continue;
        }
        if !entry.path().is_dir() {
            continue;
        }
        match std::fs::remove_dir_all(entry.path()) {
            Ok(()) => ctx.log(format!("Removed the older version {name}.")),
            Err(err) => ctx.log(format!("Could not remove the older version {name}: {err}")),
        }
    }
}

fn files_differ(left: &Path, right: &Path) -> bool {
    match (std::fs::read(left), std::fs::read(right)) {
        (Ok(a), Ok(b)) => a != b,
        // If we cannot compare them, assume something changed: restarting the
        // containers costs a few minutes, missing a change costs a broken
        // retrieval engine.
        _ => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_changed_compose_file_is_noticed_and_an_identical_one_is_not() {
        let dir = tempfile::tempdir().expect("temp dir");
        let old = dir.path().join("old.yml");
        let new = dir.path().join("new.yml");
        std::fs::write(&old, "services: {}\n").expect("write");
        std::fs::write(&new, "services: {}\n").expect("write");
        assert!(!files_differ(&old, &new));

        std::fs::write(&new, "services: { nemotron-embed: {} }\n").expect("write");
        assert!(files_differ(&old, &new));
    }

    #[test]
    fn a_file_we_cannot_read_counts_as_changed() {
        let dir = tempfile::tempdir().expect("temp dir");
        let missing = dir.path().join("not-there.yml");
        let present = dir.path().join("there.yml");
        std::fs::write(&present, "services: {}\n").expect("write");
        assert!(files_differ(&missing, &present));
    }
}
