//! Stopping half-way through.
//!
//! One flag, shared by everything in a run. Steps check it between commands
//! and `process::run` checks it while a command is still talking, so pressing
//! Cancel during a twenty-minute download does not wait for the download.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// The error every step returns once the flag is set, so a cancelled run is
/// reported as cancelled rather than as a failure.
#[derive(Debug, thiserror::Error)]
#[error("Stopped at your request.")]
pub struct Cancelled;

#[derive(Debug, Clone, Default)]
pub struct Cancel {
    flag: Arc<AtomicBool>,
}

impl Cancel {
    pub fn new() -> Self {
        Cancel::default()
    }

    pub fn cancel(&self) {
        self.flag.store(true, Ordering::SeqCst);
    }

    pub fn is_cancelled(&self) -> bool {
        self.flag.load(Ordering::SeqCst)
    }

    /// Bail out of the current step if Cancel has been pressed.
    pub fn check(&self) -> anyhow::Result<()> {
        if self.is_cancelled() {
            Err(Cancelled.into())
        } else {
            Ok(())
        }
    }
}

/// True when this error came from Cancel rather than from something going
/// wrong, anywhere in the chain of `.context()` sentences.
pub fn is_cancelled_error(err: &anyhow::Error) -> bool {
    err.chain().any(|cause| cause.is::<Cancelled>())
}
