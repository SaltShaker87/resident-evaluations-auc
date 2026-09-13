//! The `configure` step: `auc.env`, the data folder, and the local-only check.
//!
//! `auc.env` is the only place AUC is configured, and it is a file the user is
//! allowed to edit. So the rule from `setup.sh` holds: on an update, a value
//! they changed by hand wins over the one we would generate. What they have
//! just chosen on the screen is different — that is a fresh instruction, and
//! it wins over what the file used to say.

use std::collections::BTreeMap;

use anyhow::Result;

use crate::engine::envfile::EnvFile;
use crate::engine::guard;
use crate::engine::layout::{ensure_dir, Layout};
use crate::engine::recommend::EMBED_MODEL;
use crate::engine::steps::ollama::DEFAULT_OLLAMA_URL;
use crate::engine::types::{NetworkScope, StepId};
use crate::engine::Ctx;

pub const DEFAULT_PORT: &str = "3000";
pub const BACKUP_KEEP_DAYS: &str = "14";

/// The setting that pins AUC to the Standard engine. Present only while
/// Nemotron was chosen but is not answering yet.
pub const ENGINE_DEFAULT_KEY: &str = "AUC_RETRIEVAL_ENGINE_DEFAULT";

pub enum Mode<'a> {
    /// Choices the user has just made; these win over the existing file.
    Chosen {
        scope: NetworkScope,
        model: Option<&'a str>,
    },
    /// An update or a repair: the file wins, and only genuinely new settings
    /// are added.
    Merge,
}

pub struct Configured {
    pub env: EnvFile,
    pub port: String,
    /// True when the embedding model is not the one the index was built with,
    /// which is the only reason an update has to rebuild the index.
    pub embed_model_changed: bool,
}

/// Everything the installer knows how to set, with its default value.
pub fn generated_env(
    layout: &Layout,
    scope: NetworkScope,
    model: Option<&str>,
) -> Vec<(String, String)> {
    let mut generated = vec![
        (
            "AUC_DATA_DIR".to_string(),
            layout.data_dir().display().to_string(),
        ),
        ("AUC_HOST".to_string(), scope.host().to_string()),
        ("AUC_PORT".to_string(), DEFAULT_PORT.to_string()),
        (
            "AUC_BACKUP_DIR".to_string(),
            layout.backups_dir().display().to_string(),
        ),
        (
            "AUC_BACKUP_KEEP_DAYS".to_string(),
            BACKUP_KEEP_DAYS.to_string(),
        ),
        ("OLLAMA_URL".to_string(), DEFAULT_OLLAMA_URL.to_string()),
        ("AUC_EMBED_MODEL".to_string(), EMBED_MODEL.to_string()),
        // Always this machine. `guard` refuses anything else.
        (
            "AUC_NIM_EMBED_URL".to_string(),
            guard::NIM_EMBED_URL.to_string(),
        ),
        (
            "AUC_NIM_RERANK_URL".to_string(),
            guard::NIM_RERANK_URL.to_string(),
        ),
    ];
    if let Some(model) = model.map(str::trim).filter(|m| !m.is_empty()) {
        generated.push(("OLLAMA_MODEL".to_string(), model.to_string()));
    }
    generated
}

pub fn run(
    ctx: &mut Ctx,
    mode: Mode<'_>,
    adopted_env: Option<&BTreeMap<String, String>>,
) -> Result<Configured> {
    ctx.progress.start(StepId::Configure);

    ensure_dir(&ctx.layout.data_dir())?;
    ensure_dir(&ctx.layout.data_dir().join("photos"))?;
    ensure_dir(&ctx.layout.backups_dir())?;

    let path = ctx.layout.env_file();
    let mut env = EnvFile::load(&path)?;
    let embed_before = env.get("AUC_EMBED_MODEL").map(str::to_string);

    // Settings carried over from a hand-made install: they are the user's
    // choices too, so they beat our defaults, but not the file we already have.
    if let Some(adopted) = adopted_env {
        let carried: Vec<(String, String)> = adopted
            .iter()
            .filter(|(key, _)| key.starts_with("AUC_") || key.starts_with("OLLAMA_"))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        if !carried.is_empty() {
            ctx.log(format!(
                "Carrying {} settings across from the existing install.",
                carried.len()
            ));
            env.merge_generated(carried);
        }
    }

    let (scope, model) = match mode {
        Mode::Chosen { scope, model } => (Some(scope), model),
        Mode::Merge => (None, None),
    };
    if let Some(scope) = scope {
        // A fresh instruction from the screen, so it replaces what was there.
        env.set("AUC_HOST", scope.host());
        if let Some(model) = model.map(str::trim).filter(|m| !m.is_empty()) {
            env.set("OLLAMA_MODEL", model);
        }
    }
    env.merge_generated(generated_env(
        &ctx.layout,
        scope.unwrap_or(NetworkScope::Local),
        model,
    ));

    // Nothing is written until we know AUC will not be pointed off-machine.
    guard::check_env_local(&env.map())?;
    env.save(&path)?;
    ctx.log(format!("Wrote {}.", path.display()));

    let port = port_of(&env);
    let embed_after = env.get("AUC_EMBED_MODEL").map(str::to_string);
    let configured = Configured {
        embed_model_changed: embed_before.is_some() && embed_before != embed_after,
        env,
        port,
    };
    ctx.progress.done_with(
        StepId::Configure,
        format!("Settings written to {}", path.display()),
    );
    Ok(configured)
}

/// Add or remove the line that pins AUC to the Standard engine.
pub fn set_engine_default(ctx: &mut Ctx, engine: Option<&str>) -> Result<()> {
    let path = ctx.layout.env_file();
    let mut env = EnvFile::load(&path)?;
    match engine {
        Some(engine) => {
            env.set(ENGINE_DEFAULT_KEY, engine);
            ctx.log(format!(
                "Set {ENGINE_DEFAULT_KEY}={engine} in {} for now.",
                path.display()
            ));
        }
        None => {
            if !env.contains(ENGINE_DEFAULT_KEY) {
                return Ok(());
            }
            env.remove(ENGINE_DEFAULT_KEY);
            ctx.log(format!(
                "Removed {ENGINE_DEFAULT_KEY} from {}: AUC can use NVIDIA Nemotron now.",
                path.display()
            ));
        }
    }
    env.save(&path)
}

pub fn port_of(env: &EnvFile) -> String {
    env.get("AUC_PORT")
        .map(str::to_string)
        .filter(|p| !p.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_PORT.to_string())
}

/// The port AUC is on, read from the settings on disk.
pub fn port_on_disk(layout: &Layout) -> String {
    EnvFile::load(&layout.env_file())
        .map(|env| port_of(&env))
        .unwrap_or_else(|_| DEFAULT_PORT.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn the_generated_settings_are_the_ones_the_contract_lists() {
        let layout = Layout::rooted_at(Path::new("/home/you"));
        let generated = generated_env(&layout, NetworkScope::Local, Some("qwen3.5:9b"));
        let keys: Vec<&str> = generated.iter().map(|(k, _)| k.as_str()).collect();
        for expected in [
            "AUC_DATA_DIR",
            "AUC_HOST",
            "AUC_PORT",
            "AUC_BACKUP_DIR",
            "AUC_BACKUP_KEEP_DAYS",
            "OLLAMA_URL",
            "OLLAMA_MODEL",
            "AUC_EMBED_MODEL",
            "AUC_NIM_EMBED_URL",
            "AUC_NIM_RERANK_URL",
        ] {
            assert!(keys.contains(&expected), "missing {expected}");
        }
        let map: BTreeMap<&str, &str> = generated
            .iter()
            .map(|(k, v)| (k.as_str(), v.as_str()))
            .collect();
        assert_eq!(map["AUC_HOST"], "127.0.0.1");
        assert_eq!(map["AUC_PORT"], "3000");
        assert_eq!(map["AUC_EMBED_MODEL"], "qwen3-embedding:0.6b");
        assert_eq!(map["AUC_NIM_EMBED_URL"], "http://localhost:8001");
        assert_eq!(map["AUC_NIM_RERANK_URL"], "http://localhost:8002");
        assert_eq!(map["AUC_DATA_DIR"], "/home/you/.local/share/auc/data");
    }

    #[test]
    fn the_lan_choice_listens_on_every_interface() {
        let layout = Layout::rooted_at(Path::new("/home/you"));
        let generated = generated_env(&layout, NetworkScope::Lan, None);
        let host = generated
            .iter()
            .find(|(k, _)| k == "AUC_HOST")
            .map(|(_, v)| v.as_str());
        assert_eq!(host, Some("0.0.0.0"));
        // No model chosen means no OLLAMA_MODEL line, rather than an empty one.
        assert!(!generated.iter().any(|(k, _)| k == "OLLAMA_MODEL"));
    }

    #[test]
    fn everything_we_generate_passes_the_local_only_check() {
        let layout = Layout::rooted_at(Path::new("/home/you"));
        let map: BTreeMap<String, String> = generated_env(&layout, NetworkScope::Lan, None)
            .into_iter()
            .collect();
        guard::check_env_local(&map).expect("what we generate must be local");
    }

    #[test]
    fn the_port_falls_back_to_three_thousand() {
        assert_eq!(port_of(&EnvFile::parse("")), "3000");
        assert_eq!(port_of(&EnvFile::parse("AUC_PORT=3100\n")), "3100");
        assert_eq!(port_of(&EnvFile::parse("AUC_PORT=\n")), "3000");
    }
}
