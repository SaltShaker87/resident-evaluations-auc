//! The `download` step: get a copy of AUC and unpack it into
//! `$AUC_HOME/app/<version>/`.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Context, Result};

use crate::engine::layout::ensure_dir;
use crate::engine::release::{self, Source};
use crate::engine::types::StepId;
use crate::engine::{net, Ctx};

/// What ended up on disk.
pub struct Downloaded {
    pub version: String,
    pub dir: PathBuf,
    pub notes: Option<String>,
}

pub fn run(ctx: &mut Ctx, source: &Source) -> Result<Downloaded> {
    ctx.progress.start(StepId::Download);

    let app_dir = ctx.layout.app_dir();
    ensure_dir(&app_dir)?;
    let staging = app_dir.join(".staging");
    remove_dir_if_present(&staging)?;

    let fetched = fetch(ctx, source)?;

    ctx.progress.detail(StepId::Download, "Unpacking");
    release::extract_tar_gz(&fetched.archive, &staging)?;
    if let Some(temporary) = &fetched.temporary {
        let _ = std::fs::remove_file(temporary);
    }

    // A release says what it is in its own VERSION file, which is the only
    // answer available when installing from a file rather than from GitHub.
    let version = release::version_in_dir(&staging)
        .or(fetched.version_hint)
        .unwrap_or_else(|| "unknown".to_string());
    let target = ctx.layout.version_dir(&version);

    if target.exists() {
        // Repairing or reinstalling the same version: write the files over the
        // top rather than deleting first, because backend/venv lives in there
        // and rebuilding it takes minutes.
        ctx.log(format!(
            "AUC {version} is already unpacked at {}; its files are being replaced.",
            target.display()
        ));
        copy_over(&staging, &target)?;
        remove_dir_if_present(&staging)?;
    } else {
        std::fs::rename(&staging, &target).with_context(|| {
            format!(
                "Could not move the unpacked copy of AUC into {}.",
                target.display()
            )
        })?;
    }

    ctx.progress
        .done_with(StepId::Download, format!("AUC {version}"));
    Ok(Downloaded {
        version,
        dir: target,
        notes: fetched.notes,
    })
}

/// What `fetch` came back with, before anything has been unpacked.
struct Fetched {
    /// The archive to unpack.
    archive: PathBuf,
    /// Set when we downloaded it ourselves and should tidy it up afterwards.
    temporary: Option<PathBuf>,
    /// The version, when the source already told us; otherwise the release's
    /// own VERSION file has the last word.
    version_hint: Option<String>,
    notes: Option<String>,
}

fn fetch(ctx: &mut Ctx, source: &Source) -> Result<Fetched> {
    match source {
        Source::LocalFile(path) => {
            if !path.is_file() {
                anyhow::bail!(
                    "There is no file at {}. Check the path given to --source.",
                    path.display()
                );
            }
            // Nothing published to compare it against, so say so rather than
            // implying it was checked.
            ctx.log(format!(
                "Installing from the file {} — there is no published fingerprint for a local \
                 file, so that check was skipped.",
                path.display()
            ));
            ctx.progress
                .detail(StepId::Download, "Reading the file you chose");
            Ok(Fetched {
                archive: path.clone(),
                temporary: None,
                version_hint: path
                    .file_name()
                    .and_then(|n| n.to_str())
                    .and_then(release::version_from_filename),
                notes: None,
            })
        }
        Source::Url(url) => {
            ctx.log(format!(
                "Installing from {url} — a direct link has no published fingerprint, so that \
                 check was skipped."
            ));
            let destination = ctx.layout.app_dir().join(".download.tar.gz");
            download_to(ctx, url, &destination)?;
            let hint = url
                .rsplit('/')
                .next()
                .and_then(release::version_from_filename);
            Ok(Fetched {
                archive: destination.clone(),
                temporary: Some(destination),
                version_hint: hint,
                notes: None,
            })
        }
        Source::Latest => {
            ctx.progress
                .detail(StepId::Download, "Asking GitHub for the newest version");
            let found = release::latest_release()?;
            ctx.log(format!(
                "The newest release is AUC {} ({}).",
                found.version, found.tarball_url
            ));

            let destination = ctx
                .layout
                .app_dir()
                .join(format!(".download-{}.tar.gz", found.version));
            download_to(ctx, &found.tarball_url, &destination)?;

            match &found.digest_url {
                Some(url) => {
                    ctx.progress
                        .detail(StepId::Download, "Checking the download is intact");
                    let text = net::get_text(url, Duration::from_secs(20))?;
                    let digest = release::digest_from_sha256_file(&text).with_context(|| {
                        format!("The published fingerprint at {url} was not readable.")
                    })?;
                    release::verify_digest(&destination, &digest)?;
                    ctx.log("The download matches its published fingerprint.");
                }
                None => ctx.log(
                    "This release publishes no fingerprint file, so the download could not be \
                     checked against one.",
                ),
            }

            Ok(Fetched {
                archive: destination.clone(),
                temporary: Some(destination),
                version_hint: Some(found.version),
                notes: found.notes,
            })
        }
    }
}

fn download_to(ctx: &mut Ctx, url: &str, destination: &Path) -> Result<()> {
    let progress = &ctx.progress;
    let mut last_reported = 0u64;
    release::download(url, destination, &ctx.cancel, &mut |done, total| {
        // Redrawing on every 256 KB chunk would be noise; a tenth of a
        // megabyte at a time is smooth enough for a person.
        if done - last_reported < 400_000 && Some(done) != total {
            return;
        }
        last_reported = done;
        let detail = match total {
            Some(total) if total > 0 => format!("{} of {}", format_size(done), format_size(total)),
            _ => format_size(done).to_string(),
        };
        let fraction = total
            .filter(|t| *t > 0)
            .map(|total| (done as f64 / total as f64).clamp(0.0, 1.0));
        progress.tick(StepId::Download, detail, fraction);
    })?;
    Ok(())
}

/// Sizes the way a download dialog writes them.
pub fn format_size(bytes: u64) -> String {
    const MB: f64 = 1_000_000.0;
    const GB: f64 = 1_000_000_000.0;
    let bytes = bytes as f64;
    if bytes >= GB {
        format!("{:.1} GB", bytes / GB)
    } else if bytes >= MB {
        format!("{:.0} MB", bytes / MB)
    } else {
        format!("{:.0} KB", (bytes / 1000.0).max(1.0))
    }
}

fn remove_dir_if_present(dir: &Path) -> Result<()> {
    if dir.exists() {
        std::fs::remove_dir_all(dir)
            .with_context(|| format!("Could not clear out {}.", dir.display()))?;
    }
    Ok(())
}

/// Copy everything from `from` over the top of `to`, leaving anything already
/// in `to` that the new copy does not mention — which is how backend/venv
/// survives a repair.
pub fn copy_over(from: &Path, to: &Path) -> Result<()> {
    ensure_dir(to)?;
    for entry in
        std::fs::read_dir(from).with_context(|| format!("Could not read {}.", from.display()))?
    {
        let entry = entry.with_context(|| format!("Could not read {}.", from.display()))?;
        let source = entry.path();
        let target = to.join(entry.file_name());
        let kind = entry
            .file_type()
            .with_context(|| format!("Could not tell what {} is.", source.display()))?;
        if kind.is_dir() {
            copy_over(&source, &target)?;
        } else {
            if target.exists() {
                let _ = std::fs::remove_file(&target);
            }
            std::fs::copy(&source, &target).with_context(|| {
                format!(
                    "Could not copy {} to {}.",
                    source.display(),
                    target.display()
                )
            })?;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sizes_are_written_the_way_a_download_dialog_writes_them() {
        assert_eq!(format_size(180_300_000), "180 MB");
        assert_eq!(format_size(2_100_000_000), "2.1 GB");
        assert_eq!(format_size(12_000), "12 KB");
    }

    #[test]
    fn copying_over_a_version_leaves_the_python_environment_alone() {
        let dir = tempfile::tempdir().expect("temp dir");
        let new = dir.path().join("staging");
        let existing = dir.path().join("1.4.0");
        std::fs::create_dir_all(new.join("backend")).expect("new");
        std::fs::write(new.join("backend/app.py"), "# new\n").expect("write");
        std::fs::create_dir_all(existing.join("backend/venv/bin")).expect("existing");
        std::fs::write(existing.join("backend/venv/bin/python"), "old venv\n").expect("write");
        std::fs::write(existing.join("backend/app.py"), "# old\n").expect("write");

        copy_over(&new, &existing).expect("copy over");

        assert_eq!(
            std::fs::read_to_string(existing.join("backend/app.py")).expect("read"),
            "# new\n"
        );
        assert!(
            existing.join("backend/venv/bin/python").is_file(),
            "the venv must survive a repair"
        );
    }
}
