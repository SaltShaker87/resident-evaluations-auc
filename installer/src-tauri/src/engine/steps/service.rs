//! The `start` step, and the service handling Update and Uninstall share.

use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};

use crate::engine::steps::autostart::{
    systemctl, systemctl_quiet, BACKUP_TIMER_UNIT, SERVICE_UNIT,
};
use crate::engine::types::StepId;
use crate::engine::{app_url, net, Ctx};

/// How long AUC is given to answer. It has a Python interpreter and an index
/// to open, so a few seconds is normal and a minute is the outside edge.
const START_TIMEOUT: Duration = Duration::from_secs(60);

/// The `start` step: restart the service and wait until the app answers.
pub fn start_and_wait(ctx: &mut Ctx, port: &str, systemd_user: bool) -> Result<()> {
    ctx.progress.start(StepId::Start);
    if !systemd_user {
        ctx.progress.skipped(
            StepId::Start,
            "There is no systemd user session here, so AUC has to be started by hand",
        );
        return Ok(());
    }

    systemctl(ctx, &["restart", SERVICE_UNIT], "Starting AUC")?;
    wait_until_answering(ctx, port)?;
    ctx.progress
        .done_with(StepId::Start, format!("AUC is at {}", app_url(port)));
    Ok(())
}

/// Poll `/api/auth/status`, which answers as soon as the app is serving.
pub fn wait_until_answering(ctx: &mut Ctx, port: &str) -> Result<()> {
    let url = format!("{}/api/auth/status", app_url(port));
    let deadline = Instant::now() + START_TIMEOUT;
    loop {
        ctx.cancel.check()?;
        if net::get_ok(&url, Duration::from_secs(3)) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(anyhow!(
                "AUC did not answer on port {port} within a minute of being started. \
                 See what it said with: journalctl --user -u auc -n 50"
            ));
        }
        ctx.progress.detail(
            StepId::Start,
            format!("Waiting for AUC to answer on port {port}"),
        );
        std::thread::sleep(Duration::from_secs(2));
    }
}

/// Stop the service and the timer, quietly: a unit that is already stopped,
/// or was never there, is not a problem.
pub fn stop_everything(ctx: &mut Ctx) {
    systemctl_quiet(ctx, &["stop", SERVICE_UNIT]);
    systemctl_quiet(ctx, &["stop", BACKUP_TIMER_UNIT]);
}
