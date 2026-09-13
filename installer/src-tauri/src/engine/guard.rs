//! The local-only guard.
//!
//! Resident comments are sent to the Nemotron containers. They must never
//! leave this machine, so two things are checked before the containers start
//! and before `auc.env` is written:
//!
//!   * the Nemotron URLs AUC will use point at this machine, not at
//!     build.nvidia.com or any other hosted endpoint, and
//!   * every port in `nim/docker-compose.yml` is published on the loopback
//!     address only, so nothing on the network can reach them either.
//!
//! A failure here stops the step. This is the one place in the installer that
//! refuses to carry on with a warning.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{anyhow, Context, Result};

/// The two URLs the installer writes. Always this machine.
pub const NIM_EMBED_URL: &str = "http://localhost:8001";
pub const NIM_RERANK_URL: &str = "http://localhost:8002";

/// The settings whose value has to be a local address.
pub const LOCAL_ONLY_KEYS: [&str; 2] = ["AUC_NIM_EMBED_URL", "AUC_NIM_RERANK_URL"];

/// Is this host name or address this machine, and only this machine?
pub fn is_loopback_host(host: &str) -> bool {
    let host = host.trim().trim_matches(|c| c == '[' || c == ']');
    host.eq_ignore_ascii_case("localhost")
        || host == "::1"
        || host == "0:0:0:0:0:0:0:1"
        // The whole 127.x.x.x range never leaves the machine.
        || host
            .split_once('.')
            .is_some_and(|(first, _)| first == "127" && host.split('.').count() == 4)
}

/// The host part of a URL, without needing a full URL parser for the three
/// shapes that actually turn up here.
pub fn host_of(url: &str) -> Option<String> {
    let rest = url.split_once("://").map(|(_, r)| r).unwrap_or(url);
    let authority = rest.split(['/', '?', '#']).next()?;
    let authority = authority
        .rsplit_once('@')
        .map(|(_, a)| a)
        .unwrap_or(authority);
    if authority.is_empty() {
        return None;
    }
    // [::1]:8001
    if let Some(after_bracket) = authority.strip_prefix('[') {
        return after_bracket.split(']').next().map(str::to_string);
    }
    authority.split(':').next().map(str::to_string)
}

/// One URL, with the setting's name in the message so the user knows what to
/// change.
pub fn check_url_local(key: &str, url: &str) -> Result<()> {
    let host = host_of(url).ok_or_else(|| {
        anyhow!("{key} is set to \"{url}\", which is not an address AUC can understand. It has to point at this machine, for example {NIM_EMBED_URL}.")
    })?;
    if !is_loopback_host(&host) {
        return Err(anyhow!(
            "{key} points at {host}, which is somewhere other than this machine. \
             Resident comments are sent to the Nemotron service, so it has to run here: \
             set {key} back to {NIM_EMBED_URL} (embedding) or {NIM_RERANK_URL} (reranking). \
             Nothing has been started."
        ));
    }
    Ok(())
}

/// Both Nemotron URLs in a set of settings, before we write it or use it.
pub fn check_env_local(env: &BTreeMap<String, String>) -> Result<()> {
    for key in LOCAL_ONLY_KEYS {
        if let Some(value) = env.get(key) {
            check_url_local(key, value)?;
        }
    }
    Ok(())
}

/// Every published port in a compose file has to be bound to loopback.
///
/// The file ships bound to 127.0.0.1; this catches an edited copy, whether
/// the edit was deliberate or came from a merge.
pub fn check_compose_ports_local(yaml: &str) -> Result<()> {
    for entry in compose_port_entries(yaml) {
        let host = published_host(&entry).ok_or_else(|| {
            anyhow!(
                "nim/docker-compose.yml publishes a port as \"{entry}\", which does not say which \
                 address to publish it on, so Docker would offer it to the whole network. \
                 Resident comments are sent to these containers, so they must answer this machine \
                 only: write it as \"127.0.0.1:8001:8000\". Nothing has been started."
            )
        })?;
        if !is_loopback_host(&host) {
            return Err(anyhow!(
                "nim/docker-compose.yml publishes a port on {host} (\"{entry}\"), which lets other \
                 machines on the network reach the Nemotron containers. Resident comments are sent \
                 to them, so they must answer this machine only: write it as \
                 \"127.0.0.1:8001:8000\". Nothing has been started."
            ));
        }
    }
    Ok(())
}

pub fn check_compose_file(path: &Path) -> Result<()> {
    let yaml = std::fs::read_to_string(path).with_context(|| {
        format!(
            "Could not read {}, which says how the Nemotron containers are started.",
            path.display()
        )
    })?;
    check_compose_ports_local(&yaml)
}

/// Pull out everything listed under a `ports:` key.
///
/// A hand-rolled reader rather than a YAML library: it only has to understand
/// the two shapes compose allows, and being strict about anything it does not
/// recognise is the safe direction to fail in.
fn compose_port_entries(yaml: &str) -> Vec<String> {
    let mut entries: Vec<String> = Vec::new();
    let mut ports_indent: Option<usize> = None;
    // Set while reading the indented keys of a long-form entry
    // (- target: 8000 / published: 8001 / host_ip: 127.0.0.1).
    let mut long_form: Option<(usize, Vec<String>)> = None;

    for raw in yaml.lines() {
        let line = strip_comment(raw);
        if line.trim().is_empty() {
            continue;
        }
        let indent = line.len() - line.trim_start().len();
        let trimmed = line.trim();

        if let Some((item_indent, keys)) = long_form.as_mut() {
            if indent > *item_indent && !trimmed.starts_with('-') {
                keys.push(trimmed.to_string());
                continue;
            }
            let (_, keys) = long_form.take().expect("just checked");
            entries.push(keys.join(" "));
        }

        if let Some(base) = ports_indent {
            if indent <= base {
                ports_indent = None;
            } else if let Some(item) = trimmed.strip_prefix('-') {
                let item = unquote(item.trim());
                // An IPv6 binding has colons of its own; it is still the short
                // form, and splitting it up would misread it as the long one.
                let short_form = item.starts_with('[') || item.split(':').all(is_port_ish);
                if item.contains(':') && !short_form {
                    // Long form starts on this line and continues below it.
                    long_form = Some((indent, vec![item.to_string()]));
                } else {
                    entries.push(item.to_string());
                }
                continue;
            }
        }
        if ports_indent.is_none() && trimmed == "ports:" {
            ports_indent = Some(indent);
        }
    }
    if let Some((_, keys)) = long_form {
        entries.push(keys.join(" "));
    }
    entries
}

/// Which address a `ports:` entry publishes on, or `None` when it does not
/// say — which means every interface, and is therefore a failure.
fn published_host(entry: &str) -> Option<String> {
    let entry = unquote(entry.trim());
    // Long form: target/published/host_ip keys.
    if entry.contains("target:") || entry.contains("published:") {
        let host = entry
            .split_whitespace()
            .collect::<Vec<_>>()
            .windows(2)
            .find(|pair| pair[0] == "host_ip:")
            .map(|pair| unquote(pair[1]).to_string());
        return host;
    }
    // [::1]:8001:8000
    if let Some(after_bracket) = entry.strip_prefix('[') {
        let (host, rest) = after_bracket.split_once(']')?;
        // The remainder still has to be host:container, or there is no host part.
        if rest.trim_start_matches(':').split(':').count() < 2 {
            return None;
        }
        return Some(host.to_string());
    }
    let parts: Vec<&str> = entry.split(':').collect();
    match parts.len() {
        // "8000" or "8001:8000" — no address, so Docker uses all of them.
        0..=2 => None,
        _ => Some(parts[0].to_string()),
    }
}

fn is_port_ish(text: &str) -> bool {
    let text = text.trim();
    !text.is_empty()
        && text
            .chars()
            .all(|c| c.is_ascii_digit() || c == '.' || c == '-' || c == '/')
}

fn strip_comment(line: &str) -> &str {
    match line.find(" #") {
        Some(at) => &line[..at],
        None if line.trim_start().starts_with('#') => "",
        None => line,
    }
}

fn unquote(text: &str) -> &str {
    let text = text.trim();
    for quote in ['"', '\''] {
        if text.len() >= 2 && text.starts_with(quote) && text.ends_with(quote) {
            return &text[1..text.len() - 1];
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The relevant part of auc/nim/docker-compose.yml as it ships.
    const SHIPPED: &str = r#"
x-nim: &nim
  runtime: nvidia
  shm_size: 16gb
  user: "${NIM_UID:?start these with: bash auc/start-nemotron.sh}"
  environment:
    - NGC_API_KEY
    - HF_TOKEN
  restart: unless-stopped

services:
  nemotron-embed:
    <<: *nim
    image: nvcr.io/nim/nvidia/nemotron-3-embed-1b:2.3
    container_name: nemotron-embed
    volumes:
      - "${NIM_CACHE_DIR}/nemotron-embed/cache:/opt/cache"
    ports:
      - "127.0.0.1:8001:8000"

  nemotron-rerank:
    <<: *nim
    image: nvcr.io/nim/nvidia/llama-nemotron-rerank-vl-1b-v2:2.3
    container_name: nemotron-rerank
    ports:
      - "127.0.0.1:8002:8000"   # this machine only
"#;

    #[test]
    fn the_compose_file_we_ship_is_accepted() {
        check_compose_ports_local(SHIPPED).expect("the shipped file binds to loopback");
    }

    #[test]
    fn a_file_with_no_published_ports_at_all_is_accepted() {
        check_compose_ports_local("services:\n  a:\n    image: x\n").expect("nothing to publish");
    }

    #[test]
    fn publishing_on_every_interface_is_refused() {
        let edited = SHIPPED.replace("127.0.0.1:8001", "0.0.0.0:8001");
        let err = check_compose_ports_local(&edited).expect_err("0.0.0.0 must be refused");
        let message = err.to_string();
        assert!(message.contains("0.0.0.0"), "{message}");
        assert!(message.contains("Nothing has been started."), "{message}");
    }

    #[test]
    fn leaving_the_address_out_is_refused_because_docker_fills_in_every_interface() {
        let edited = SHIPPED.replace("\"127.0.0.1:8001:8000\"", "\"8001:8000\"");
        let err = check_compose_ports_local(&edited).expect_err("a bare mapping must be refused");
        assert!(
            err.to_string().contains("does not say which address"),
            "{err}"
        );
    }

    #[test]
    fn a_container_port_on_its_own_is_refused() {
        let yaml = "services:\n  a:\n    ports:\n      - \"8000\"\n";
        assert!(check_compose_ports_local(yaml).is_err());
    }

    #[test]
    fn the_ipv6_loopback_is_accepted() {
        let yaml = "services:\n  a:\n    ports:\n      - \"[::1]:8001:8000\"\n";
        check_compose_ports_local(yaml).expect("::1 is this machine");
    }

    #[test]
    fn a_public_address_in_the_long_form_is_refused_and_loopback_is_accepted() {
        let bad = "services:\n  a:\n    ports:\n      - target: 8000\n        published: 8001\n        host_ip: 0.0.0.0\n";
        assert!(check_compose_ports_local(bad).is_err());

        let good = "services:\n  a:\n    ports:\n      - target: 8000\n        published: 8001\n        host_ip: 127.0.0.1\n";
        check_compose_ports_local(good).expect("loopback in long form is fine");

        let missing =
            "services:\n  a:\n    ports:\n      - target: 8000\n        published: 8001\n";
        assert!(
            check_compose_ports_local(missing).is_err(),
            "no host_ip means every interface"
        );
    }

    #[test]
    fn something_that_is_not_a_ports_list_is_left_alone() {
        // `expose:` and `environment:` lists look similar and are not bindings.
        let yaml = "services:\n  a:\n    expose:\n      - \"8000\"\n    environment:\n      - NGC_API_KEY\n";
        check_compose_ports_local(yaml).expect("only ports: entries are bindings");
    }

    #[test]
    fn the_urls_we_write_are_accepted() {
        check_url_local("AUC_NIM_EMBED_URL", NIM_EMBED_URL).expect("localhost");
        check_url_local("AUC_NIM_RERANK_URL", NIM_RERANK_URL).expect("localhost");
        check_url_local("AUC_NIM_EMBED_URL", "http://127.0.0.1:8001").expect("127.0.0.1");
        check_url_local("AUC_NIM_EMBED_URL", "http://[::1]:8001").expect("::1");
        check_url_local("AUC_NIM_EMBED_URL", "http://127.0.0.5:8001/v1").expect("loopback range");
    }

    #[test]
    fn nvidias_hosted_endpoint_is_refused() {
        let err = check_url_local(
            "AUC_NIM_EMBED_URL",
            "https://build.nvidia.com/v1/embeddings",
        )
        .expect_err("a hosted endpoint must be refused");
        let message = err.to_string();
        assert!(message.contains("build.nvidia.com"), "{message}");
        assert!(message.contains("Resident comments"), "{message}");
    }

    #[test]
    fn another_machine_on_the_network_is_refused() {
        assert!(check_url_local("AUC_NIM_RERANK_URL", "http://192.168.1.40:8002").is_err());
        assert!(check_url_local("AUC_NIM_EMBED_URL", "http://gpu-box.local:8001").is_err());
    }

    #[test]
    fn settings_are_checked_as_a_set_before_they_are_written() {
        let mut env = BTreeMap::new();
        env.insert("AUC_NIM_EMBED_URL".to_string(), NIM_EMBED_URL.to_string());
        env.insert("AUC_NIM_RERANK_URL".to_string(), NIM_RERANK_URL.to_string());
        env.insert("AUC_PORT".to_string(), "3000".to_string());
        check_env_local(&env).expect("the settings we generate are local");

        env.insert(
            "AUC_NIM_RERANK_URL".to_string(),
            "https://build.nvidia.com".to_string(),
        );
        assert!(check_env_local(&env).is_err());
    }

    #[test]
    fn host_names_are_pulled_out_of_urls_including_the_awkward_ones() {
        assert_eq!(
            host_of("http://localhost:8001").as_deref(),
            Some("localhost")
        );
        assert_eq!(
            host_of("http://[::1]:8001/v1/health").as_deref(),
            Some("::1")
        );
        assert_eq!(
            host_of("https://user:pass@example.org/path").as_deref(),
            Some("example.org")
        );
        assert_eq!(host_of("localhost:8001").as_deref(), Some("localhost"));
    }

    #[test]
    fn zero_zero_zero_zero_is_not_loopback_however_it_is_written() {
        assert!(!is_loopback_host("0.0.0.0"));
        assert!(!is_loopback_host("::"));
        assert!(is_loopback_host("127.0.0.1"));
        assert!(is_loopback_host("LOCALHOST"));
    }
}
