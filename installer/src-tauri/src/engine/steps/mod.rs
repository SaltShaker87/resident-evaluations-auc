//! The steps, and the order they run in.
//!
//! Each action's whole step list is declared here in one place, because the
//! screen draws it before any work starts and the headless run numbers its
//! lines from it ("[3/12] Setting up Python"). CONTRACT.md section 4 is the
//! list; this is it in code.

pub mod adopt;
pub mod autostart;
pub mod configure;
pub mod download;
pub mod finish_nemotron;
pub mod index;
pub mod install;
pub mod nemotron;
pub mod ollama;
pub mod preflight;
pub mod python;
pub mod repair;
pub mod service;
pub mod uninstall;
pub mod update;

use anyhow::Result;

use crate::engine::types::{Action, StepId};
use crate::engine::{Ctx, FlowResult};
use crate::platform::Platform;

/// install and repair run the same steps: repair is an install that expects
/// to find most of the work already done.
const INSTALL_STEPS: &[(StepId, &str)] = &[
    (StepId::Prepare, "Getting ready"),
    (StepId::Download, "Downloading AUC"),
    (StepId::Python, "Setting up Python"),
    (StepId::Configure, "Writing settings"),
    (StepId::Ollama, "Installing Ollama"),
    (StepId::Models, "Downloading AI models"),
    (StepId::Docker, "Setting up Docker for NVIDIA Nemotron"),
    (StepId::Nemotron, "Starting NVIDIA Nemotron"),
    (StepId::Index, "Building the ACGME reference index"),
    (StepId::Autostart, "Setting up auto-start"),
    (StepId::Start, "Starting AUC"),
    (StepId::Preflight, "Checking everything works"),
];

const UPDATE_STEPS: &[(StepId, &str)] = &[
    (StepId::Prepare, "Getting ready"),
    (StepId::Backup, "Backing up your data"),
    (StepId::Download, "Downloading AUC"),
    (StepId::Python, "Setting up Python"),
    (StepId::Configure, "Writing settings"),
    (StepId::Nemotron, "Starting NVIDIA Nemotron"),
    (StepId::Index, "Building the ACGME reference index"),
    (StepId::Switch, "Switching to the new version"),
    (StepId::Preflight, "Checking everything works"),
];

const FINISH_NEMOTRON_STEPS: &[(StepId, &str)] = &[
    (StepId::Prepare, "Getting ready"),
    (StepId::Docker, "Setting up Docker for NVIDIA Nemotron"),
    (StepId::Nemotron, "Starting NVIDIA Nemotron"),
    (StepId::Index, "Building the ACGME reference index"),
    (StepId::Configure, "Writing settings"),
    (StepId::Start, "Starting AUC"),
    (StepId::Preflight, "Checking everything works"),
];

const UNINSTALL_STEPS: &[(StepId, &str)] = &[
    (StepId::Stop, "Stopping AUC"),
    (StepId::RemoveAutostart, "Removing auto-start"),
    (StepId::RemoveApp, "Removing the application"),
    (StepId::RemoveData, "Removing your data"),
    (StepId::RemoveNemotronCache, "Removing the Nemotron models"),
];

pub fn plan_for(action: &Action) -> &'static [(StepId, &'static str)] {
    match action {
        Action::Install { .. } | Action::Repair => INSTALL_STEPS,
        Action::Update => UPDATE_STEPS,
        Action::FinishNemotron { .. } => FINISH_NEMOTRON_STEPS,
        Action::Uninstall { .. } => UNINSTALL_STEPS,
    }
}

pub fn dispatch(action: &Action, platform: &dyn Platform, ctx: &mut Ctx) -> Result<FlowResult> {
    match action {
        Action::Install { options } => install::run(platform, ctx, options.clone(), false),
        Action::Repair => repair::run(platform, ctx),
        Action::Update => update::run(platform, ctx),
        Action::FinishNemotron { ngc_key } => {
            finish_nemotron::run(platform, ctx, ngc_key.as_deref())
        }
        Action::Uninstall {
            delete_data,
            delete_nemotron_cache,
        } => uninstall::run(ctx, *delete_data, *delete_nemotron_cache),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::types::{InstallOptions, NetworkScope};

    fn install_action() -> Action {
        Action::Install {
            options: InstallOptions {
                ai_enabled: true,
                model: Some("qwen3.5:9b".to_string()),
                nemotron_enabled: false,
                ngc_key: None,
                network_scope: NetworkScope::Local,
                adopt_existing: false,
            },
        }
    }

    #[test]
    fn the_step_lists_are_the_ones_the_contract_sets_out() {
        assert_eq!(plan_for(&install_action()).len(), 12);
        assert_eq!(plan_for(&Action::Repair).len(), 12);
        assert_eq!(plan_for(&Action::Update).len(), 9);
        assert_eq!(plan_for(&Action::FinishNemotron { ngc_key: None }).len(), 7);
        assert_eq!(
            plan_for(&Action::Uninstall {
                delete_data: false,
                delete_nemotron_cache: false
            })
            .len(),
            5
        );
    }

    #[test]
    fn install_starts_by_getting_ready_and_ends_by_checking_everything_works() {
        let plan = plan_for(&install_action());
        assert_eq!(plan.first().map(|s| s.0), Some(StepId::Prepare));
        assert_eq!(plan.last().map(|s| s.0), Some(StepId::Preflight));
    }

    #[test]
    fn no_step_is_listed_twice_in_any_plan() {
        for action in [
            install_action(),
            Action::Update,
            Action::FinishNemotron { ngc_key: None },
            Action::Uninstall {
                delete_data: true,
                delete_nemotron_cache: true,
            },
        ] {
            let plan = plan_for(&action);
            let mut ids: Vec<StepId> = plan.iter().map(|(id, _)| *id).collect();
            let before = ids.len();
            ids.sort_by_key(|id| format!("{id:?}"));
            ids.dedup();
            assert_eq!(ids.len(), before, "a step is repeated in {action:?}");
        }
    }

    #[test]
    fn the_json_names_of_the_step_ids_are_the_contracts_names() {
        let names: Vec<String> = INSTALL_STEPS
            .iter()
            .map(|(id, _)| {
                serde_json::to_value(id)
                    .expect("serialise")
                    .as_str()
                    .expect("a string")
                    .to_string()
            })
            .collect();
        assert_eq!(
            names,
            vec![
                "prepare",
                "download",
                "python",
                "configure",
                "ollama",
                "models",
                "docker",
                "nemotron",
                "index",
                "autostart",
                "start",
                "preflight",
            ]
        );
    }
}
