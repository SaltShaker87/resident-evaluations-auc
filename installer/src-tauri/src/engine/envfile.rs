//! `auc.env` — the one file that configures a running AUC.
//!
//! It is a systemd `EnvironmentFile`: plain `KEY=VALUE` lines, no `export`.
//! The rule that matters is the same one `setup.sh`'s `install_unit` follows:
//! **a value the user edited by hand wins over the one we would generate**, and
//! their comments survive. Re-running the installer must never quietly undo a
//! deliberate change.

use std::collections::BTreeMap;
use std::path::Path;

use anyhow::{Context, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
enum Line {
    /// A comment, a blank line, or anything we did not recognise. Kept
    /// verbatim and in place.
    Other(String),
    Entry {
        key: String,
        value: String,
    },
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct EnvFile {
    lines: Vec<Line>,
}

impl EnvFile {
    pub fn empty() -> Self {
        EnvFile::default()
    }

    pub fn parse(text: &str) -> Self {
        let lines = text
            .lines()
            .map(|line| match split_entry(line) {
                Some((key, value)) => Line::Entry { key, value },
                None => Line::Other(line.to_string()),
            })
            .collect();
        EnvFile { lines }
    }

    /// Read the file, or start from nothing if it is not there yet.
    pub fn load(path: &Path) -> Result<Self> {
        match std::fs::read_to_string(path) {
            Ok(text) => Ok(EnvFile::parse(&text)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(EnvFile::empty()),
            Err(e) => Err(e).with_context(|| {
                format!(
                    "Could not read the settings file {}. Check that you can read it.",
                    path.display()
                )
            }),
        }
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            crate::engine::layout::ensure_dir(parent)?;
        }
        std::fs::write(path, self.render()).with_context(|| {
            format!(
                "Could not write the settings file {}. Check that you can write there.",
                path.display()
            )
        })
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.lines.iter().rev().find_map(|line| match line {
            Line::Entry { key: k, value } if k == key => Some(unquote(value)),
            _ => None,
        })
    }

    pub fn contains(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// Set it, wherever it already is; add it at the end if it is new.
    pub fn set(&mut self, key: &str, value: impl Into<String>) {
        let value = value.into();
        let mut replaced = false;
        for line in self.lines.iter_mut() {
            if let Line::Entry { key: k, value: v } = line {
                if k == key {
                    *v = value.clone();
                    replaced = true;
                }
            }
        }
        if !replaced {
            self.lines.push(Line::Entry {
                key: key.to_string(),
                value,
            });
        }
    }

    /// Take the key out altogether, comments and all other lines untouched.
    pub fn remove(&mut self, key: &str) {
        self.lines
            .retain(|line| !matches!(line, Line::Entry { key: k, .. } if k == key));
    }

    /// Merge in what we would generate, letting the user's value win.
    ///
    /// New keys are appended, so an upgrade that adds a setting picks up the
    /// new default without touching anything the user chose.
    pub fn merge_generated<I, K, V>(&mut self, generated: I)
    where
        I: IntoIterator<Item = (K, V)>,
        K: AsRef<str>,
        V: Into<String>,
    {
        for (key, value) in generated {
            let key = key.as_ref();
            if !self.contains(key) {
                self.set(key, value);
            }
        }
    }

    /// Every setting, ready to hand to a child process.
    pub fn map(&self) -> BTreeMap<String, String> {
        let mut out = BTreeMap::new();
        for line in &self.lines {
            if let Line::Entry { key, value } = line {
                out.insert(key.clone(), unquote(value).to_string());
            }
        }
        out
    }

    pub fn render(&self) -> String {
        let mut out = String::new();
        for line in &self.lines {
            match line {
                Line::Other(text) => out.push_str(text),
                Line::Entry { key, value } => {
                    out.push_str(key);
                    out.push('=');
                    out.push_str(value);
                }
            }
            out.push('\n');
        }
        out
    }
}

/// `KEY=value`, systemd style. A leading `export` is not part of that syntax,
/// so a line with one is left alone rather than half-understood.
fn split_entry(line: &str) -> Option<(String, String)> {
    let trimmed = line.trim();
    if trimmed.is_empty() || trimmed.starts_with('#') {
        return None;
    }
    // Indentation is not legal in an EnvironmentFile, and treating an indented
    // line as a setting would silently change meaning.
    if line.starts_with(char::is_whitespace) {
        return None;
    }
    let (key, value) = trimmed.split_once('=')?;
    if key.is_empty() || !key.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
        return None;
    }
    if key.chars().next().is_some_and(|c| c.is_ascii_digit()) {
        return None;
    }
    Some((key.to_string(), value.to_string()))
}

fn unquote(value: &str) -> &str {
    let trimmed = value.trim();
    for quote in ['"', '\''] {
        if trimmed.len() >= 2 && trimmed.starts_with(quote) && trimmed.ends_with(quote) {
            return &trimmed[1..trimmed.len() - 1];
        }
    }
    trimmed
}

#[cfg(test)]
mod tests {
    use super::*;

    const EXISTING: &str = "\
# my notes, please keep them
AUC_PORT=3100
AUC_HOST=127.0.0.1

# a setting the installer knows nothing about
MEDHUB_API_URL=https://example.org/api
";

    fn generated() -> Vec<(&'static str, String)> {
        vec![
            ("AUC_PORT", "3000".to_string()),
            ("AUC_HOST", "0.0.0.0".to_string()),
            ("OLLAMA_MODEL", "qwen3.5:9b".to_string()),
        ]
    }

    #[test]
    fn a_hand_edited_value_wins_over_ours() {
        let mut env = EnvFile::parse(EXISTING);
        env.merge_generated(generated());
        assert_eq!(env.get("AUC_PORT"), Some("3100"));
        assert_eq!(env.get("AUC_HOST"), Some("127.0.0.1"));
    }

    #[test]
    fn a_setting_that_is_new_this_version_is_added_at_the_end() {
        let mut env = EnvFile::parse(EXISTING);
        env.merge_generated(generated());
        assert_eq!(env.get("OLLAMA_MODEL"), Some("qwen3.5:9b"));
        assert!(env.render().trim_end().ends_with("OLLAMA_MODEL=qwen3.5:9b"));
    }

    #[test]
    fn comments_blank_lines_and_unknown_settings_survive() {
        let mut env = EnvFile::parse(EXISTING);
        env.merge_generated(generated());
        let rendered = env.render();
        assert!(rendered.contains("# my notes, please keep them"));
        assert!(rendered.contains("# a setting the installer knows nothing about"));
        assert!(rendered.contains("MEDHUB_API_URL=https://example.org/api"));
        assert!(
            rendered.contains("\n\n"),
            "the blank line should still be there"
        );
    }

    #[test]
    fn the_ollama_fallback_can_be_added_and_taken_away_again() {
        let mut env = EnvFile::parse(EXISTING);
        env.set("AUC_RETRIEVAL_ENGINE_DEFAULT", "ollama");
        assert_eq!(env.get("AUC_RETRIEVAL_ENGINE_DEFAULT"), Some("ollama"));
        assert!(env.render().contains("AUC_RETRIEVAL_ENGINE_DEFAULT=ollama"));

        env.remove("AUC_RETRIEVAL_ENGINE_DEFAULT");
        assert_eq!(env.get("AUC_RETRIEVAL_ENGINE_DEFAULT"), None);
        assert!(!env.render().contains("AUC_RETRIEVAL_ENGINE_DEFAULT"));
        // and nothing else went with it
        assert_eq!(env.get("AUC_PORT"), Some("3100"));
        assert!(env.render().contains("# my notes, please keep them"));
    }

    #[test]
    fn setting_an_existing_key_edits_it_in_place() {
        let mut env = EnvFile::parse("AUC_PORT=3000\n# tail comment\n");
        env.set("AUC_PORT", "4000");
        assert_eq!(env.render(), "AUC_PORT=4000\n# tail comment\n");
    }

    #[test]
    fn quoted_values_are_read_without_their_quotes() {
        let env = EnvFile::parse("AUC_BACKUP_DIR=\"/mnt/One Drive/auc\"\n");
        assert_eq!(env.get("AUC_BACKUP_DIR"), Some("/mnt/One Drive/auc"));
        // but the file is not rewritten just for having been read
        assert_eq!(env.render(), "AUC_BACKUP_DIR=\"/mnt/One Drive/auc\"\n");
    }

    #[test]
    fn the_whole_file_comes_back_as_a_map_for_child_processes() {
        let env = EnvFile::parse(EXISTING);
        let map = env.map();
        assert_eq!(map.get("AUC_PORT").map(String::as_str), Some("3100"));
        assert_eq!(map.len(), 3);
    }
}
