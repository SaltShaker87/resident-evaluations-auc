//! The `autostart` step: the three systemd units, lingering, and a menu icon.
//!
//! The units are written with systemd's `%h` rather than an absolute home
//! folder, so the same text is right on every machine. Lingering is the single
//! most common headless surprise: without it, user services only run while
//! someone is logged in at the console, which on a machine reached over SSH is
//! never.

use std::path::Path;
use std::time::Duration;

use anyhow::{Context, Result};

use crate::engine::layout::{ensure_dir, Layout};
use crate::engine::process::{have, Cmd};
use crate::engine::types::StepId;
use crate::engine::Ctx;
use crate::platform::Platform;

pub const SERVICE_UNIT: &str = "auc.service";
pub const BACKUP_SERVICE_UNIT: &str = "auc-backup.service";
pub const BACKUP_TIMER_UNIT: &str = "auc-backup.timer";
pub const UNITS: [&str; 3] = [SERVICE_UNIT, BACKUP_SERVICE_UNIT, BACKUP_TIMER_UNIT];

/// The two folders the units refer to, written the way the unit file will
/// spell them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnitPaths {
    pub auc_home: String,
    pub config_dir: String,
}

impl UnitPaths {
    /// The normal case: both folders are in the user's home, so `%h` says it
    /// once and correctly for whoever is logged in.
    pub fn standard() -> UnitPaths {
        UnitPaths {
            auc_home: "%h/.local/share/auc".to_string(),
            config_dir: "%h/.config/auc".to_string(),
        }
    }

    /// If AUC_HOME moved the tree somewhere else, `%h` would be a lie, so the
    /// real path is written instead.
    pub fn for_layout(layout: &Layout) -> UnitPaths {
        let standard = UnitPaths::standard();
        UnitPaths {
            auc_home: if layout.auc_home == layout.home.join(".local/share/auc") {
                standard.auc_home
            } else {
                layout.auc_home.display().to_string()
            },
            config_dir: if layout.config_dir == layout.home.join(".config/auc") {
                standard.config_dir
            } else {
                layout.config_dir.display().to_string()
            },
        }
    }
}

/// The three units, exactly as CONTRACT.md sets them out.
pub fn render_units(paths: &UnitPaths) -> Vec<(&'static str, String)> {
    let app = format!("{}/app/current", paths.auc_home);
    let env_file = format!("{}/auc.env", paths.config_dir);
    vec![
        (
            SERVICE_UNIT,
            format!(
                "[Unit]\n\
                 Description=AUC — Assessments Under Curve\n\
                 After=network.target\n\
                 \n\
                 [Service]\n\
                 Type=simple\n\
                 WorkingDirectory={app}/backend\n\
                 ExecStart={app}/run.sh\n\
                 EnvironmentFile={env_file}\n\
                 Restart=on-failure\n\
                 RestartSec=5\n\
                 \n\
                 [Install]\n\
                 WantedBy=default.target\n"
            ),
        ),
        (
            BACKUP_SERVICE_UNIT,
            format!(
                "[Unit]\n\
                 Description=AUC daily backup (database + photos)\n\
                 \n\
                 [Service]\n\
                 Type=oneshot\n\
                 WorkingDirectory={app}/backend\n\
                 ExecStart={app}/backend/venv/bin/python backup.py\n\
                 EnvironmentFile={env_file}\n"
            ),
        ),
        (
            BACKUP_TIMER_UNIT,
            // Persistent=true matters on a desktop that is switched off
            // overnight: a 02:00 schedule never fires, so it runs at the next
            // boot instead.
            "[Unit]\n\
             Description=Run AUC backup once a day\n\
             \n\
             [Timer]\n\
             OnCalendar=*-*-* 02:00:00\n\
             Persistent=true\n\
             \n\
             [Install]\n\
             WantedBy=timers.target\n"
                .to_string(),
        ),
    ]
}

/// The menu icon. `xdg-open` rather than a browser by name, so it opens
/// whichever browser the user actually uses.
pub fn render_desktop_file(port: &str) -> String {
    format!(
        "[Desktop Entry]\n\
         Type=Application\n\
         Name=AUC\n\
         Comment=Assessments Under Curve\n\
         Exec=xdg-open http://localhost:{port}\n\
         Icon=web-browser\n\
         Terminal=false\n\
         Categories=Office;\n"
    )
}

pub fn install(
    platform: &dyn Platform,
    ctx: &mut Ctx,
    port: &str,
    systemd_user: bool,
) -> Result<()> {
    ctx.progress.start(StepId::Autostart);

    // The desktop icon is worth having either way, and costs nothing.
    write_desktop_file(ctx, port)?;

    if !systemd_user {
        ctx.warn(format!(
            "There is no systemd user session on this machine, so AUC cannot be set to start by \
             itself. It is installed and ready; start it by hand with: bash {}/run.sh — or run \
             the installer again from a normal login session.",
            ctx.layout.current_link().display()
        ));
        ctx.progress
            .warning(StepId::Autostart, "No systemd user session here");
        return Ok(());
    }

    platform.install_autostart(ctx)?;
    ctx.progress.done(StepId::Autostart);
    Ok(())
}

/// Write the units, reload, enable and start them.
pub fn write_and_enable_units(ctx: &mut Ctx) -> Result<()> {
    let dir = ctx.layout.systemd_user_dir();
    ensure_dir(&dir)?;
    let paths = UnitPaths::for_layout(&ctx.layout);
    for (name, contents) in render_units(&paths) {
        write_unit(ctx, &dir.join(name), &contents)?;
    }

    systemctl(
        ctx,
        &["daemon-reload"],
        "Telling systemd about the new units",
    )?;
    systemctl(
        ctx,
        &["enable", SERVICE_UNIT, BACKUP_TIMER_UNIT],
        "Setting AUC to start with the machine",
    )?;
    // Idempotent, and it means a repair leaves a running app behind even if
    // the `start` step is never reached.
    systemctl(ctx, &["restart", SERVICE_UNIT], "Starting AUC")?;
    systemctl(
        ctx,
        &["start", BACKUP_TIMER_UNIT],
        "Starting the daily backup timer",
    )?;
    Ok(())
}

/// Keep the previous copy when it differs, the way `setup.sh` does, so a
/// hand-edited unit is never silently thrown away.
fn write_unit(ctx: &mut Ctx, path: &Path, contents: &str) -> Result<()> {
    if let Ok(existing) = std::fs::read_to_string(path) {
        if existing == contents {
            ctx.log(format!("{} is already correct.", path.display()));
            return Ok(());
        }
        let backup = path.with_extension(format!(
            "bak-{}",
            chrono::Local::now().format("%Y%m%d-%H%M%S")
        ));
        if std::fs::copy(path, &backup).is_ok() {
            ctx.log(format!(
                "{} was replaced; the previous version is kept as {}.",
                path.display(),
                backup.display()
            ));
        }
    }
    std::fs::write(path, contents)
        .with_context(|| format!("Could not write {}.", path.display()))?;
    ctx.log(format!("Wrote {}.", path.display()));
    Ok(())
}

/// Turn on lingering, without the password if we can and with it if we must.
pub fn enable_linger(platform: &dyn Platform, ctx: &mut Ctx) -> Result<()> {
    let user = current_user();
    if !have("loginctl") {
        return Ok(());
    }
    let already = Cmd::new("loginctl")
        .args(["show-user", &user, "-p", "Linger", "--value"])
        .quiet()
        .run(ctx.emitter.as_ref(), &ctx.cancel)
        .map(|out| out.text().trim() == "yes")
        .unwrap_or(false);
    if already {
        ctx.log("Lingering is already on, so AUC starts at boot without anyone logging in.");
        return Ok(());
    }

    let unprivileged = Cmd::new("loginctl")
        .args(["enable-linger", &user])
        .timeout(Duration::from_secs(30))
        .run(ctx.emitter.as_ref(), &ctx.cancel)?;
    if unprivileged.success {
        ctx.log("Lingering enabled — AUC will start at boot without anyone logging in.");
        return Ok(());
    }

    // It usually needs root, and it matters enough to ask.
    let script = format!(
        "#!/bin/bash\n\
         # AUC installer — letting AUC run without anyone logged in at the console.\n\
         set -euo pipefail\n\
         loginctl enable-linger {}\n",
        super::nemotron::shell_quote(&user)
    );
    match platform.privileged_run(ctx, "Allowing AUC to start at boot", &script, None) {
        Ok(()) => {
            ctx.log("Lingering enabled — AUC will start at boot without anyone logging in.");
            Ok(())
        }
        Err(err) => {
            ctx.log(format!("{err:#}"));
            ctx.warn(format!(
                "Lingering could not be turned on, so AUC will only start once someone logs in at \
                 the machine. To fix it later: sudo loginctl enable-linger {user}"
            ));
            Ok(())
        }
    }
}

fn write_desktop_file(ctx: &mut Ctx, port: &str) -> Result<()> {
    let path = ctx.layout.desktop_file();
    if let Some(parent) = path.parent() {
        ensure_dir(parent)?;
    }
    std::fs::write(&path, render_desktop_file(port))
        .with_context(|| format!("Could not write the menu icon {}.", path.display()))?;
    ctx.log(format!("Wrote the AUC menu icon at {}.", path.display()));

    // Some desktops only notice a new icon once their database is rebuilt.
    if have("update-desktop-database") {
        if let Some(dir) = path.parent() {
            let _ = Cmd::new("update-desktop-database")
                .arg(dir.display().to_string())
                .quiet()
                .run(ctx.emitter.as_ref(), &ctx.cancel);
        }
    }
    Ok(())
}

pub fn systemctl(ctx: &Ctx, args: &[&str], what: &str) -> Result<()> {
    let mut full = vec!["--user"];
    full.extend_from_slice(args);
    Cmd::new("systemctl")
        .args(full)
        .timeout(Duration::from_secs(120))
        .run_ok(ctx.emitter.as_ref(), &ctx.cancel, what)?;
    Ok(())
}

/// Best effort: used while taking things down, where a unit that is already
/// gone is not a problem.
pub fn systemctl_quiet(ctx: &Ctx, args: &[&str]) {
    let mut full = vec!["--user"];
    full.extend_from_slice(args);
    let _ = Cmd::new("systemctl")
        .args(full)
        .timeout(Duration::from_secs(120))
        .run(ctx.emitter.as_ref(), &ctx.cancel);
}

pub fn current_user() -> String {
    std::env::var("USER")
        .or_else(|_| std::env::var("LOGNAME"))
        .ok()
        .filter(|u| !u.is_empty())
        .or_else(|| crate::engine::process::capture("id", &["-un"]).map(|o| o.trim().to_string()))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn unit(name: &str) -> String {
        render_units(&UnitPaths::standard())
            .into_iter()
            .find(|(unit_name, _)| *unit_name == name)
            .map(|(_, text)| text)
            .expect("unit should be rendered")
    }

    #[test]
    fn the_service_unit_is_word_for_word_what_the_contract_shows() {
        assert_eq!(
            unit(SERVICE_UNIT),
            "[Unit]\n\
             Description=AUC — Assessments Under Curve\n\
             After=network.target\n\
             \n\
             [Service]\n\
             Type=simple\n\
             WorkingDirectory=%h/.local/share/auc/app/current/backend\n\
             ExecStart=%h/.local/share/auc/app/current/run.sh\n\
             EnvironmentFile=%h/.config/auc/auc.env\n\
             Restart=on-failure\n\
             RestartSec=5\n\
             \n\
             [Install]\n\
             WantedBy=default.target\n"
        );
    }

    #[test]
    fn the_backup_service_runs_backup_py_from_the_venv_with_the_same_settings() {
        let text = unit(BACKUP_SERVICE_UNIT);
        assert!(text.contains("Type=oneshot"));
        assert!(text.contains(
            "ExecStart=%h/.local/share/auc/app/current/backend/venv/bin/python backup.py"
        ));
        assert!(text.contains("WorkingDirectory=%h/.local/share/auc/app/current/backend"));
        assert!(text.contains("EnvironmentFile=%h/.config/auc/auc.env"));
    }

    #[test]
    fn the_timer_runs_at_two_in_the_morning_and_catches_up_after_a_night_switched_off() {
        let text = unit(BACKUP_TIMER_UNIT);
        assert!(text.contains("OnCalendar=*-*-* 02:00:00"));
        assert!(text.contains("Persistent=true"));
        assert!(text.contains("WantedBy=timers.target"));
    }

    #[test]
    fn three_units_are_written_and_no_more() {
        let units = render_units(&UnitPaths::standard());
        assert_eq!(units.len(), 3);
        let names: Vec<&str> = units.iter().map(|(name, _)| *name).collect();
        assert_eq!(
            names,
            vec![SERVICE_UNIT, BACKUP_SERVICE_UNIT, BACKUP_TIMER_UNIT]
        );
    }

    #[test]
    fn moving_auc_home_writes_real_paths_instead_of_a_misleading_percent_h() {
        let layout = Layout {
            auc_home: "/srv/auc".into(),
            config_dir: "/srv/auc-config".into(),
            home: "/home/you".into(),
        };
        let text = render_units(&UnitPaths::for_layout(&layout))
            .into_iter()
            .next()
            .expect("the service unit")
            .1;
        assert!(text.contains("WorkingDirectory=/srv/auc/app/current/backend"));
        assert!(text.contains("EnvironmentFile=/srv/auc-config/auc.env"));
        assert!(!text.contains("%h"));
    }

    #[test]
    fn the_ordinary_layout_still_uses_percent_h() {
        let layout = Layout::rooted_at(Path::new("/home/you"));
        assert_eq!(UnitPaths::for_layout(&layout), UnitPaths::standard());
    }

    #[test]
    fn the_menu_icon_opens_the_port_the_app_is_on() {
        let text = render_desktop_file("3100");
        assert!(text.contains("Exec=xdg-open http://localhost:3100"));
        assert!(text.contains("Type=Application"));
        assert!(text.contains("Name=AUC"));
        assert!(text.contains("Icon=web-browser"));
        assert!(text.contains("Categories=Office;"));
    }
}
