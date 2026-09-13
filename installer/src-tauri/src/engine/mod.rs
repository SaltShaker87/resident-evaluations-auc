//! The engine: everything the installer actually does.
//!
//! It knows nothing about Tauri and nothing about React. It is handed an
//! `Emitter` to describe itself to and a `Cancel` to watch, and it works
//! through the steps CONTRACT.md lists. That is what lets the same code run
//! behind a window and over SSH, and lets the fiddly parts be unit-tested.

pub mod cancel;
pub mod detect;
pub mod emitter;
pub mod envfile;
pub mod guard;
pub mod layout;
pub mod net;
pub mod process;
pub mod recommend;
pub mod release;
pub mod state;
pub mod steps;
pub mod types;

use std::sync::Arc;

use anyhow::Result;

use crate::engine::cancel::{is_cancelled_error, Cancel};
use crate::engine::emitter::Emitter;
use crate::engine::layout::Layout;
use crate::engine::types::{
    Action, Outcome, OutcomeError, StepEvent, StepId, StepStatus, SystemInfo, UpdateInfo,
};

/// What the contract calls this operating system.
///
/// Rust already spells linux, macos and windows the way the contract does.
/// Anything else is reported as itself and turned away by `detect`.
pub fn os_name() -> &'static str {
    std::env::consts::OS
}

/// What the contract calls this processor family.
pub fn arch_name() -> &'static str {
    match std::env::consts::ARCH {
        "x86_64" => "x86_64",
        "aarch64" | "arm64" => "aarch64",
        other => other,
    }
}

/// How we ask for the administrator password: the graphical prompt behind a
/// window, `sudo` in a terminal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Privilege {
    Pkexec,
    Sudo,
}

// ---------------------------------------------------------------------------
// Progress
// ---------------------------------------------------------------------------

/// Draws the step list and keeps it up to date.
///
/// The whole list is announced as `pending` before any work starts, so the
/// screen can show what is coming rather than growing a line at a time.
pub struct Progress {
    emitter: Arc<dyn Emitter>,
    steps: Vec<(StepId, String)>,
}

impl Progress {
    pub fn new(emitter: Arc<dyn Emitter>, plan: &[(StepId, &str)]) -> Progress {
        let steps: Vec<(StepId, String)> = plan
            .iter()
            .map(|(id, label)| (*id, (*label).to_string()))
            .collect();
        let progress = Progress { emitter, steps };
        for (id, label) in &progress.steps {
            progress.emitter.step(StepEvent {
                id: *id,
                label: label.clone(),
                status: StepStatus::Pending,
                detail: None,
                progress: None,
            });
        }
        progress
    }

    fn label(&self, id: StepId) -> String {
        self.steps
            .iter()
            .find(|(step, _)| *step == id)
            .map(|(_, label)| label.clone())
            .unwrap_or_else(|| format!("{id:?}"))
    }

    fn emit(&self, id: StepId, status: StepStatus, detail: Option<String>, progress: Option<f64>) {
        self.emitter.step(StepEvent {
            id,
            label: self.label(id),
            status,
            detail,
            progress,
        });
    }

    pub fn start(&self, id: StepId) {
        self.emit(id, StepStatus::Running, None, None);
    }

    pub fn detail(&self, id: StepId, detail: impl Into<String>) {
        self.emit(id, StepStatus::Running, Some(detail.into()), None);
    }

    /// A detail line and a bar position, for downloads.
    pub fn tick(&self, id: StepId, detail: impl Into<String>, fraction: Option<f64>) {
        self.emit(id, StepStatus::Running, Some(detail.into()), fraction);
    }

    pub fn done(&self, id: StepId) {
        self.emit(id, StepStatus::Done, None, Some(1.0));
    }

    pub fn done_with(&self, id: StepId, detail: impl Into<String>) {
        self.emit(id, StepStatus::Done, Some(detail.into()), Some(1.0));
    }

    pub fn warning(&self, id: StepId, detail: impl Into<String>) {
        self.emit(id, StepStatus::Warning, Some(detail.into()), None);
    }

    pub fn failed(&self, id: StepId, detail: impl Into<String>) {
        self.emit(id, StepStatus::Failed, Some(detail.into()), None);
    }

    pub fn skipped(&self, id: StepId, detail: impl Into<String>) {
        self.emit(id, StepStatus::Skipped, Some(detail.into()), None);
    }
}

// ---------------------------------------------------------------------------
// The run
// ---------------------------------------------------------------------------

/// Everything a step needs: where to report, where things live, whether the
/// user has pressed Cancel, and the notes we are collecting for the summary.
pub struct Ctx {
    pub emitter: Arc<dyn Emitter>,
    pub cancel: Cancel,
    pub layout: Layout,
    pub progress: Progress,
    pub privilege: Privilege,
    /// Things that went less than perfectly but did not stop the install.
    pub warnings: Vec<String>,
    /// Nemotron was chosen but is not answering yet, so AUC runs on Ollama
    /// and the screens can offer to finish it later.
    pub nemotron_pending: bool,
    /// False when `requirements-rag.txt` would not install, which is the one
    /// thing that makes the index step pointless.
    pub rag_installed: bool,
}

impl Ctx {
    pub fn log(&self, line: impl AsRef<str>) {
        self.emitter.log(line.as_ref());
    }

    /// Record something the user should know about at the end.
    pub fn warn(&mut self, text: impl Into<String>) {
        let text = text.into();
        self.emitter.log(&format!("  ⚠ {text}"));
        self.warnings.push(text);
    }
}

/// What a finished flow has to say for itself.
pub struct FlowResult {
    pub summary: String,
    pub app_url: Option<String>,
}

/// Look at this machine. The answer is what the Welcome screen is drawn from.
pub fn detect_system() -> SystemInfo {
    crate::platform::current().detect()
}

/// Is there a newer AUC than the one installed?
pub fn check_for_update() -> UpdateInfo {
    let installed = Layout::detect()
        .ok()
        .and_then(|layout| state::read(&layout))
        .map(|state| state.version);
    match release::latest_release() {
        Ok(release) => UpdateInfo {
            available: installed
                .as_deref()
                .map(|current| current != release.version)
                .unwrap_or(false),
            installed,
            latest: Some(release.version),
            notes: release.notes,
            error: None,
        },
        Err(err) => UpdateInfo {
            installed,
            latest: None,
            available: false,
            notes: None,
            error: Some(err.to_string()),
        },
    }
}

/// Run one action from start to finish. Never returns an error: a failure is
/// an `Outcome` with `ok: false`, because that is what the screens render and
/// what the headless run prints.
pub fn run_action(
    action: Action,
    emitter: Arc<dyn Emitter>,
    cancel: Cancel,
    privilege: Privilege,
) -> Outcome {
    let layout = match Layout::detect() {
        Ok(layout) => layout,
        Err(err) => {
            let outcome = failure_outcome(&err, false, Vec::new(), false);
            emitter.finished(outcome.clone());
            return outcome;
        }
    };

    let plan = steps::plan_for(&action);
    let progress = Progress::new(emitter.clone(), plan);
    let mut ctx = Ctx {
        emitter: emitter.clone(),
        cancel,
        layout,
        progress,
        privilege,
        warnings: Vec::new(),
        nemotron_pending: false,
        rag_installed: true,
    };

    let platform = crate::platform::current();
    let result = steps::dispatch(&action, platform.as_ref(), &mut ctx);

    let outcome = match result {
        Ok(flow) => Outcome {
            ok: true,
            cancelled: false,
            summary: flow.summary,
            warnings: ctx.warnings.clone(),
            app_url: flow.app_url,
            nemotron_pending: ctx.nemotron_pending,
            error: None,
        },
        Err(err) => failure_outcome(
            &err,
            is_cancelled_error(&err),
            ctx.warnings.clone(),
            ctx.nemotron_pending,
        ),
    };
    emitter.finished(outcome.clone());
    outcome
}

fn failure_outcome(
    err: &anyhow::Error,
    cancelled: bool,
    warnings: Vec<String>,
    nemotron_pending: bool,
) -> Outcome {
    let message = err.to_string();
    // The chain is the "why" behind the headline, in the order it happened.
    // It is what the Copy details button hands over.
    let details: Vec<String> = err.chain().skip(1).map(|cause| cause.to_string()).collect();
    Outcome {
        ok: false,
        cancelled,
        summary: if cancelled {
            "Stopped at your request. Nothing else was changed.".to_string()
        } else {
            message.clone()
        },
        warnings,
        app_url: None,
        nemotron_pending,
        error: if cancelled {
            None
        } else {
            Some(OutcomeError {
                message,
                hint: Some(
                    "The log below has every command that ran and what it said. \
                     Copy details when asking for help."
                        .to_string(),
                ),
                details: if details.is_empty() {
                    None
                } else {
                    Some(details.join("\n"))
                },
            })
        },
    }
}

/// The address AUC will answer on, given its settings.
pub fn app_url(port: &str) -> String {
    format!("http://localhost:{port}")
}

/// Load `auc.env` and hand it to a child process, so a script sees exactly
/// what the service sees.
pub fn env_for_scripts(layout: &Layout) -> Result<std::collections::BTreeMap<String, String>> {
    Ok(envfile::EnvFile::load(&layout.env_file())?.map())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::emitter::RecordingEmitter;
    use crate::engine::types::{InstallOptions, NetworkScope};

    #[test]
    fn the_whole_step_list_is_announced_before_any_work_starts() {
        let emitter = RecordingEmitter::new();
        let action = Action::Install {
            options: InstallOptions {
                ai_enabled: true,
                model: Some("qwen3.5:9b".to_string()),
                nemotron_enabled: true,
                ngc_key: None,
                network_scope: NetworkScope::Local,
                adopt_existing: false,
            },
        };
        let progress = Progress::new(emitter.clone(), steps::plan_for(&action));

        let announced = emitter.steps.lock().expect("lock").clone();
        assert_eq!(announced.len(), 12);
        assert!(
            announced.iter().all(|e| e.status == StepStatus::Pending),
            "every step should start as pending so the screen can draw the list"
        );
        assert_eq!(announced[2].id, StepId::Python);
        assert_eq!(announced[2].label, "Setting up Python");

        // And a step's label stays the same once it is running.
        progress.start(StepId::Python);
        let running = emitter
            .steps
            .lock()
            .expect("lock")
            .last()
            .cloned()
            .expect("an event");
        assert_eq!(running.id, StepId::Python);
        assert_eq!(running.label, "Setting up Python");
        assert_eq!(running.status, StepStatus::Running);
    }

    #[test]
    fn a_cancelled_run_is_reported_as_cancelled_rather_than_as_a_failure() {
        let err = anyhow::Error::new(cancel::Cancelled)
            .context("Downloading the model qwen3.5:9b did not finish.");
        let outcome = failure_outcome(&err, is_cancelled_error(&err), Vec::new(), false);
        assert!(!outcome.ok);
        assert!(outcome.cancelled);
        assert!(
            outcome.error.is_none(),
            "a cancellation is not an error to report"
        );
        assert!(outcome.summary.contains("Stopped at your request"));
    }

    #[test]
    fn a_failure_keeps_the_headline_the_reasons_and_any_warnings() {
        let err = anyhow::anyhow!("Ollama is not answering at http://localhost:11434.")
            .context("The models could not be downloaded.");
        let outcome = failure_outcome(
            &err,
            false,
            vec!["The ACGME index layer did not install.".to_string()],
            true,
        );
        assert!(!outcome.ok && !outcome.cancelled);
        assert_eq!(outcome.summary, "The models could not be downloaded.");
        let error = outcome.error.expect("there should be an error");
        assert!(error.details.expect("details").contains("not answering"));
        assert_eq!(outcome.warnings.len(), 1);
        assert!(outcome.nemotron_pending);
        assert!(outcome.app_url.is_none());
    }
}

/// A run that reports to nothing and touches nothing, for tests that only
/// want to see how a step behaves.
#[cfg(test)]
pub mod test_support {
    use super::*;
    use crate::engine::emitter::RecordingEmitter;

    pub fn ctx() -> Ctx {
        let emitter = RecordingEmitter::new();
        let layout = Layout::rooted_at(std::path::Path::new("/tmp/auc-installer-test"));
        Ctx {
            emitter: emitter.clone(),
            cancel: Cancel::new(),
            layout,
            progress: Progress::new(emitter, &[]),
            privilege: Privilege::Sudo,
            warnings: Vec::new(),
            nemotron_pending: false,
            rag_installed: true,
        }
    }
}
