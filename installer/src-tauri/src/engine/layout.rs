//! Where everything goes on disk. CONTRACT.md section 1 is the map; this is
//! the only place that spells those paths out.

use anyhow::{anyhow, Result};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Layout {
    /// $AUC_HOME — ~/.local/share/auc unless AUC_HOME says otherwise.
    pub auc_home: PathBuf,
    /// ~/.config/auc
    pub config_dir: PathBuf,
    /// The user's home, as systemd's %h would expand it.
    pub home: PathBuf,
}

impl Layout {
    /// The real layout for the person running the installer.
    ///
    /// AUC_HOME is honoured mostly so that a test, or someone trying the
    /// installer out, can point the whole tree somewhere disposable.
    pub fn detect() -> Result<Layout> {
        let home = dirs::home_dir().ok_or_else(|| {
            anyhow!("Could not work out your home folder, so there is nowhere to install AUC.")
        })?;
        let auc_home = match std::env::var_os("AUC_HOME") {
            Some(dir) if !dir.is_empty() => PathBuf::from(dir),
            _ => home.join(".local/share/auc"),
        };
        let config_dir = match std::env::var_os("AUC_CONFIG_HOME") {
            Some(dir) if !dir.is_empty() => PathBuf::from(dir),
            _ => home.join(".config/auc"),
        };
        Ok(Layout {
            auc_home,
            config_dir,
            home,
        })
    }

    /// A layout rooted anywhere, for tests.
    #[cfg(test)]
    pub fn rooted_at(root: &Path) -> Layout {
        Layout {
            auc_home: root.join(".local/share/auc"),
            config_dir: root.join(".config/auc"),
            home: root.to_path_buf(),
        }
    }

    pub fn app_dir(&self) -> PathBuf {
        self.auc_home.join("app")
    }

    pub fn version_dir(&self, version: &str) -> PathBuf {
        self.app_dir().join(version)
    }

    /// app/current — the symlink the systemd units point at.
    pub fn current_link(&self) -> PathBuf {
        self.app_dir().join("current")
    }

    pub fn data_dir(&self) -> PathBuf {
        self.auc_home.join("data")
    }

    pub fn backups_dir(&self) -> PathBuf {
        self.auc_home.join("backups")
    }

    pub fn tools_dir(&self) -> PathBuf {
        self.auc_home.join("tools")
    }

    pub fn uv_bin(&self) -> PathBuf {
        self.tools_dir().join("uv")
    }

    /// UV_PYTHON_INSTALL_DIR: the managed Python lives here, not in /usr.
    pub fn python_install_dir(&self) -> PathBuf {
        self.tools_dir().join("python")
    }

    pub fn logs_dir(&self) -> PathBuf {
        self.auc_home.join("logs")
    }

    pub fn env_file(&self) -> PathBuf {
        self.config_dir.join("auc.env")
    }

    pub fn state_file(&self) -> PathBuf {
        self.config_dir.join("installer-state.json")
    }

    pub fn systemd_user_dir(&self) -> PathBuf {
        self.home.join(".config/systemd/user")
    }

    pub fn unit_path(&self, name: &str) -> PathBuf {
        self.systemd_user_dir().join(name)
    }

    pub fn desktop_file(&self) -> PathBuf {
        self.home.join(".local/share/applications/auc.desktop")
    }

    /// The AUC emblem the menu entry and the Desktop shortcut point at. In the
    /// icon-theme folder so a desktop that looks icons up by name finds it too.
    pub fn icon_file(&self) -> PathBuf {
        self.home
            .join(".local/share/icons/hicolor/256x256/apps/auc.png")
    }

    /// `~/.config/user-dirs.dirs`, where the desktop records which folder is
    /// the Desktop (it is not always called that).
    pub fn user_dirs_file(&self) -> PathBuf {
        self.home.join(".config/user-dirs.dirs")
    }

    /// The Nemotron model weights — the same folder start-nemotron.sh uses,
    /// so an install and a hand-started stack share one multi-gigabyte cache.
    pub fn nim_cache_dir(&self) -> PathBuf {
        self.home.join(".cache/nim")
    }

    /// The Python interpreter inside a release's venv. Scripts in the repo
    /// expect it at backend/venv, so that is where it goes.
    pub fn venv_python(&self, app: &Path) -> PathBuf {
        app.join("backend/venv/bin/python")
    }
}

/// Create a directory and say, in one sentence, what to do if we cannot.
pub fn ensure_dir(path: &Path) -> Result<()> {
    std::fs::create_dir_all(path).map_err(|e| {
        anyhow!(
            "Could not create the folder {}: {e}. Check that you have permission to write there.",
            path.display()
        )
    })
}
