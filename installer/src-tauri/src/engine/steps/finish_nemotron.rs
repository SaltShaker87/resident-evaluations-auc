//! Finishing the NVIDIA Nemotron setup after an install that carried on
//! without it — usually because the NGC API key was not to hand at the time,
//! or the several-gigabyte weights download did not finish.

use anyhow::Result;

use crate::engine::state;
use crate::engine::steps::{configure, index, install, nemotron, preflight, service};
use crate::engine::types::StepId;
use crate::engine::{app_url, Ctx, FlowResult};
use crate::platform::Platform;

pub fn run(platform: &dyn Platform, ctx: &mut Ctx, ngc_key: Option<&str>) -> Result<FlowResult> {
    let system = install::prepare(platform, ctx, false)?;

    let Some(existing) = state::read(&ctx.layout) else {
        anyhow::bail!(
            "AUC does not appear to be installed on this machine yet, so there is no Nemotron \
             setup to finish. Install AUC first."
        );
    };

    // Whatever `current` points at; that is the copy the service runs.
    let app = if ctx.layout.current_link().exists() {
        ctx.layout.current_link()
    } else {
        ctx.layout.version_dir(&existing.version)
    };
    let port = configure::port_on_disk(&ctx.layout);

    nemotron::docker_and_start(platform, ctx, &app, &system, ngc_key, true)?;

    if ctx.nemotron_pending {
        index::skip(ctx, index::Skip::NothingChanged);
    } else {
        index::run(ctx, &app)?;
    }

    // The `configure` step of this action has one job: take away the line that
    // pins AUC to the Standard engine, now that Nemotron can answer.
    ctx.progress.start(StepId::Configure);
    if ctx.nemotron_pending {
        ctx.progress.warning(
            StepId::Configure,
            "AUC stays on the Standard engine until the containers answer",
        );
    } else {
        configure::set_engine_default(ctx, None)?;
        ctx.progress.done_with(
            StepId::Configure,
            "AUC will use NVIDIA Nemotron from now on",
        );
    }

    service::start_and_wait(ctx, &port, system.tools.systemd_user)?;
    preflight::run(ctx, &app)?;

    let mut record = existing.clone();
    record.choices.nemotron_enabled = true;
    record.nemotron_pending = ctx.nemotron_pending;
    state::write(&ctx.layout, &record)?;

    Ok(FlowResult {
        summary: if ctx.nemotron_pending {
            "The NVIDIA Nemotron containers still are not answering, so AUC is still using the \
             Standard engine. The log says how far it got."
                .to_string()
        } else {
            "NVIDIA Nemotron is running, and AUC will use it for summaries from now on.".to_string()
        },
        app_url: Some(app_url(&port)),
    })
}
