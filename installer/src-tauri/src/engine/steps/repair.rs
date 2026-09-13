//! Repair: the install steps again, with the choices already on record.
//!
//! It exists because "run it again" is what everyone tries first, and because
//! every step in `install` is written to be safe to repeat.

use anyhow::Result;

use crate::engine::recommend;
use crate::engine::state;
use crate::engine::types::{InstallOptions, NemotronMode, NetworkScope};
use crate::engine::{Ctx, FlowResult};
use crate::platform::Platform;

pub fn run(platform: &dyn Platform, ctx: &mut Ctx) -> Result<FlowResult> {
    let options = match state::read(&ctx.layout) {
        Some(existing) => {
            ctx.log(format!(
                "Repairing the AUC {} install, keeping the choices already made.",
                existing.version
            ));
            InstallOptions {
                ai_enabled: existing.choices.ai_enabled,
                model: existing.choices.model.clone(),
                nemotron_enabled: existing.choices.nemotron_enabled,
                // Never stored, so a repair that needs to sign in to NVIDIA's
                // registry again relies on the images already being cached.
                ngc_key: None,
                network_scope: existing.choices.network_scope,
                adopt_existing: false,
            }
        }
        None => {
            // No record of what was chosen, so fall back to what this machine
            // would be offered on a fresh install.
            let recommendation = recommend::recommend(&platform.detect());
            ctx.log(
                "There is no record of the original choices, so the ones this machine would be \
                 offered on a fresh install are used.",
            );
            InstallOptions {
                ai_enabled: recommendation.ai.recommended,
                model: recommendation.default_model().map(str::to_string),
                nemotron_enabled: recommendation.nemotron.mode == NemotronMode::Default,
                ngc_key: None,
                network_scope: NetworkScope::Local,
                adopt_existing: false,
            }
        }
    };

    super::install::run(platform, ctx, options, true)
}
