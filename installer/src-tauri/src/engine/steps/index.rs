//! The `index` step: build the ACGME reference index.
//!
//! `build_index.py` builds one index per retrieval engine it can actually
//! reach, and skips the others with a reason. Summary generation needs it, but
//! the rest of AUC does not, so a failure here is a warning: the app installs,
//! and Settings says what is missing.

use std::path::Path;
use std::time::Duration;

use anyhow::Result;

use crate::engine::process::Cmd;
use crate::engine::types::StepId;
use crate::engine::Ctx;

/// Why the index step might be skipped without anything being wrong.
pub enum Skip {
    /// The AI features were turned off, so there is nothing to search.
    AiDisabled,
    /// An update where nothing that affects the index changed.
    NothingChanged,
}

pub fn skip(ctx: &mut Ctx, reason: Skip) {
    ctx.progress.start(StepId::Index);
    let text = match reason {
        Skip::AiDisabled => "The AI summary features were not turned on",
        Skip::NothingChanged => "Nothing that affects the index has changed",
    };
    ctx.progress.skipped(StepId::Index, text);
}

pub fn run(ctx: &mut Ctx, app: &Path) -> Result<()> {
    ctx.progress.start(StepId::Index);

    if !ctx.rag_installed {
        ctx.progress.skipped(
            StepId::Index,
            "The ACGME index layer (chromadb) did not install, so there is nothing to build with",
        );
        return Ok(());
    }

    let python = ctx.layout.venv_python(app);
    let script = app.join("rag/build_index.py");
    if !python.is_file() || !script.is_file() {
        ctx.progress
            .skipped(StepId::Index, "This copy of AUC has no index to build");
        return Ok(());
    }

    let env = crate::engine::env_for_scripts(&ctx.layout)?;
    let output = Cmd::new(python.display().to_string())
        .arg(script.display().to_string())
        .cwd(app)
        .envs(env)
        .timeout(Duration::from_secs(60 * 60))
        .run(ctx.emitter.as_ref(), &ctx.cancel)?;

    if output.success {
        ctx.progress.done(StepId::Index);
        return Ok(());
    }

    let tail = output.tail(15);
    ctx.warn(format!(
        "The ACGME reference index was not built for every engine, so summary generation may not \
         work yet. Once the cause is fixed, run: {} {}\nThe last lines were:\n{tail}",
        python.display(),
        script.display()
    ));
    ctx.progress.warning(
        StepId::Index,
        "Not built for every engine — summaries may not work yet",
    );
    Ok(())
}
