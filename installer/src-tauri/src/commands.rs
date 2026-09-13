//! The commands the screens call, and the one rule that governs them: long
//! work never happens inside a command.
//!
//! `run_action` starts a thread and returns at once; everything after that
//! arrives as an `auc://` event. That is what keeps the window answering while
//! a 5 GB model downloads.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::{AppHandle, State};

use crate::engine::cancel::Cancel;
use crate::engine::emitter::{LogSink, TauriEmitter};
use crate::engine::layout::Layout;
use crate::engine::steps::configure;
use crate::engine::types::{Action, InstallerState, Recommendation, SystemInfo, UpdateInfo};
use crate::engine::{self as engine, Privilege};

/// The hosts the screens are allowed to send the user to. Everything else is
/// refused, so a link can never be turned into a way to open anything at all.
const ALLOWED_HOSTS: [&str; 3] = ["ngc.nvidia.com", "github.com", "ollama.com"];

/// Guards the one thing that must not happen twice at once: an action.
pub struct ActionRunner {
    running: AtomicBool,
    cancel: Mutex<Cancel>,
    log: Mutex<Option<Arc<LogSink>>>,
}

impl ActionRunner {
    pub fn new() -> ActionRunner {
        ActionRunner {
            running: AtomicBool::new(false),
            cancel: Mutex::new(Cancel::new()),
            log: Mutex::new(None),
        }
    }

    /// Claim the runner, or say why not. A fresh `Cancel` each time, so an
    /// earlier cancellation cannot stop the next run before it starts.
    fn begin(&self) -> Result<Cancel, String> {
        if self.running.swap(true, Ordering::SeqCst) {
            return Err(
                "Something is already running. Wait for it to finish, or cancel it first."
                    .to_string(),
            );
        }
        let fresh = Cancel::new();
        if let Ok(mut cancel) = self.cancel.lock() {
            *cancel = fresh.clone();
        }
        Ok(fresh)
    }

    fn finish(&self) {
        self.running.store(false, Ordering::SeqCst);
    }

    fn request_cancel(&self) {
        if let Ok(cancel) = self.cancel.lock() {
            cancel.cancel();
        }
    }

    fn remember_log(&self, log: Arc<LogSink>) {
        if let Ok(mut slot) = self.log.lock() {
            *slot = Some(log);
        }
    }

    fn log_text(&self) -> String {
        self.log
            .lock()
            .ok()
            .and_then(|slot| slot.as_ref().map(|log| log.contents()))
            .unwrap_or_default()
    }
}

impl Default for ActionRunner {
    fn default() -> Self {
        ActionRunner::new()
    }
}

// ---------------------------------------------------------------------------
// The commands
// ---------------------------------------------------------------------------

#[tauri::command]
fn detect_system() -> Result<SystemInfo, String> {
    Ok(engine::detect_system())
}

#[tauri::command]
fn recommend(system: SystemInfo) -> Result<Recommendation, String> {
    Ok(engine::recommend::recommend(&system))
}

#[tauri::command]
fn read_state() -> Result<Option<InstallerState>, String> {
    let layout = Layout::detect().map_err(|err| err.to_string())?;
    Ok(engine::state::read(&layout))
}

#[tauri::command]
fn check_for_update() -> Result<UpdateInfo, String> {
    Ok(engine::check_for_update())
}

#[tauri::command]
fn run_action(
    action: Action,
    app: AppHandle,
    runner: State<'_, Arc<ActionRunner>>,
) -> Result<(), String> {
    let cancel = runner.begin()?;
    let layout = Layout::detect().map_err(|err| {
        runner.finish();
        err.to_string()
    })?;

    let log = LogSink::open(&layout);
    runner.remember_log(log.clone());
    let emitter = Arc::new(TauriEmitter::new(app, log));
    let runner = runner.inner().clone();

    // Its own thread: the command has to return now so the window keeps
    // drawing, and everything from here on is an event.
    std::thread::Builder::new()
        .name("auc-installer-action".to_string())
        .spawn(move || {
            engine::run_action(action, emitter, cancel, Privilege::Pkexec);
            runner.finish();
        })
        .map_err(|err| format!("Could not start the installer's work in the background: {err}"))?;
    Ok(())
}

#[tauri::command]
fn cancel_action(runner: State<'_, Arc<ActionRunner>>) -> Result<(), String> {
    runner.request_cancel();
    Ok(())
}

#[tauri::command]
fn get_log(runner: State<'_, Arc<ActionRunner>>) -> Result<String, String> {
    Ok(runner.log_text())
}

#[tauri::command]
fn open_app() -> Result<(), String> {
    let layout = Layout::detect().map_err(|err| err.to_string())?;
    let port = configure::port_on_disk(&layout);
    crate::platform::current()
        .open_browser(&engine::app_url(&port))
        .map_err(|err| err.to_string())
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    if !is_allowed_url(&url) {
        return Err(format!(
            "The installer will not open {url}. It only opens ngc.nvidia.com, github.com and \
             ollama.com."
        ));
    }
    crate::platform::current()
        .open_browser(&url)
        .map_err(|err| err.to_string())
}

/// https, and one of three hosts. Nothing else.
pub fn is_allowed_url(url: &str) -> bool {
    if !url.starts_with("https://") {
        return false;
    }
    let Some(host) = crate::engine::guard::host_of(url) else {
        return false;
    };
    let host = host.trim_start_matches("www.").to_ascii_lowercase();
    ALLOWED_HOSTS.contains(&host.as_str())
}

/// Start the window.
pub fn start_gui() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(ActionRunner::new()))
        .invoke_handler(tauri::generate_handler![
            detect_system,
            recommend,
            read_state,
            check_for_update,
            run_action,
            cancel_action,
            get_log,
            open_app,
            open_url,
        ])
        .run(tauri::generate_context!())
        .expect("The AUC installer window could not be opened.");
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_the_three_hosts_in_the_contract_can_be_opened() {
        assert!(is_allowed_url("https://ngc.nvidia.com/setup"));
        assert!(is_allowed_url(
            "https://github.com/SaltShaker87/resident-evaluations-auc"
        ));
        assert!(is_allowed_url("https://ollama.com/download"));
        assert!(is_allowed_url("https://www.github.com/"));
    }

    #[test]
    fn anything_else_is_refused() {
        assert!(!is_allowed_url("https://example.org"));
        assert!(
            !is_allowed_url("http://github.com"),
            "plain http is refused"
        );
        assert!(!is_allowed_url("file:///etc/passwd"));
        assert!(!is_allowed_url("https://github.com.evil.test/"));
        assert!(!is_allowed_url(""));
    }

    #[test]
    fn only_one_action_can_run_at_a_time() {
        let runner = ActionRunner::new();
        let first = runner.begin().expect("the first one starts");
        let second = runner.begin();
        assert!(second.is_err(), "a second action must be refused");
        assert!(second.expect_err("refused").contains("already running"));

        runner.finish();
        runner
            .begin()
            .expect("after it finishes, another can start");
        // The new run gets its own flag, so an old Cancel cannot leak in.
        first.cancel();
        assert!(!runner.cancel.lock().expect("lock").is_cancelled());
    }

    #[test]
    fn cancelling_sets_the_flag_the_running_action_is_watching() {
        let runner = ActionRunner::new();
        let cancel = runner.begin().expect("started");
        assert!(!cancel.is_cancelled());
        runner.request_cancel();
        assert!(cancel.is_cancelled());
    }
}
