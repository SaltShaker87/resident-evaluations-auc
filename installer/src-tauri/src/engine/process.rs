//! Running other people's programs.
//!
//! Every external command in the installer goes through here, so three
//! promises hold everywhere: the command line is written to the log (with any
//! credential blanked out), its output is streamed to the screen as it appears
//! rather than at the end, and Cancel stops it.

use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use anyhow::{anyhow, Context, Result};

use crate::engine::cancel::{Cancel, Cancelled};
use crate::engine::emitter::Emitter;

// ---------------------------------------------------------------------------
// The environment children are given
// ---------------------------------------------------------------------------

/// Variables an AppImage's launcher (linuxdeploy's AppRun) exports whether or
/// not the bundled program has any use for them. They point into the
/// AppImage's temporary mount, which holds no Python, no Perl and only the
/// installer's own libraries — so a child `python3` dies on startup with
/// "No module named 'encodings'", and other programs can pick up the wrong
/// shared libraries. None of them is anything the installer wants a child to
/// inherit on any machine, AppImage or not.
pub const LEAKED_VARS: [&str; 19] = [
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PERLLIB",
    "PERL5LIB",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "GSETTINGS_SCHEMA_DIR",
    "QT_PLUGIN_PATH",
    "GST_PLUGIN_SYSTEM_PATH",
    "GST_PLUGIN_SYSTEM_PATH_1_0",
    "GIO_MODULE_DIR",
    "GDK_PIXBUF_MODULE_FILE",
    "GTK_IM_MODULE_FILE",
    "GTK_DATA_PREFIX",
    "GTK_EXE_PREFIX",
    "GTK_PATH",
    "GTK_THEME",
    "GDK_BACKEND",
];

/// Path-like variables the launcher prepends its mount to. Those entries are
/// dropped; the rest of the value is kept.
const PREFIXED_VARS: [&str; 2] = ["PATH", "XDG_DATA_DIRS"];

/// Give a child the environment this machine has, not the AppImage's.
pub fn scrub_env(command: &mut Command) {
    for var in LEAKED_VARS {
        command.env_remove(var);
    }
    if let Some(appdir) = appimage_dir() {
        for var in PREFIXED_VARS {
            if let Some(value) = std::env::var_os(var) {
                let cleaned = without_prefix(&value.to_string_lossy(), &appdir);
                command.env(var, cleaned);
            }
        }
    }
}

/// The AppImage's mount point, when this process is running from one.
fn appimage_dir() -> Option<String> {
    std::env::var("APPDIR")
        .ok()
        .map(|d| d.trim_end_matches('/').to_string())
        .filter(|d| !d.is_empty())
}

/// A colon-separated list with every entry under `prefix` removed.
pub fn without_prefix(value: &str, prefix: &str) -> String {
    value
        .split(':')
        .filter(|entry| !entry.is_empty())
        .filter(|entry| entry != &prefix && !entry.starts_with(&format!("{prefix}/")))
        .collect::<Vec<_>>()
        .join(":")
}

/// Which of the leaked variables are set right now — for one line in the log,
/// so that a report from an AppImage shows what was taken away from children.
pub fn leaked_vars_present() -> Vec<String> {
    let mut found: Vec<String> = LEAKED_VARS
        .iter()
        .filter(|var| std::env::var_os(var).is_some())
        .map(|var| (*var).to_string())
        .collect();
    if appimage_dir().is_some() {
        found.push("APPDIR entries in PATH and XDG_DATA_DIRS".to_string());
    }
    found
}

/// What a finished command left behind.
#[derive(Debug, Clone, Default)]
pub struct CmdOutput {
    pub code: Option<i32>,
    pub success: bool,
    /// stdout and stderr interleaved, one entry per line, in the order they
    /// arrived — which is what you want when reading an error afterwards.
    pub lines: Vec<String>,
}

impl CmdOutput {
    pub fn text(&self) -> String {
        self.lines.join("\n")
    }

    /// The last few lines, for a message that has to fit on a screen.
    pub fn tail(&self, count: usize) -> String {
        let start = self.lines.len().saturating_sub(count);
        self.lines[start..].join("\n")
    }
}

/// A command about to be run.
#[derive(Debug, Clone)]
pub struct Cmd {
    program: String,
    args: Vec<String>,
    env: Vec<(String, String)>,
    cwd: Option<PathBuf>,
    stdin: Option<String>,
    timeout: Option<Duration>,
    redact: Vec<String>,
    quiet: bool,
}

impl Cmd {
    pub fn new(program: impl Into<String>) -> Self {
        Cmd {
            program: program.into(),
            args: Vec::new(),
            env: Vec::new(),
            cwd: None,
            stdin: None,
            timeout: None,
            redact: Vec::new(),
            quiet: false,
        }
    }

    pub fn arg(mut self, arg: impl Into<String>) -> Self {
        self.args.push(arg.into());
        self
    }

    pub fn args<I, S>(mut self, args: I) -> Self
    where
        I: IntoIterator<Item = S>,
        S: Into<String>,
    {
        self.args.extend(args.into_iter().map(Into::into));
        self
    }

    pub fn env(mut self, key: impl Into<String>, value: impl Into<String>) -> Self {
        self.env.push((key.into(), value.into()));
        self
    }

    pub fn envs<I, K, V>(mut self, vars: I) -> Self
    where
        I: IntoIterator<Item = (K, V)>,
        K: Into<String>,
        V: Into<String>,
    {
        self.env
            .extend(vars.into_iter().map(|(k, v)| (k.into(), v.into())));
        self
    }

    pub fn cwd(mut self, dir: impl Into<PathBuf>) -> Self {
        self.cwd = Some(dir.into());
        self
    }

    /// Text handed to the command on stdin. Used for credentials — it never
    /// appears in the log, and never in the process list either.
    pub fn stdin_secret(mut self, text: impl Into<String>) -> Self {
        self.stdin = Some(text.into());
        self
    }

    pub fn timeout(mut self, timeout: Duration) -> Self {
        self.timeout = Some(timeout);
        self
    }

    /// Blank this string out wherever it appears in the logged command line.
    pub fn redact(mut self, secret: impl Into<String>) -> Self {
        let secret = secret.into();
        if !secret.is_empty() {
            self.redact.push(secret);
        }
        self
    }

    /// Do not stream this command's chatter to the screen. Its output is
    /// still captured and still goes to the log file.
    pub fn quiet(mut self) -> Self {
        self.quiet = true;
        self
    }

    /// The command line as it is safe to write down.
    pub fn display(&self) -> String {
        let mut parts = vec![self.program.clone()];
        parts.extend(self.args.iter().cloned());
        let line = parts
            .iter()
            .map(|part| {
                if part.contains(' ') {
                    format!("'{part}'")
                } else {
                    part.clone()
                }
            })
            .collect::<Vec<_>>()
            .join(" ");
        redact_all(&line, &self.redact)
    }

    /// Run it, streaming as it goes. Returns even when the command failed —
    /// the caller decides whether a non-zero exit matters.
    pub fn run(&self, emitter: &dyn Emitter, cancel: &Cancel) -> Result<CmdOutput> {
        cancel.check()?;
        emitter.log(&format!("$ {}", self.display()));

        let mut command = Command::new(&self.program);
        command
            .args(&self.args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .stdin(if self.stdin.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            });
        // First, so that anything a step sets on purpose wins.
        scrub_env(&mut command);
        for (key, value) in &self.env {
            command.env(key, value);
        }
        if let Some(dir) = &self.cwd {
            command.current_dir(dir);
        }

        let mut child = command.spawn().map_err(|e| {
            anyhow!(
                "Could not start {}: {e}. It may not be installed on this machine.",
                self.program
            )
        })?;

        if let Some(text) = &self.stdin {
            if let Some(mut pipe) = child.stdin.take() {
                let text = text.clone();
                // Written from its own thread: a command that reads stdin only
                // after printing something would otherwise deadlock with us.
                std::thread::spawn(move || {
                    let _ = pipe.write_all(text.as_bytes());
                });
            }
        }

        let (tx, rx) = mpsc::channel::<String>();
        if let Some(stdout) = child.stdout.take() {
            spawn_reader(stdout, tx.clone());
        }
        if let Some(stderr) = child.stderr.take() {
            spawn_reader(stderr, tx.clone());
        }
        drop(tx);

        let started = Instant::now();
        let mut lines: Vec<String> = Vec::new();
        loop {
            match rx.recv_timeout(Duration::from_millis(200)) {
                Ok(line) => {
                    if !self.quiet {
                        emitter.log(&line);
                    }
                    lines.push(line);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                // Both readers have finished, so the command has closed its
                // output; all that is left is to collect its exit code.
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }

            if cancel.is_cancelled() {
                stop(&mut child);
                return Err(Cancelled.into());
            }
            if let Some(limit) = self.timeout {
                if started.elapsed() > limit {
                    stop(&mut child);
                    return Err(anyhow!(
                        "{} was still running after {} minutes, so it was stopped. The log above shows how far it got.",
                        self.program,
                        limit.as_secs() / 60
                    ));
                }
            }
        }

        let status = child
            .wait()
            .with_context(|| format!("Lost track of {} while it was running.", self.program))?;
        let output = CmdOutput {
            code: status.code(),
            success: status.success(),
            lines,
        };
        if !self.quiet && !output.success {
            emitter.log(&format!(
                "  {} exited with status {}",
                self.program,
                output
                    .code
                    .map(|c| c.to_string())
                    .unwrap_or_else(|| "unknown".into())
            ));
        }
        Ok(output)
    }

    /// Run it and treat a non-zero exit as a failure, with `what` as the
    /// plain-language description of what was being attempted.
    pub fn run_ok(&self, emitter: &dyn Emitter, cancel: &Cancel, what: &str) -> Result<CmdOutput> {
        let output = self.run(emitter, cancel)?;
        if !output.success {
            let tail = output.tail(8);
            let detail = if tail.trim().is_empty() {
                String::new()
            } else {
                format!(" It said:\n{tail}")
            };
            return Err(anyhow!("{what} did not work.{detail}"));
        }
        Ok(output)
    }
}

fn spawn_reader<R: std::io::Read + Send + 'static>(source: R, tx: mpsc::Sender<String>) {
    std::thread::spawn(move || {
        let reader = BufReader::new(source);
        // Read bytes rather than str: pip and docker occasionally emit a
        // progress byte that is not valid UTF-8, and losing the whole rest of
        // the output over one byte would be absurd.
        for line in reader.split(b'\n') {
            let Ok(bytes) = line else { break };
            let text = String::from_utf8_lossy(&bytes)
                .trim_end_matches('\r')
                .to_string();
            if tx.send(text).is_err() {
                break;
            }
        }
    });
}

/// Ask it to stop, then make it stop. Nothing useful can be done about a
/// failure here: we are already on our way out.
fn stop(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

fn redact_all(text: &str, secrets: &[String]) -> String {
    let mut out = text.to_string();
    for secret in secrets {
        out = out.replace(secret, "******");
    }
    out
}

// ---------------------------------------------------------------------------
// Small, quiet questions
// ---------------------------------------------------------------------------

/// Run something short and give back its stdout, or `None` if it could not be
/// run at all. For working out what is on the machine, where a missing
/// program is an answer rather than an error.
pub fn capture(program: &str, args: &[&str]) -> Option<String> {
    let mut command = Command::new(program);
    command.args(args).stdin(Stdio::null());
    scrub_env(&mut command);
    let output = command.output().ok()?;
    if !output.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&output.stdout).to_string())
}

/// Like `capture`, but the exit code is the answer.
pub fn succeeds(program: &str, args: &[&str]) -> bool {
    let mut command = Command::new(program);
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());
    scrub_env(&mut command);
    command.status().map(|s| s.success()).unwrap_or(false)
}

/// Is this program on the PATH?
pub fn have(program: &str) -> bool {
    which::which(program).is_ok()
}

/// Make a file executable. Downloaded tools arrive without the bit set.
#[cfg(unix)]
pub fn make_executable(path: &Path) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut perms = std::fs::metadata(path)
        .with_context(|| format!("Could not look at {}.", path.display()))?
        .permissions();
    perms.set_mode(0o755);
    std::fs::set_permissions(path, perms)
        .with_context(|| format!("Could not make {} runnable.", path.display()))
}

#[cfg(not(unix))]
pub fn make_executable(_path: &Path) -> Result<()> {
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_credential_never_reaches_the_log() {
        let cmd = Cmd::new("docker")
            .args(["login", "nvcr.io", "--password", "nvapi-secret"])
            .redact("nvapi-secret");
        assert_eq!(cmd.display(), "docker login nvcr.io --password ******");
    }

    #[test]
    fn the_appimage_mount_is_taken_out_of_a_path_list_and_nothing_else_is() {
        let appdir = "/tmp/.mount_AUCxyz";
        assert_eq!(
            without_prefix(
                "/tmp/.mount_AUCxyz/usr/bin:/usr/local/bin:/tmp/.mount_AUCxyz:/usr/bin",
                appdir
            ),
            "/usr/local/bin:/usr/bin"
        );
        assert_eq!(
            without_prefix("/usr/local/bin:/usr/bin", appdir),
            "/usr/local/bin:/usr/bin",
            "a machine that is not running an AppImage is left alone"
        );
        assert_eq!(
            without_prefix("/tmp/.mount_AUCxyz-other/bin:/usr/bin", appdir),
            "/tmp/.mount_AUCxyz-other/bin:/usr/bin",
            "only entries under the mount go, not ones that merely start with the same letters"
        );
    }

    #[test]
    fn a_child_never_inherits_the_appimages_python_settings() {
        // std::process::Command records env_remove as an explicit None, which
        // is what a child sees as "not set" even when this process has it.
        let mut command = Command::new("true");
        scrub_env(&mut command);
        let removed: Vec<String> = command
            .get_envs()
            .filter(|(_, value)| value.is_none())
            .map(|(key, _)| key.to_string_lossy().to_string())
            .collect();
        for var in [
            "PYTHONHOME",
            "PYTHONPATH",
            "LD_LIBRARY_PATH",
            "GDK_PIXBUF_MODULE_FILE",
        ] {
            assert!(removed.iter().any(|k| k == var), "{var} should be removed");
        }
    }

    #[test]
    fn a_setting_a_step_asks_for_wins_over_the_scrub() {
        // NIM_UID and friends are set by steps after the scrub, so a step can
        // still hand a child exactly what it needs.
        let cmd = Cmd::new("true").env("PYTHONPATH", "/somewhere/on/purpose");
        assert_eq!(
            cmd.env,
            vec![(
                "PYTHONPATH".to_string(),
                "/somewhere/on/purpose".to_string()
            )]
        );
    }

    #[test]
    fn arguments_with_spaces_are_quoted_so_the_log_can_be_pasted_back() {
        let cmd = Cmd::new("docker").args(["login", "--username", "$oauthtoken"]);
        assert_eq!(cmd.display(), "docker login --username $oauthtoken");
        let cmd = Cmd::new("bash").args(["-c", "echo hello world"]);
        assert_eq!(cmd.display(), "bash -c 'echo hello world'");
    }
}
