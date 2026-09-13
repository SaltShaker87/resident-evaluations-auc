//! The `ollama` and `models` steps.
//!
//! Ollama writes the summaries whichever retrieval engine is in use, so it is
//! needed whenever the AI features are on. Models are pulled through Ollama's
//! own HTTP API rather than by running `ollama pull`, because the API reports
//! bytes as it goes and a progress bar is the difference between "it is
//! working" and "it has hung" on a 5 GB download.

use std::io::{BufRead, BufReader};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};

use crate::engine::recommend::EMBED_MODEL;
use crate::engine::types::StepId;
use crate::engine::{net, Ctx};
use crate::platform::Platform;

pub const DEFAULT_OLLAMA_URL: &str = "http://localhost:11434";

/// Ollama's own installer. It is the supported way to install it, it needs
/// root, and it is on the short list in CONTRACT.md section 6.
pub const INSTALL_SCRIPT: &str = "curl -fsSL https://ollama.com/install.sh | sh";

/// The `ollama` step. Skipped when Ollama is already here, because its
/// installer would replace a version the user may have chosen deliberately.
pub fn install(platform: &dyn Platform, ctx: &mut Ctx, already_present: bool) -> Result<()> {
    ctx.progress.start(StepId::Ollama);
    if already_present {
        ctx.progress
            .skipped(StepId::Ollama, "Ollama is already installed");
        return Ok(());
    }
    platform.install_ollama(ctx)?;
    ctx.progress.done(StepId::Ollama);
    Ok(())
}

/// Both models the install needs: the one the user chose, and the embedding
/// model, which is not a choice because the ACGME index is built with it.
pub fn models_to_pull(chosen: Option<&str>) -> Vec<String> {
    let mut models = Vec::new();
    if let Some(model) = chosen {
        if !model.trim().is_empty() {
            models.push(model.trim().to_string());
        }
    }
    if !models.iter().any(|m| m == EMBED_MODEL) {
        models.push(EMBED_MODEL.to_string());
    }
    models
}

/// The `models` step.
pub fn pull_models(ctx: &mut Ctx, base_url: &str, models: &[String]) -> Result<()> {
    ctx.progress.start(StepId::Models);
    wait_until_answering(ctx, base_url)?;

    for (index, model) in models.iter().enumerate() {
        ctx.log(format!(
            "Pulling {model} ({} of {}).",
            index + 1,
            models.len()
        ));
        pull_one(ctx, base_url, model)?;
    }
    ctx.progress
        .done_with(StepId::Models, format!("{} models ready", models.len()));
    Ok(())
}

/// Ollama's installer starts its service, but starting is not the same as
/// answering; a pull sent too early fails for no good reason.
fn wait_until_answering(ctx: &mut Ctx, base_url: &str) -> Result<()> {
    let url = format!("{base_url}/api/tags");
    let deadline = Instant::now() + Duration::from_secs(60);
    let mut said_waiting = false;
    loop {
        ctx.cancel.check()?;
        if net::get_ok(&url, Duration::from_secs(3)) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(anyhow!(
                "Ollama is not answering at {base_url}, so the models could not be downloaded. \
                 Check it with: systemctl status ollama"
            ));
        }
        if !said_waiting {
            said_waiting = true;
            ctx.progress
                .detail(StepId::Models, "Waiting for Ollama to start");
        }
        std::thread::sleep(Duration::from_secs(2));
    }
}

fn pull_one(ctx: &mut Ctx, base_url: &str, model: &str) -> Result<()> {
    // No overall timeout: a first pull of a large model legitimately takes
    // half an hour on a slow connection. Cancel still works, because every
    // line that arrives is a chance to check it.
    let client = reqwest::blocking::Client::builder()
        .user_agent(net::USER_AGENT)
        .timeout(None)
        .build()
        .context("Could not set up the connection to Ollama.")?;

    ctx.progress
        .tick(StepId::Models, format!("{model} — starting"), None);
    let response = client
        .post(format!("{base_url}/api/pull"))
        .json(&serde_json::json!({ "model": model, "stream": true }))
        .send()
        .with_context(|| format!("Could not ask Ollama to download {model}."))?;
    if !response.status().is_success() {
        return Err(anyhow!(
            "Ollama refused to download {model} ({}). Check the model name.",
            response.status()
        ));
    }

    let progress = &ctx.progress;
    let cancel = ctx.cancel.clone();
    let emitter = ctx.emitter.clone();
    let mut last_status = String::new();
    consume_pull_stream(BufReader::new(response), &mut |update| {
        // Every named stage is worth a log line, but only once.
        if let Some(status) = &update.status {
            if *status != last_status {
                last_status = status.clone();
                emitter.log(&format!("  {model}: {status}"));
            }
        }
        progress.tick(
            StepId::Models,
            format_pull_detail(model, update),
            update.fraction(),
        );
        cancel.check()
    })
    .with_context(|| format!("Downloading the model {model} did not finish."))?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Ollama's progress stream
// ---------------------------------------------------------------------------

/// One line of Ollama's pull stream.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PullProgress {
    pub status: Option<String>,
    pub completed: Option<u64>,
    pub total: Option<u64>,
    pub error: Option<String>,
}

impl PullProgress {
    /// How far through this layer we are, when the line says enough to tell.
    pub fn fraction(&self) -> Option<f64> {
        match (self.completed, self.total) {
            (Some(done), Some(total)) if total > 0 => {
                Some((done as f64 / total as f64).clamp(0.0, 1.0))
            }
            _ => None,
        }
    }
}

/// Read one NDJSON line. Anything unrecognisable is `None` rather than an
/// error: Ollama is free to add fields, and a blank line is not a problem.
pub fn parse_pull_line(line: &str) -> Option<PullProgress> {
    let line = line.trim();
    if line.is_empty() {
        return None;
    }
    let value: serde_json::Value = serde_json::from_str(line).ok()?;
    let object = value.as_object()?;
    let get_u64 = |key: &str| object.get(key).and_then(|v| v.as_u64());
    let get_str = |key: &str| {
        object
            .get(key)
            .and_then(|v| v.as_str())
            .map(str::to_string)
            .filter(|s| !s.is_empty())
    };
    Some(PullProgress {
        status: get_str("status"),
        completed: get_u64("completed"),
        total: get_u64("total"),
        error: get_str("error"),
    })
}

/// Walk the whole stream, handing each update to `on_update`. Stops at the
/// first line that reports an error, or when `on_update` says to stop.
pub fn consume_pull_stream<R: BufRead>(
    reader: R,
    on_update: &mut dyn FnMut(&PullProgress) -> Result<()>,
) -> Result<()> {
    for line in reader.lines() {
        let line = line.context("The connection to Ollama broke part way through.")?;
        let Some(update) = parse_pull_line(&line) else {
            continue;
        };
        if let Some(error) = &update.error {
            return Err(anyhow!("Ollama said: {error}"));
        }
        on_update(&update)?;
    }
    Ok(())
}

/// "qwen3.5:9b — 2.1 GB of 5.6 GB", or the stage's own words when there are
/// no bytes to report yet.
pub fn format_pull_detail(model: &str, update: &PullProgress) -> String {
    match (update.completed, update.total) {
        (Some(done), Some(total)) if total > 0 => {
            format!("{model} — {} of {}", format_gb(done), format_gb(total))
        }
        _ => match &update.status {
            Some(status) => format!("{model} — {status}"),
            None => model.to_string(),
        },
    }
}

/// Gigabytes the way a download dialog writes them, dropping to megabytes for
/// the small files a pull starts with.
fn format_gb(bytes: u64) -> String {
    const GB: f64 = 1_000_000_000.0;
    const MB: f64 = 1_000_000.0;
    let bytes = bytes as f64;
    if bytes >= GB {
        format!("{:.1} GB", bytes / GB)
    } else {
        format!("{:.0} MB", (bytes / MB).max(1.0))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn a_progress_line_becomes_bytes_and_a_fraction() {
        let update = parse_pull_line(
            r#"{"status":"pulling 4fed7364ee3e","digest":"sha256:4fed","total":5600000000,"completed":2100000000}"#,
        )
        .expect("should parse");
        assert_eq!(update.completed, Some(2_100_000_000));
        assert_eq!(update.total, Some(5_600_000_000));
        assert_eq!(
            format_pull_detail("qwen3.5:9b", &update),
            "qwen3.5:9b — 2.1 GB of 5.6 GB"
        );
        assert_eq!(update.fraction(), Some(0.375));
    }

    #[test]
    fn a_stage_with_no_bytes_yet_shows_its_own_words() {
        let update = parse_pull_line(r#"{"status":"pulling manifest"}"#).expect("should parse");
        assert_eq!(update.fraction(), None);
        assert_eq!(
            format_pull_detail("qwen3.5:9b", &update),
            "qwen3.5:9b — pulling manifest"
        );
    }

    #[test]
    fn small_files_are_reported_in_megabytes() {
        let update = PullProgress {
            completed: Some(12_000_000),
            total: Some(480_000_000),
            ..Default::default()
        };
        assert_eq!(
            format_pull_detail("qwen3-embedding:0.6b", &update),
            "qwen3-embedding:0.6b — 12 MB of 480 MB"
        );
    }

    #[test]
    fn blank_and_broken_lines_are_ignored_rather_than_fatal() {
        assert!(parse_pull_line("").is_none());
        assert!(parse_pull_line("   ").is_none());
        assert!(parse_pull_line("not json at all").is_none());
        assert!(parse_pull_line("[1,2,3]").is_none());
    }

    #[test]
    fn a_whole_stream_is_followed_from_manifest_to_success() {
        let stream = concat!(
            "{\"status\":\"pulling manifest\"}\n",
            "{\"status\":\"pulling 4fed\",\"total\":5600000000,\"completed\":0}\n",
            "\n",
            "{\"status\":\"pulling 4fed\",\"total\":5600000000,\"completed\":2800000000}\n",
            "{\"status\":\"verifying sha256 digest\"}\n",
            "{\"status\":\"success\"}\n"
        );
        let mut details = Vec::new();
        consume_pull_stream(Cursor::new(stream), &mut |update| {
            details.push(format_pull_detail("qwen3.5:9b", update));
            Ok(())
        })
        .expect("the stream should be read to the end");

        assert_eq!(details.len(), 5, "the blank line should be skipped");
        assert_eq!(details[0], "qwen3.5:9b — pulling manifest");
        assert_eq!(details[2], "qwen3.5:9b — 2.8 GB of 5.6 GB");
        assert_eq!(details[4], "qwen3.5:9b — success");
    }

    #[test]
    fn an_error_in_the_stream_stops_the_pull_and_keeps_ollamas_own_words() {
        let stream = concat!(
            "{\"status\":\"pulling manifest\"}\n",
            "{\"error\":\"model 'qwen3.5:9000b' not found\"}\n"
        );
        let err = consume_pull_stream(Cursor::new(stream), &mut |_| Ok(()))
            .expect_err("an error line must stop it");
        assert!(err.to_string().contains("not found"), "{err}");
    }

    #[test]
    fn the_embedding_model_is_always_pulled_and_never_twice() {
        assert_eq!(
            models_to_pull(Some("qwen3.5:9b")),
            vec!["qwen3.5:9b".to_string(), EMBED_MODEL.to_string()]
        );
        assert_eq!(models_to_pull(None), vec![EMBED_MODEL.to_string()]);
        assert_eq!(
            models_to_pull(Some(EMBED_MODEL)),
            vec![EMBED_MODEL.to_string()]
        );
        assert_eq!(models_to_pull(Some("  ")), vec![EMBED_MODEL.to_string()]);
    }
}
