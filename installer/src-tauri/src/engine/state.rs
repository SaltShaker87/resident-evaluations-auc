//! `installer-state.json` — the small file that remembers what we installed
//! and what the user chose, so Update, Repair and "finish Nemotron later"
//! know where to pick up.

use std::path::Path;

use anyhow::{Context, Result};

use crate::engine::layout::{ensure_dir, Layout};
use crate::engine::types::{Choices, InstallerState, STATE_SCHEMA};

/// The state file, or `None` when this machine has never been installed by us.
///
/// A file we cannot make sense of is treated as absent rather than fatal: the
/// user can still install, and a broken state file must not brick the screen
/// that would let them.
pub fn read(layout: &Layout) -> Option<InstallerState> {
    read_from(&layout.state_file())
}

pub fn read_from(path: &Path) -> Option<InstallerState> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

pub fn write(layout: &Layout, state: &InstallerState) -> Result<()> {
    let path = layout.state_file();
    ensure_dir(&layout.config_dir)?;
    let text = serde_json::to_string_pretty(state)
        .context("Could not describe what was installed in order to save it.")?;
    std::fs::write(&path, text + "\n")
        .with_context(|| format!("Could not save {}.", path.display()))
}

pub fn remove(layout: &Layout) -> Result<()> {
    let path = layout.state_file();
    match std::fs::remove_file(&path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e).with_context(|| format!("Could not remove {}.", path.display())),
    }
}

/// A fresh state record for a version we have just put on disk.
pub fn new_state(layout: &Layout, version: &str, choices: Choices) -> InstallerState {
    InstallerState {
        schema: STATE_SCHEMA,
        version: version.to_string(),
        installed_at: now_iso(),
        auc_home: layout.auc_home.display().to_string(),
        choices,
        nemotron_pending: false,
        adopted_from: None,
    }
}

/// UTC, to the second, the way the contract's example shows it.
pub fn now_iso() -> String {
    chrono::Utc::now().format("%Y-%m-%dT%H:%M:%SZ").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::types::NetworkScope;

    fn choices() -> Choices {
        Choices {
            ai_enabled: true,
            model: Some("qwen3.5:9b".to_string()),
            nemotron_enabled: true,
            network_scope: NetworkScope::Local,
        }
    }

    #[test]
    fn what_we_write_is_what_we_read_back() {
        let dir = tempfile::tempdir().expect("temp dir");
        let layout = Layout::rooted_at(dir.path());
        let mut state = new_state(&layout, "1.4.0", choices());
        state.nemotron_pending = true;
        state.adopted_from = Some("/home/you/resident-evaluations-auc/auc".to_string());

        write(&layout, &state).expect("write state");
        let back = read(&layout).expect("state should be there");
        assert_eq!(back, state);
    }

    #[test]
    fn the_json_keys_are_the_ones_the_contract_names() {
        let dir = tempfile::tempdir().expect("temp dir");
        let layout = Layout::rooted_at(dir.path());
        let state = new_state(&layout, "1.4.0", choices());
        let json = serde_json::to_value(&state).expect("serialise");
        for key in [
            "schema",
            "version",
            "installed_at",
            "auc_home",
            "choices",
            "nemotron_pending",
            "adopted_from",
        ] {
            assert!(json.get(key).is_some(), "missing {key}");
        }
        assert_eq!(json["choices"]["network_scope"], "local");
        assert_eq!(json["schema"], 1);
    }

    #[test]
    fn no_state_file_means_we_have_never_installed_here() {
        let dir = tempfile::tempdir().expect("temp dir");
        let layout = Layout::rooted_at(dir.path());
        assert!(read(&layout).is_none());
        // and removing one that is not there is not an error
        remove(&layout).expect("remove should be quiet about a missing file");
    }

    #[test]
    fn a_damaged_state_file_reads_as_absent_rather_than_blowing_up() {
        let dir = tempfile::tempdir().expect("temp dir");
        let layout = Layout::rooted_at(dir.path());
        ensure_dir(&layout.config_dir).expect("config dir");
        std::fs::write(layout.state_file(), "{ this is not json").expect("write");
        assert!(read(&layout).is_none());
    }
}
