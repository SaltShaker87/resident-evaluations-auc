//! The `preflight` step: run the repository's own `preflight.sh` and turn its
//! output into something the screen can draw.
//!
//! The checks themselves are deliberately not reimplemented here. preflight.sh
//! is what the documentation tells people to run when something stops working,
//! so the installer runs the same script and shows the same answers — the two
//! can never disagree about what healthy looks like.

use std::path::Path;
use std::time::Duration;

use anyhow::Result;

use crate::engine::process::Cmd;
use crate::engine::types::{PreflightLevel, PreflightLine, PreflightReport, StepId};
use crate::engine::Ctx;

/// Read preflight.sh's output.
///
/// Its shape is fixed by the `pass`/`warn`/`fail`/`info` helpers at the top of
/// that script: two spaces, a mark, the text; and for a problem, an indented
/// `→` line saying what to do about it.
pub fn parse(output: &str) -> PreflightReport {
    let mut lines: Vec<PreflightLine> = Vec::new();
    for raw in output.lines() {
        let line = strip_ansi(raw);
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }

        // "→ do this" belongs to the check above it.
        if let Some(hint) = trimmed.strip_prefix('→') {
            if let Some(last) = lines.last_mut() {
                let hint = hint.trim();
                if !hint.is_empty() {
                    last.hint = Some(match last.hint.take() {
                        Some(existing) => format!("{existing} {hint}"),
                        None => hint.to_string(),
                    });
                }
            }
            continue;
        }

        let Some((level, text)) = level_of(trimmed) else {
            // Section headings, counts and the odd extra detail line. They
            // are in the log; they are not checks.
            continue;
        };
        if text.is_empty() {
            continue;
        }
        lines.push(PreflightLine {
            level,
            text: text.to_string(),
            hint: None,
        });
    }
    PreflightReport { lines }
}

fn level_of(text: &str) -> Option<(PreflightLevel, &str)> {
    for (mark, level) in [
        ("✓", PreflightLevel::Pass),
        ("⚠", PreflightLevel::Warn),
        ("✗", PreflightLevel::Fail),
        ("·", PreflightLevel::Info),
    ] {
        if let Some(rest) = text.strip_prefix(mark) {
            return Some((level, rest.trim()));
        }
    }
    None
}

/// preflight.sh colours its output when it thinks it is talking to a terminal.
/// We capture it through a pipe, so normally there is nothing to strip — but a
/// machine with CLICOLOR_FORCE set would otherwise fill the screen with
/// escape codes.
fn strip_ansi(line: &str) -> String {
    let mut out = String::with_capacity(line.len());
    let mut chars = line.chars().peekable();
    while let Some(ch) = chars.next() {
        if ch != '\u{1b}' {
            out.push(ch);
            continue;
        }
        if chars.peek() == Some(&'[') {
            chars.next();
            // A colour code ends at the first letter.
            for inner in chars.by_ref() {
                if inner.is_ascii_alphabetic() {
                    break;
                }
            }
        }
    }
    out
}

pub fn count(report: &PreflightReport, level: PreflightLevel) -> usize {
    report
        .lines
        .iter()
        .filter(|line| line.level == level)
        .count()
}

/// Run it, with `auc.env` loaded so the script sees what the service sees.
pub fn run(ctx: &mut Ctx, app: &Path) -> Result<()> {
    ctx.progress.start(StepId::Preflight);
    let script = app.join("preflight.sh");
    if !script.is_file() {
        ctx.progress.skipped(
            StepId::Preflight,
            "This copy of AUC has no preflight.sh to run",
        );
        return Ok(());
    }

    let env = crate::engine::env_for_scripts(&ctx.layout)?;
    let output = Cmd::new("bash")
        .arg(script.display().to_string())
        .cwd(app)
        .envs(env)
        .timeout(Duration::from_secs(10 * 60))
        .run(ctx.emitter.as_ref(), &ctx.cancel)?;

    let report = parse(&output.text());
    let failures = count(&report, PreflightLevel::Fail);
    let warnings = count(&report, PreflightLevel::Warn);
    let passes = count(&report, PreflightLevel::Pass);
    ctx.emitter.preflight(report);

    let summary = format!(
        "{passes} passed, {warnings} {}, {failures} {}",
        if warnings == 1 { "warning" } else { "warnings" },
        if failures == 1 { "failure" } else { "failures" }
    );
    if failures > 0 {
        // The install itself worked; these are things about the machine that
        // still need attention, and each line already says what to do.
        ctx.warn(format!(
            "The final check found {failures} {}. The list on the screen says what each one needs.",
            if failures == 1 { "problem" } else { "problems" }
        ));
        ctx.progress.warning(StepId::Preflight, summary);
    } else {
        ctx.progress.done_with(StepId::Preflight, summary);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A cut-down run of the real script, colours and all.
    const OUTPUT: &str = concat!(
        "\n",
        "  AUC preflight — 2026-09-12 23:40\n",
        "  /home/you/.local/share/auc/app/current\n",
        "\n",
        "Machine\n",
        "  \u{1b}[2m·\u{1b}[0m Processor family: aarch64\n",
        "  \u{1b}[32m✓\u{1b}[0m Disk space: 412 GB free\n",
        "  \u{1b}[33m⚠\u{1b}[0m Python 3.10.12 — works, but 3.11+ is the comfortable floor\n",
        "\n",
        "Application\n",
        "  \u{1b}[32m✓\u{1b}[0m Python environment present (3.12.7)\n",
        "  \u{1b}[31m✗\u{1b}[0m cannot import chromadb — summary generation will not work\n",
        "      \u{1b}[2m→ Install it: backend/venv/bin/pip install -r backend/requirements-rag.txt\u{1b}[0m\n",
        "      ModuleNotFoundError: No module named 'chromadb'\n",
        "\n",
        "Service\n",
        "  \u{1b}[31m✗\u{1b}[0m Lingering is NOT enabled\n",
        "      \u{1b}[2m→ sudo loginctl enable-linger you — without it the app does not start at boot.\u{1b}[0m\n",
        "\n",
        "  4 passed, 1 warning, 2 failures\n",
        "\n"
    );

    #[test]
    fn every_check_line_is_read_with_its_level() {
        let report = parse(OUTPUT);
        let levels: Vec<PreflightLevel> = report.lines.iter().map(|l| l.level).collect();
        assert_eq!(
            levels,
            vec![
                PreflightLevel::Info,
                PreflightLevel::Pass,
                PreflightLevel::Warn,
                PreflightLevel::Pass,
                PreflightLevel::Fail,
                PreflightLevel::Fail,
            ]
        );
        assert_eq!(report.lines[0].text, "Processor family: aarch64");
        assert_eq!(report.lines[1].text, "Disk space: 412 GB free");
    }

    #[test]
    fn the_arrow_line_becomes_the_hint_for_the_check_above_it() {
        let report = parse(OUTPUT);
        let chromadb = report
            .lines
            .iter()
            .find(|line| line.text.contains("chromadb"))
            .expect("the chromadb failure");
        assert_eq!(chromadb.level, PreflightLevel::Fail);
        assert_eq!(
            chromadb.hint.as_deref(),
            Some("Install it: backend/venv/bin/pip install -r backend/requirements-rag.txt")
        );

        let linger = report.lines.last().expect("the linger failure");
        assert!(linger
            .hint
            .as_deref()
            .unwrap_or_default()
            .contains("enable-linger"));
    }

    #[test]
    fn headings_counts_and_stray_detail_lines_are_not_mistaken_for_checks() {
        let report = parse(OUTPUT);
        assert_eq!(report.lines.len(), 6);
        assert!(!report.lines.iter().any(|l| l.text.contains("passed,")));
        assert!(!report
            .lines
            .iter()
            .any(|l| l.text.contains("ModuleNotFound")));
        assert!(!report.lines.iter().any(|l| l.text == "Machine"));
    }

    #[test]
    fn colour_codes_never_reach_the_screen() {
        let report = parse(OUTPUT);
        for line in &report.lines {
            assert!(
                !line.text.contains('\u{1b}'),
                "escape code in {:?}",
                line.text
            );
            assert!(
                !line.text.starts_with('['),
                "escape code in {:?}",
                line.text
            );
        }
    }

    #[test]
    fn the_counts_are_what_the_step_summary_reports() {
        let report = parse(OUTPUT);
        assert_eq!(count(&report, PreflightLevel::Pass), 2);
        assert_eq!(count(&report, PreflightLevel::Warn), 1);
        assert_eq!(count(&report, PreflightLevel::Fail), 2);
        assert_eq!(count(&report, PreflightLevel::Info), 1);
    }

    #[test]
    fn a_hint_with_nothing_above_it_is_dropped_rather_than_crashing() {
        let report = parse("      → do this first\n  ✓ then this\n");
        assert_eq!(report.lines.len(), 1);
        assert_eq!(report.lines[0].hint, None);
    }

    #[test]
    fn output_with_no_checks_in_it_is_an_empty_report() {
        assert!(parse("").lines.is_empty());
        assert!(parse("Machine\n\n  4 passed, 0 warnings, 0 failures\n")
            .lines
            .is_empty());
    }
}
