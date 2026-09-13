//! The `python` step: a Python of our own, and the packages AUC needs.
//!
//! The user's system Python is not touched. `uv` installs a managed Python
//! 3.12 under $AUC_HOME/tools and builds the virtual environment at
//! `backend/venv`, which is where every script in the repository looks for it.
//!
//! The two-stage install is `setup.sh`'s, for its reasons: the core packages
//! are pure Python and the app cannot run without them, so a failure there is
//! fatal; the ACGME index layer (chromadb) is the only thing with compiled
//! parts, so it is the only thing that can plausibly fail on an unfamiliar
//! processor, and it costs summary generation rather than the whole app.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{anyhow, Context, Result};

use crate::engine::layout::ensure_dir;
use crate::engine::process::{make_executable, Cmd};
use crate::engine::release;
use crate::engine::types::StepId;
use crate::engine::Ctx;

/// The Python AUC is developed against. 3.10 is the floor, 3.11+ is the
/// comfortable floor, and pinning one version means every machine behaves the
/// same way.
pub const PYTHON_VERSION: &str = "3.12";

/// Where uv is published. The "latest" link means we do not have to chase
/// their version numbers.
pub fn uv_download_url(arch: &str) -> Result<String> {
    let triple = match arch {
        "x86_64" => "x86_64-unknown-linux-gnu",
        "aarch64" => "aarch64-unknown-linux-gnu",
        other => {
            return Err(anyhow!(
                "AUC does not have a Python installer for {other} processors. \
                 It supports 64-bit Intel/AMD and 64-bit ARM machines."
            ))
        }
    };
    Ok(format!(
        "https://github.com/astral-sh/uv/releases/latest/download/uv-{triple}.tar.gz"
    ))
}

pub fn run(ctx: &mut Ctx, app: &Path) -> Result<()> {
    ctx.progress.start(StepId::Python);

    let backend = app.join("backend");
    if !backend.is_dir() {
        anyhow::bail!(
            "The copy of AUC at {} has no backend folder, so it is not complete.",
            app.display()
        );
    }

    let uv = ensure_uv(ctx)?;
    let python_dir = ctx.layout.python_install_dir();
    ensure_dir(&python_dir)?;
    let uv_env = vec![(
        "UV_PYTHON_INSTALL_DIR".to_string(),
        python_dir.display().to_string(),
    )];

    ctx.progress.detail(
        StepId::Python,
        format!("Installing Python {PYTHON_VERSION}"),
    );
    Cmd::new(uv.display().to_string())
        .args(["python", "install", PYTHON_VERSION])
        .envs(uv_env.clone())
        .timeout(Duration::from_secs(20 * 60))
        .run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            &format!("Installing Python {PYTHON_VERSION}"),
        )?;

    let venv = backend.join("venv");
    ctx.progress
        .detail(StepId::Python, "Creating the Python environment");
    // `--clear` lets Repair and same-version Update replace an environment
    // that is already there instead of stopping with "already exists".
    Cmd::new(uv.display().to_string())
        .args(["venv", "--clear", "--python", PYTHON_VERSION])
        .arg(venv.display().to_string())
        .envs(uv_env.clone())
        .timeout(Duration::from_secs(10 * 60))
        .run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            "Creating the Python environment",
        )?;

    let python = venv.join("bin/python");

    ctx.progress
        .detail(StepId::Python, "Installing the core packages");
    pip_install(ctx, &uv, &python, &backend, "requirements.txt", &uv_env).context(
        "The packages AUC needs to run at all would not install, so it cannot start yet.",
    )?;

    // The ACGME index layer. A warning, never a failure — exactly as setup.sh
    // treats it, and for the same reason.
    ctx.progress
        .detail(StepId::Python, "Installing the ACGME index layer");
    let rag = pip_install(ctx, &uv, &python, &backend, "requirements-rag.txt", &uv_env);
    match rag {
        Ok(()) => {
            ctx.rag_installed = true;
            ctx.progress.done(StepId::Python);
        }
        Err(err) => {
            if crate::engine::cancel::is_cancelled_error(&err) {
                return Err(err);
            }
            ctx.rag_installed = false;
            ctx.log(format!("{err}"));
            ctx.warn(
                "The ACGME index layer (chromadb) would not install. Residents, notes, \
                 follow-ups, the CCC drawer and PDF export all work; what you lose is summary \
                 generation, and the reference index step was skipped because of it. \
                 To retry later: backend/venv/bin/python -m pip install -r \
                 backend/requirements-rag.txt",
            );
            ctx.progress.warning(
                StepId::Python,
                "The ACGME index layer did not install — summaries are unavailable",
            );
        }
    }
    Ok(())
}

fn pip_install(
    ctx: &Ctx,
    uv: &Path,
    python: &Path,
    backend: &Path,
    requirements: &str,
    uv_env: &[(String, String)],
) -> Result<()> {
    let file = backend.join(requirements);
    if !file.is_file() {
        anyhow::bail!(
            "{} is missing from this copy of AUC, so its packages could not be installed.",
            file.display()
        );
    }
    Cmd::new(uv.display().to_string())
        .args(["pip", "install", "--python"])
        .arg(python.display().to_string())
        .args(["-r", requirements])
        .cwd(backend)
        .envs(uv_env.to_vec())
        .timeout(Duration::from_secs(45 * 60))
        .run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            &format!("Installing the packages listed in {requirements}"),
        )?;
    Ok(())
}

/// Put uv in $AUC_HOME/tools, downloading it if it is not there yet.
fn ensure_uv(ctx: &mut Ctx) -> Result<PathBuf> {
    let uv = ctx.layout.uv_bin();
    if uv.is_file() {
        ctx.log(format!("Using the uv already at {}.", uv.display()));
        return Ok(uv);
    }

    let url = uv_download_url(crate::engine::arch_name())?;
    ctx.progress
        .detail(StepId::Python, "Downloading the Python installer (uv)");
    ensure_dir(&ctx.layout.tools_dir())?;
    let archive = ctx.layout.tools_dir().join(".uv.tar.gz");
    ctx.log(format!("Downloading uv from {url}"));

    {
        let progress = &ctx.progress;
        release::download(&url, &archive, &ctx.cancel, &mut |done, total| {
            let fraction = total
                .filter(|t| *t > 0)
                .map(|total| (done as f64 / total as f64).clamp(0.0, 1.0));
            progress.tick(
                StepId::Python,
                format!("Downloading uv — {}", super::download::format_size(done)),
                fraction,
            );
        })?;
    }

    // The tarball wraps everything in uv-<triple>/; the stripping extractor
    // leaves us uv and uvx side by side.
    let unpacked = ctx.layout.tools_dir().join(".uv");
    if unpacked.exists() {
        let _ = std::fs::remove_dir_all(&unpacked);
    }
    release::extract_tar_gz(&archive, &unpacked)?;
    let _ = std::fs::remove_file(&archive);

    let downloaded = unpacked.join("uv");
    if !downloaded.is_file() {
        anyhow::bail!(
            "The uv download did not contain the uv program, so Python cannot be set up. \
             Check this machine's internet connection and try again."
        );
    }
    if uv.exists() {
        let _ = std::fs::remove_file(&uv);
    }
    std::fs::rename(&downloaded, &uv)
        .with_context(|| format!("Could not put uv at {}.", uv.display()))?;
    make_executable(&uv)?;
    let _ = std::fs::remove_dir_all(&unpacked);
    Ok(uv)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn uv_is_downloaded_for_the_processor_this_machine_has() {
        assert_eq!(
            uv_download_url("x86_64").expect("intel"),
            "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz"
        );
        assert_eq!(
            uv_download_url("aarch64").expect("arm, as on a DGX Spark"),
            "https://github.com/astral-sh/uv/releases/latest/download/uv-aarch64-unknown-linux-gnu.tar.gz"
        );
    }

    #[test]
    fn an_unknown_processor_is_turned_away_in_plain_language() {
        let err = uv_download_url("riscv64").expect_err("no build for it");
        assert!(err.to_string().contains("riscv64"), "{err}");
        assert!(err.to_string().contains("64-bit"), "{err}");
    }
}
