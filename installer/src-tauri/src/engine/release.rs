//! Getting a copy of AUC onto this machine.
//!
//! Normally the newest GitHub Release, digest checked before anything is
//! unpacked. `AUC_INSTALLER_SOURCE` (or `--source`) points at a file or a URL
//! instead, which is how development and offline installs work.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};
use sha2::{Digest, Sha256};

use crate::engine::cancel::Cancel;
use crate::engine::net;

pub const REPO: &str = "SaltShaker87/resident-evaluations-auc";

fn latest_release_url() -> String {
    format!("https://api.github.com/repos/{REPO}/releases/latest")
}

/// Where this install is coming from.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Source {
    /// The newest GitHub Release of the repository.
    Latest,
    /// A `.tar.gz` already on this machine. The digest check is skipped —
    /// there is nothing to check it against — and the log says so.
    LocalFile(PathBuf),
    /// A direct link to a `.tar.gz`.
    Url(String),
}

/// `--source` wins over `AUC_INSTALLER_SOURCE`, which wins over GitHub.
pub fn source_from(flag: Option<&str>) -> Source {
    let given = flag
        .map(str::to_string)
        .or_else(|| std::env::var("AUC_INSTALLER_SOURCE").ok())
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty());
    match given {
        None => Source::Latest,
        Some(value) if value.starts_with("http://") || value.starts_with("https://") => {
            Source::Url(value)
        }
        Some(value) => Source::LocalFile(PathBuf::from(value)),
    }
}

/// What a GitHub Release offers us.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Release {
    pub version: String,
    pub tarball_url: String,
    pub digest_url: Option<String>,
    pub notes: Option<String>,
}

pub fn latest_release() -> Result<Release> {
    let body = net::get_text(&latest_release_url(), Duration::from_secs(20))
        .with_context(|| format!("Could not ask GitHub for the newest version of AUC ({REPO})."))?;
    parse_release_json(&body)
}

/// Read the bits of GitHub's answer we need: the version, the tarball and its
/// digest.
pub fn parse_release_json(text: &str) -> Result<Release> {
    let value: serde_json::Value = serde_json::from_str(text)
        .context("GitHub's answer about the newest version was not in a shape we understand.")?;
    let tag = value
        .get("tag_name")
        .and_then(|t| t.as_str())
        .ok_or_else(|| anyhow!("GitHub did not say which version the newest release is."))?;
    let version = version_from_tag(tag);

    let assets = value
        .get("assets")
        .and_then(|a| a.as_array())
        .map(|a| a.as_slice())
        .unwrap_or(&[]);
    let find = |suffix: &str| -> Option<String> {
        assets
            .iter()
            .filter(|asset| {
                asset
                    .get("name")
                    .and_then(|n| n.as_str())
                    .is_some_and(|name| name.ends_with(suffix))
            })
            .find_map(|asset| {
                asset
                    .get("browser_download_url")
                    .and_then(|u| u.as_str())
                    .map(str::to_string)
            })
    };
    // .sha256 first, or the tarball lookup would match it too.
    let digest_url = find(".tar.gz.sha256");
    let tarball_url = assets
        .iter()
        .filter(|asset| {
            asset
                .get("name")
                .and_then(|n| n.as_str())
                .is_some_and(|name| name.ends_with(".tar.gz"))
        })
        .find_map(|asset| {
            asset
                .get("browser_download_url")
                .and_then(|u| u.as_str())
                .map(str::to_string)
        })
        .ok_or_else(|| {
            anyhow!(
                "Release {version} has no auc-{version}.tar.gz to download. \
                 It may still be being published; try again in a few minutes."
            )
        })?;

    Ok(Release {
        version,
        tarball_url,
        digest_url,
        notes: value
            .get("body")
            .and_then(|b| b.as_str())
            .map(str::trim)
            .filter(|b| !b.is_empty())
            .map(str::to_string),
    })
}

/// Tags are written `v1.4.0`; versions are written `1.4.0`.
pub fn version_from_tag(tag: &str) -> String {
    tag.trim().trim_start_matches('v').to_string()
}

/// `auc-1.4.0.tar.gz` -> `1.4.0`.
pub fn version_from_filename(name: &str) -> Option<String> {
    let stem = name.strip_suffix(".tar.gz")?;
    let version = stem.strip_prefix("auc-")?;
    if version.is_empty() {
        None
    } else {
        Some(version.to_string())
    }
}

// ---------------------------------------------------------------------------
// Downloading
// ---------------------------------------------------------------------------

/// Download to `dest`, calling `progress` with (bytes so far, total if known).
///
/// Written in chunks so the screen can show a bar, and so Cancel during a
/// 300 MB download takes effect straight away.
pub fn download(
    url: &str,
    dest: &Path,
    cancel: &Cancel,
    progress: &mut dyn FnMut(u64, Option<u64>),
) -> Result<()> {
    let client = net::client(Duration::from_secs(60))?;
    let mut response = client
        .get(url)
        .send()
        .with_context(|| format!("Could not start the download from {url}."))?;
    if !response.status().is_success() {
        return Err(anyhow!(
            "The download from {url} was refused ({}). Check the internet connection, \
             or pass --source to install from a file you already have.",
            response.status()
        ));
    }
    let total = response.content_length();

    if let Some(parent) = dest.parent() {
        crate::engine::layout::ensure_dir(parent)?;
    }
    let mut file = std::fs::File::create(dest)
        .with_context(|| format!("Could not create {}.", dest.display()))?;

    let mut buffer = vec![0u8; 256 * 1024];
    let mut done: u64 = 0;
    loop {
        cancel.check()?;
        let read = response
            .read(&mut buffer)
            .with_context(|| format!("The download from {url} stopped part way through."))?;
        if read == 0 {
            break;
        }
        file.write_all(&buffer[..read])
            .with_context(|| format!("Could not write to {}. Is the disk full?", dest.display()))?;
        done += read as u64;
        progress(done, total);
    }
    file.flush().ok();
    Ok(())
}

// ---------------------------------------------------------------------------
// Checking what arrived
// ---------------------------------------------------------------------------

pub fn sha256_file(path: &Path) -> Result<String> {
    let mut file = std::fs::File::open(path)
        .with_context(|| format!("Could not open {} to check it.", path.display()))?;
    let mut hasher = Sha256::new();
    let mut buffer = vec![0u8; 256 * 1024];
    loop {
        let read = file
            .read(&mut buffer)
            .with_context(|| format!("Could not read {} to check it.", path.display()))?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    Ok(hex::encode(hasher.finalize()))
}

/// A `.sha256` file is `<digest>  <filename>`; only the digest matters.
pub fn digest_from_sha256_file(text: &str) -> Option<String> {
    let word = text.split_whitespace().next()?;
    let digest = word.trim().to_ascii_lowercase();
    if digest.len() == 64 && digest.chars().all(|c| c.is_ascii_hexdigit()) {
        Some(digest)
    } else {
        None
    }
}

pub fn verify_digest(archive: &Path, expected: &str) -> Result<()> {
    let actual = sha256_file(archive)?;
    if actual != expected.to_ascii_lowercase() {
        return Err(anyhow!(
            "The downloaded copy of AUC does not match its published fingerprint, so it was not \
             unpacked. This usually means the download was interrupted — try again. \
             (expected {expected}, got {actual})"
        ));
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Unpacking
// ---------------------------------------------------------------------------

/// Unpack the release into `dest`, dropping the single `auc/` folder the
/// tarball wraps everything in, so `dest/backend` and friends land directly.
pub fn extract_tar_gz(archive: &Path, dest: &Path) -> Result<()> {
    let file = std::fs::File::open(archive)
        .with_context(|| format!("Could not open {}.", archive.display()))?;
    let decoder = flate2::read::GzDecoder::new(file);
    let mut tar = tar::Archive::new(decoder);
    crate::engine::layout::ensure_dir(dest)?;

    let mut wrote_anything = false;
    for entry in tar
        .entries()
        .with_context(|| format!("{} is not a readable .tar.gz archive.", archive.display()))?
    {
        let mut entry =
            entry.with_context(|| format!("{} stopped part way through.", archive.display()))?;
        let path = entry
            .path()
            .with_context(|| {
                format!(
                    "{} contains a file with an unreadable name.",
                    archive.display()
                )
            })?
            .to_path_buf();

        let Some(relative) = strip_top_level(&path) else {
            continue;
        };
        let target = dest.join(&relative);
        // A tarball that tries to write outside the folder we gave it is
        // either broken or hostile; either way it does not get to.
        if !target.starts_with(dest) {
            continue;
        }
        if let Some(parent) = target.parent() {
            crate::engine::layout::ensure_dir(parent)?;
        }
        entry
            .unpack(&target)
            .with_context(|| format!("Could not write {}.", target.display()))?;
        wrote_anything = true;
    }
    if !wrote_anything {
        return Err(anyhow!(
            "{} unpacked to nothing at all, so this is not a copy of AUC.",
            archive.display()
        ));
    }
    Ok(())
}

/// Drop the first path component — the `auc/` the release is wrapped in —
/// and refuse anything that tries to climb out of the folder.
fn strip_top_level(path: &Path) -> Option<PathBuf> {
    let mut parts = path.components();
    parts.next()?;
    let rest: PathBuf = parts.collect();
    if rest.as_os_str().is_empty() {
        return None;
    }
    if rest.components().any(|c| {
        matches!(
            c,
            std::path::Component::ParentDir | std::path::Component::RootDir
        )
    }) {
        return None;
    }
    Some(rest)
}

/// The version a release says it is, from its own VERSION file.
pub fn version_in_dir(dir: &Path) -> Option<String> {
    let text = std::fs::read_to_string(dir.join("VERSION")).ok()?;
    let version = text.trim().trim_start_matches('v').to_string();
    if version.is_empty() {
        None
    } else {
        Some(version)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RELEASE_JSON: &str = r#"{
      "tag_name": "v1.4.0",
      "name": "AUC 1.4.0",
      "body": "Adds the installer.\n",
      "assets": [
        {"name": "auc-1.4.0.tar.gz.sha256",
         "browser_download_url": "https://example.org/auc-1.4.0.tar.gz.sha256"},
        {"name": "auc-1.4.0.tar.gz",
         "browser_download_url": "https://example.org/auc-1.4.0.tar.gz"}
      ]
    }"#;

    #[test]
    fn a_release_tells_us_the_version_the_tarball_and_the_digest() {
        let release = parse_release_json(RELEASE_JSON).expect("should parse");
        assert_eq!(release.version, "1.4.0");
        assert_eq!(release.tarball_url, "https://example.org/auc-1.4.0.tar.gz");
        assert_eq!(
            release.digest_url.as_deref(),
            Some("https://example.org/auc-1.4.0.tar.gz.sha256")
        );
        assert_eq!(release.notes.as_deref(), Some("Adds the installer."));
    }

    #[test]
    fn a_release_with_no_tarball_says_so_in_words_a_user_can_act_on() {
        let json = r#"{"tag_name": "v1.4.0", "assets": []}"#;
        let err = parse_release_json(json).expect_err("no tarball");
        assert!(err.to_string().contains("auc-1.4.0.tar.gz"), "{err}");
    }

    #[test]
    fn tags_and_filenames_both_give_up_their_version() {
        assert_eq!(version_from_tag("v1.4.0"), "1.4.0");
        assert_eq!(version_from_tag("1.4.0"), "1.4.0");
        assert_eq!(
            version_from_filename("auc-1.4.0.tar.gz").as_deref(),
            Some("1.4.0")
        );
        assert_eq!(version_from_filename("something-else.zip"), None);
    }

    #[test]
    fn the_source_is_taken_from_the_flag_then_the_environment_then_github() {
        assert_eq!(
            source_from(Some("/tmp/auc.tar.gz")),
            Source::LocalFile("/tmp/auc.tar.gz".into())
        );
        assert_eq!(
            source_from(Some("https://example.org/auc.tar.gz")),
            Source::Url("https://example.org/auc.tar.gz".to_string())
        );
        // No flag and no environment variable set in this test process.
        std::env::remove_var("AUC_INSTALLER_SOURCE");
        assert_eq!(source_from(None), Source::Latest);
    }

    #[test]
    fn a_digest_file_is_read_and_a_truncated_one_is_not_trusted() {
        assert_eq!(
            digest_from_sha256_file(
                "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08  auc-1.4.0.tar.gz\n"
            )
            .as_deref(),
            Some("9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08")
        );
        assert_eq!(digest_from_sha256_file("not-a-digest  auc.tar.gz"), None);
        assert_eq!(digest_from_sha256_file(""), None);
    }

    /// Build a tarball shaped like a real release: everything inside one
    /// top-level `auc/` folder.
    fn make_release_tarball(path: &Path) {
        let file = std::fs::File::create(path).expect("create tarball");
        let encoder = flate2::write::GzEncoder::new(file, flate2::Compression::fast());
        let mut builder = tar::Builder::new(encoder);

        let mut add = |name: &str, contents: &str| {
            let mut header = tar::Header::new_gnu();
            header.set_size(contents.len() as u64);
            header.set_mode(0o644);
            header.set_cksum();
            builder
                .append_data(&mut header, name, contents.as_bytes())
                .expect("append");
        };
        add("auc/VERSION", "1.4.0\n");
        add("auc/backend/app.py", "# the app\n");
        add("auc/nim/docker-compose.yml", "services: {}\n");
        add("auc/preflight.sh", "#!/bin/bash\n");
        builder
            .into_inner()
            .expect("finish tar")
            .finish()
            .expect("finish gzip");
    }

    #[test]
    fn the_wrapping_folder_is_stripped_so_backend_lands_where_the_units_expect_it() {
        let dir = tempfile::tempdir().expect("temp dir");
        let archive = dir.path().join("auc-1.4.0.tar.gz");
        make_release_tarball(&archive);

        let dest = dir.path().join("app/1.4.0");
        extract_tar_gz(&archive, &dest).expect("extract");

        assert!(
            dest.join("VERSION").is_file(),
            "VERSION should be at the top"
        );
        assert!(dest.join("backend/app.py").is_file());
        assert!(dest.join("nim/docker-compose.yml").is_file());
        assert!(
            !dest.join("auc").exists(),
            "the auc/ wrapper should be gone"
        );
        assert_eq!(version_in_dir(&dest).as_deref(), Some("1.4.0"));
    }

    #[test]
    fn an_entry_that_tries_to_climb_out_of_the_folder_is_refused() {
        // The `tar` crate will not even let us build such an archive, so the
        // rule is checked where it is enforced.
        assert_eq!(
            strip_top_level(Path::new("auc/backend/app.py")),
            Some(PathBuf::from("backend/app.py"))
        );
        assert_eq!(
            strip_top_level(Path::new("auc")),
            None,
            "the wrapper itself"
        );
        assert_eq!(strip_top_level(Path::new("auc/../escaped.txt")), None);
        assert_eq!(
            strip_top_level(Path::new("auc/backend/../../escaped.txt")),
            None
        );
    }

    #[test]
    fn an_empty_archive_is_not_mistaken_for_a_release() {
        let dir = tempfile::tempdir().expect("temp dir");
        let archive = dir.path().join("empty.tar.gz");
        {
            let file = std::fs::File::create(&archive).expect("create");
            let encoder = flate2::write::GzEncoder::new(file, flate2::Compression::fast());
            let builder = tar::Builder::new(encoder);
            builder.into_inner().expect("tar").finish().expect("gzip");
        }
        let err = extract_tar_gz(&archive, &dir.path().join("out")).expect_err("nothing in it");
        assert!(err.to_string().contains("not a copy of AUC"), "{err}");
    }

    #[test]
    fn a_digest_that_does_not_match_stops_the_install() {
        let dir = tempfile::tempdir().expect("temp dir");
        let file = dir.path().join("thing.tar.gz");
        let mut handle = std::fs::File::create(&file).expect("create");
        handle.write_all(b"hello").expect("write");
        drop(handle);

        // sha256("hello")
        let real = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824";
        assert_eq!(sha256_file(&file).expect("digest"), real);
        verify_digest(&file, real).expect("matching digest");
        let err = verify_digest(&file, &"0".repeat(64)).expect_err("mismatch");
        assert!(err.to_string().contains("was not unpacked"), "{err}");
    }
}
