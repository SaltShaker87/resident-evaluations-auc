//! Taking over an install that was made by hand with `setup.sh`.
//!
//! Somebody who already runs AUC from a git clone has real data in it. Adopting
//! means: stop the old service, back its data up first, copy that data into the
//! new home if the new home is empty, carry its settings across, and replace
//! its units. Nothing is deleted — the old folder is left exactly where it is,
//! so if any of this turns out to be wrong, the old install still works.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::Result;

use crate::engine::process::Cmd;
use crate::engine::steps::autostart::{systemctl_quiet, BACKUP_TIMER_UNIT, SERVICE_UNIT};
use crate::engine::types::ExistingInstall;
use crate::engine::Ctx;

/// What we took over, and from where.
pub struct Adopted {
    /// The old install's folder, recorded in the state file as `adopted_from`.
    pub from: String,
    /// Its `Environment=` settings, to be carried into `auc.env`.
    pub env: BTreeMap<String, String>,
}

/// Where a hand-made install kept its data: what it was told, or the default
/// next to the code.
pub fn data_dir_of(working_directory: &Path, env: &BTreeMap<String, String>) -> PathBuf {
    match env.get("AUC_DATA_DIR").filter(|d| !d.trim().is_empty()) {
        Some(dir) => PathBuf::from(dir),
        // WorkingDirectory is <clone>/auc/backend, and the default data
        // folder is <clone>/auc/data.
        None => working_directory
            .parent()
            .map(|app| app.join("data"))
            .unwrap_or_else(|| working_directory.join("../data")),
    }
}

/// True when there is nothing of ours to overwrite, so copying is safe.
pub fn is_empty_data_dir(dir: &Path) -> bool {
    if !dir.exists() {
        return true;
    }
    if dir.join("auc.db").exists() {
        return false;
    }
    std::fs::read_dir(dir)
        .map(|mut entries| entries.next().is_none())
        .unwrap_or(false)
}

pub fn adopt(ctx: &mut Ctx, existing: &ExistingInstall) -> Result<Option<Adopted>> {
    let ExistingInstall::Manual {
        working_directory,
        env,
        ..
    } = existing
    else {
        // Nothing to adopt: we installed the copy that is already here.
        return Ok(None);
    };

    let backend = PathBuf::from(working_directory);
    let app = backend.parent().unwrap_or(&backend).to_path_buf();
    ctx.log(format!(
        "Taking over the AUC install at {}. Nothing there will be deleted.",
        app.display()
    ));

    // Stop it first, so two copies are never serving the same database.
    systemctl_quiet(ctx, &["disable", "--now", SERVICE_UNIT]);
    systemctl_quiet(ctx, &["disable", "--now", BACKUP_TIMER_UNIT]);

    back_up_old_install(ctx, &backend, env);

    let source = data_dir_of(&backend, env);
    let destination = ctx.layout.data_dir();
    if !source.is_dir() {
        ctx.warn(format!(
            "The existing install's data folder ({}) is not there, so nothing was copied across. \
             Check it before using the new install.",
            source.display()
        ));
    } else if is_empty_data_dir(&destination) {
        ctx.log(format!(
            "Copying your data from {} to {}.",
            source.display(),
            destination.display()
        ));
        super::download::copy_over(&source, &destination)?;
    } else {
        ctx.warn(format!(
            "There is already a database at {}, so the older install's data was left where it is \
             ({}). Nothing was overwritten.",
            destination.display(),
            source.display()
        ));
    }

    Ok(Some(Adopted {
        from: app.display().to_string(),
        env: env.clone(),
    }))
}

/// Run the old install's own `backup.py` with its own settings, before
/// anything else is touched. A failure is a warning: we are not deleting
/// anything, so it is not worth stopping for, but the user should know.
fn back_up_old_install(ctx: &mut Ctx, backend: &Path, env: &BTreeMap<String, String>) {
    let python = backend.join("venv/bin/python");
    let script = backend.join("backup.py");
    if !python.is_file() || !script.is_file() {
        ctx.log(
            "The existing install has no Python environment to run its own backup with, \
             so that step was skipped.",
        );
        return;
    }

    let mut settings = env.clone();
    settings
        .entry("AUC_BACKUP_DIR".to_string())
        .or_insert_with(|| ctx.layout.backups_dir().display().to_string());

    let result = Cmd::new(python.display().to_string())
        .arg("backup.py")
        .cwd(backend)
        .envs(settings)
        .timeout(Duration::from_secs(30 * 60))
        .run(ctx.emitter.as_ref(), &ctx.cancel);
    match result {
        Ok(output) if output.success => {
            ctx.log("Backed up the existing install before touching anything.");
        }
        Ok(output) => {
            let tail = output.tail(10);
            ctx.warn(format!(
                "The existing install's backup did not finish, so its data was copied without one. \
                 Its own folder is untouched, so nothing is lost. It said:\n{tail}"
            ));
        }
        Err(err) => ctx.warn(format!(
            "The existing install's backup could not be run ({err}), so its data was copied \
             without one. Its own folder is untouched, so nothing is lost."
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_data_folder_is_the_one_the_unit_named() {
        let mut env = BTreeMap::new();
        env.insert("AUC_DATA_DIR".to_string(), "/mnt/big/auc-data".to_string());
        assert_eq!(
            data_dir_of(Path::new("/home/you/clone/auc/backend"), &env),
            PathBuf::from("/mnt/big/auc-data")
        );
    }

    #[test]
    fn without_one_it_is_the_folder_next_to_the_code() {
        let env = BTreeMap::new();
        assert_eq!(
            data_dir_of(Path::new("/home/you/clone/auc/backend"), &env),
            PathBuf::from("/home/you/clone/auc/data")
        );
    }

    #[test]
    fn an_empty_setting_is_treated_as_no_setting() {
        let mut env = BTreeMap::new();
        env.insert("AUC_DATA_DIR".to_string(), "  ".to_string());
        assert_eq!(
            data_dir_of(Path::new("/home/you/clone/auc/backend"), &env),
            PathBuf::from("/home/you/clone/auc/data")
        );
    }

    #[test]
    fn a_folder_with_a_database_in_it_is_never_written_over() {
        let dir = tempfile::tempdir().expect("temp dir");
        let data = dir.path().join("data");
        assert!(is_empty_data_dir(&data), "a folder that is not there yet");
        std::fs::create_dir_all(&data).expect("create");
        assert!(is_empty_data_dir(&data), "an empty folder");
        std::fs::write(data.join("auc.db"), b"not really a database").expect("write");
        assert!(!is_empty_data_dir(&data), "a folder with a database in it");
    }
}
