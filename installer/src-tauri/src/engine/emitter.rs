//! How the engine talks about what it is doing.
//!
//! The engine knows nothing about Tauri. It holds an `Emitter` and describes
//! its progress to it; the window version turns that into `auc://` events and
//! the headless version prints it. Both write every log line to a file under
//! $AUC_HOME/logs, because "Copy details" has to have the real error in it
//! even when the window has moved on.

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use crate::engine::layout::{ensure_dir, Layout};
use crate::engine::types::{Outcome, PreflightReport, StepEvent, StepStatus};

/// Anything the engine can report to. Deliberately free of Tauri types so the
/// engine can be driven from a test or from the command line.
pub trait Emitter: Send + Sync {
    fn step(&self, event: StepEvent);
    fn log(&self, line: &str);
    fn preflight(&self, report: PreflightReport);
    fn finished(&self, outcome: Outcome);
}

// ---------------------------------------------------------------------------
// The log file
// ---------------------------------------------------------------------------

/// One run's log: on disk, and in memory so `get_log` can hand the whole
/// thing back without re-reading the file.
pub struct LogSink {
    path: Option<PathBuf>,
    file: Mutex<Option<File>>,
    text: Mutex<String>,
}

impl LogSink {
    /// Open a fresh log for this run. A log we cannot open is not worth
    /// stopping an install for, so failure here just means memory only.
    pub fn open(layout: &Layout) -> Arc<LogSink> {
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        let path = layout.logs_dir().join(format!("installer-{stamp}.log"));
        let file = ensure_dir(&layout.logs_dir()).ok().and_then(|()| {
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(&path)
                .ok()
        });
        Arc::new(LogSink {
            path: file.as_ref().map(|_| path),
            file: Mutex::new(file),
            text: Mutex::new(String::new()),
        })
    }

    pub fn path(&self) -> Option<&Path> {
        self.path.as_deref()
    }

    pub fn append(&self, line: &str) {
        if let Ok(mut text) = self.text.lock() {
            text.push_str(line);
            text.push('\n');
        }
        if let Ok(mut file) = self.file.lock() {
            if let Some(file) = file.as_mut() {
                let _ = writeln!(file, "{line}");
                let _ = file.flush();
            }
        }
    }

    pub fn contents(&self) -> String {
        self.text.lock().map(|t| t.clone()).unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// The window
// ---------------------------------------------------------------------------

pub const EVENT_STEP: &str = "auc://step";
pub const EVENT_LOG: &str = "auc://log";
pub const EVENT_PREFLIGHT: &str = "auc://preflight";
pub const EVENT_FINISHED: &str = "auc://finished";

pub struct TauriEmitter {
    app: tauri::AppHandle,
    log: Arc<LogSink>,
}

impl TauriEmitter {
    pub fn new(app: tauri::AppHandle, log: Arc<LogSink>) -> Self {
        TauriEmitter { app, log }
    }
}

impl Emitter for TauriEmitter {
    fn step(&self, event: StepEvent) {
        // Steps go to the log too: a saved log should read like the screen did.
        if event.status != StepStatus::Pending {
            let status = serde_json::to_value(event.status)
                .ok()
                .and_then(|v| v.as_str().map(str::to_string))
                .unwrap_or_default();
            match &event.detail {
                Some(detail) => self
                    .log
                    .append(&format!("== {} [{status}] {detail}", event.label)),
                None => self.log.append(&format!("== {} [{status}]", event.label)),
            }
        }
        emit(&self.app, EVENT_STEP, &event);
    }

    fn log(&self, line: &str) {
        self.log.append(line);
        emit(&self.app, EVENT_LOG, &serde_json::json!({ "line": line }));
    }

    fn preflight(&self, report: PreflightReport) {
        emit(&self.app, EVENT_PREFLIGHT, &report);
    }

    fn finished(&self, outcome: Outcome) {
        self.log
            .append(&format!("== finished: {}", outcome.summary));
        emit(&self.app, EVENT_FINISHED, &outcome);
    }
}

/// An event the window never receives is not worth crashing the install over,
/// so a failed emit is recorded and swallowed.
fn emit<T: serde::Serialize>(app: &tauri::AppHandle, event: &str, payload: &T) {
    use tauri::Emitter as _;
    if let Err(err) = app.emit(event, payload) {
        eprintln!("could not send {event} to the window: {err}");
    }
}

// ---------------------------------------------------------------------------
// Headless
// ---------------------------------------------------------------------------

/// Prints one line per step — `[3/12] Setting up Python ... done` — with the
/// commands' own output indented underneath whichever step is running.
pub struct ConsoleEmitter {
    log: Arc<LogSink>,
    state: Mutex<ConsoleState>,
}

#[derive(Default)]
struct ConsoleState {
    /// The steps as the engine announced them, in order, so we can number
    /// them the way the contract shows.
    order: Vec<String>,
    current: Option<(usize, String)>,
    /// True while a `[3/12] Label ... ` line is open and waiting for its verdict.
    line_open: bool,
    last_detail: Option<String>,
}

impl ConsoleEmitter {
    pub fn new(log: Arc<LogSink>) -> Self {
        ConsoleEmitter {
            log,
            state: Mutex::new(ConsoleState::default()),
        }
    }

    fn print_indented(&self, state: &mut ConsoleState, line: &str) {
        if state.line_open {
            println!();
            state.line_open = false;
        }
        println!("      {line}");
    }
}

impl Emitter for ConsoleEmitter {
    fn step(&self, event: StepEvent) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        let key = format!("{:?}", event.id);

        match event.status {
            StepStatus::Pending => {
                // The engine lists the whole run up front; that is where the
                // "of 12" comes from.
                if !state.order.contains(&key) {
                    state.order.push(key);
                }
            }
            StepStatus::Running => {
                let index = state.order.iter().position(|k| *k == key).unwrap_or(0) + 1;
                let total = state.order.len().max(index);
                state.current = Some((index, event.label.clone()));
                state.last_detail = None;
                print!("[{index}/{total}] {} ... ", event.label);
                let _ = std::io::stdout().flush();
                state.line_open = true;
                self.log
                    .append(&format!("== [{index}/{total}] {}", event.label));
                if let Some(detail) = event.detail {
                    self.print_indented(&mut state, &detail);
                }
            }
            _ => {
                let verdict = match event.status {
                    StepStatus::Done => "done",
                    StepStatus::Warning => "warning",
                    StepStatus::Failed => "failed",
                    StepStatus::Skipped => "skipped",
                    _ => "",
                };
                let (index, label) = state
                    .current
                    .clone()
                    .unwrap_or((state.order.len(), event.label.clone()));
                let total = state.order.len().max(index);
                if state.line_open {
                    println!("{verdict}");
                    state.line_open = false;
                } else {
                    println!("[{index}/{total}] {label} ... {verdict}");
                }
                if let Some(detail) = event.detail.as_deref() {
                    self.print_indented(&mut state, detail);
                }
                self.log.append(&format!(
                    "== [{index}/{total}] {label} [{verdict}]{}",
                    event
                        .detail
                        .as_deref()
                        .map(|d| format!(" {d}"))
                        .unwrap_or_default()
                ));
            }
        }
    }

    fn log(&self, line: &str) {
        self.log.append(line);
        if let Ok(mut state) = self.state.lock() {
            self.print_indented(&mut state, line);
        }
    }

    fn preflight(&self, report: PreflightReport) {
        if let Ok(mut state) = self.state.lock() {
            for line in &report.lines {
                let mark = match line.level {
                    crate::engine::types::PreflightLevel::Pass => "✓",
                    crate::engine::types::PreflightLevel::Warn => "⚠",
                    crate::engine::types::PreflightLevel::Fail => "✗",
                    crate::engine::types::PreflightLevel::Info => "·",
                };
                self.print_indented(&mut state, &format!("{mark} {}", line.text));
                if let Some(hint) = &line.hint {
                    self.print_indented(&mut state, &format!("  → {hint}"));
                }
            }
        }
    }

    fn finished(&self, outcome: Outcome) {
        if let Ok(mut state) = self.state.lock() {
            if state.line_open {
                println!();
                state.line_open = false;
            }
        }
        println!();
        println!("  {}", outcome.summary);
        for warning in &outcome.warnings {
            println!("  ⚠ {warning}");
        }
        if let Some(error) = &outcome.error {
            println!("  ✗ {}", error.message);
            if let Some(hint) = &error.hint {
                println!("    → {hint}");
            }
        }
        if let Some(url) = &outcome.app_url {
            println!("  AUC is at {url}");
        }
        if let Some(path) = self.log.path() {
            println!("  Full log: {}", path.display());
        }
        println!();
        self.log
            .append(&format!("== finished: {}", outcome.summary));
    }
}

/// Collects everything instead of showing it. Used by tests.
#[cfg(test)]
pub struct RecordingEmitter {
    pub steps: Mutex<Vec<StepEvent>>,
    pub lines: Mutex<Vec<String>>,
    pub outcome: Mutex<Option<Outcome>>,
}

#[cfg(test)]
impl RecordingEmitter {
    pub fn new() -> Arc<RecordingEmitter> {
        Arc::new(RecordingEmitter {
            steps: Mutex::new(Vec::new()),
            lines: Mutex::new(Vec::new()),
            outcome: Mutex::new(None),
        })
    }
}

#[cfg(test)]
impl Emitter for RecordingEmitter {
    fn step(&self, event: StepEvent) {
        self.steps.lock().expect("lock").push(event);
    }
    fn log(&self, line: &str) {
        self.lines.lock().expect("lock").push(line.to_string());
    }
    fn preflight(&self, _report: PreflightReport) {}
    fn finished(&self, outcome: Outcome) {
        *self.outcome.lock().expect("lock") = Some(outcome);
    }
}
