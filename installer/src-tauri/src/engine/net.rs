//! The few HTTP calls the installer makes: is there internet, is Ollama
//! answering, are the Nemotron containers ready, and downloading a release.
//!
//! Everything here has a timeout. A hung request on a machine reached over
//! SSH looks exactly like a hung installer.

use std::time::Duration;

use anyhow::{Context, Result};
use reqwest::blocking::Client;

/// Sent with every request, so that a puzzled server log says who called.
pub const USER_AGENT: &str = concat!("auc-installer/", env!("CARGO_PKG_VERSION"));

pub fn client(timeout: Duration) -> Result<Client> {
    Client::builder()
        .timeout(timeout)
        .user_agent(USER_AGENT)
        .build()
        .context("Could not set up the network connection for this machine.")
}

/// Can we reach GitHub? A HEAD request, because the answer is all we want.
pub fn can_reach(url: &str, timeout: Duration) -> bool {
    client(timeout)
        .and_then(|c| c.head(url).send().context("no answer"))
        .map(|r| r.status().is_success() || r.status().is_redirection())
        .unwrap_or(false)
}

/// Did a GET succeed? Used for health endpoints, where the body is noise.
pub fn get_ok(url: &str, timeout: Duration) -> bool {
    client(timeout)
        .and_then(|c| c.get(url).send().context("no answer"))
        .map(|r| r.status().is_success())
        .unwrap_or(false)
}

pub fn get_text(url: &str, timeout: Duration) -> Result<String> {
    let response = client(timeout)?.get(url).send().with_context(|| {
        format!("Could not reach {url}. Check this machine's internet connection.")
    })?;
    let status = response.status();
    let body = response
        .text()
        .with_context(|| format!("Could not read the answer from {url}."))?;
    if !status.is_success() {
        anyhow::bail!("{url} answered {status}. {}", first_line(&body));
    }
    Ok(body)
}

fn first_line(text: &str) -> String {
    text.lines().next().unwrap_or_default().trim().to_string()
}
