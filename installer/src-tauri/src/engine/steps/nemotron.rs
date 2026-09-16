//! The `docker` and `nemotron` steps: the NVIDIA Nemotron retrieval engine.
//!
//! This is `start-nemotron.sh` done in Rust, with the same decisions: pull the
//! images, bring the two containers up, wait until both answer
//! `/v1/health/ready`, and give up early if a container has died rather than
//! waiting half an hour for something that is not coming.
//!
//! Two rules are absolute here. The containers are reachable from this machine
//! only, because resident comments are sent to them (see `guard.rs`). And the
//! NGC API key is a credential: it goes in on standard input and in the
//! process environment, never in an argument, never in a file, never in the
//! log.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use anyhow::{anyhow, Result};

use crate::engine::layout::ensure_dir;
use crate::engine::process::{have, Cmd};
use crate::engine::types::{StepId, SystemInfo};
use crate::engine::{detect, guard, net, Ctx, Privilege};
use crate::platform::Platform;

/// How long the first start is allowed to take. The model weights are several
/// gigabytes, and on a slow connection that is genuinely half an hour.
const READY_TIMEOUT: Duration = Duration::from_secs(30 * 60);

/// How often to ask Docker whether a container has died while we wait.
const CRASH_CHECK: Duration = Duration::from_secs(10);

/// The two containers, and the folders their weights are cached in.
const SERVICES: [&str; 2] = ["nemotron-embed", "nemotron-rerank"];

/// Must match `auc/nim/docker-compose.yml`. Used to notice a failed pull that
/// can still start from images already on the machine. The embedder is 2.2.2
/// because nvcr.io has no 2.3 tag for it; the compose file says why.
const IMAGES: [&str; 2] = [
    "nvcr.io/nim/nvidia/nemotron-3-embed-1b:2.2.2",
    "nvcr.io/nim/nvidia/llama-nemotron-rerank-vl-1b-v2:2.3",
];

/// What to tell someone when NVIDIA's registry will not hand over the images.
const NGC_REGISTRY_HINT: &str = "The images come from NVIDIA's registry. Generate a Personal Key \
     with NGC Catalog ticked (ngc.nvidia.com, then Setup and Generate API Key), and open the two \
     Nemotron container pages once to accept the terms if NVIDIA asks.";

// ---------------------------------------------------------------------------
// The `docker` step
// ---------------------------------------------------------------------------

/// What is missing before the containers can run at all.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct DockerNeeds {
    pub install_docker: bool,
    pub install_compose: bool,
    pub install_toolkit: bool,
    pub add_group: bool,
}

impl DockerNeeds {
    pub fn anything(&self) -> bool {
        self.install_docker || self.install_compose || self.install_toolkit || self.add_group
    }
}

pub fn needs_from(system: &SystemInfo, user_in_docker_group: bool) -> DockerNeeds {
    let docker = system.tools.docker;
    DockerNeeds {
        install_docker: !docker.present,
        install_compose: docker.present && !docker.compose,
        // If Docker is installed but this session cannot talk to it yet, we
        // cannot honestly ask whether the NVIDIA runtime is there — and must
        // not try to reinstall the toolkit on every Finish Nemotron.
        install_toolkit: !docker.nvidia_runtime && (docker.usable_by_user || !docker.present),
        add_group: !user_in_docker_group,
    }
}

/// Everything that needs the administrator password, as one script, so the
/// user is asked once for the whole phase rather than four times.
pub fn docker_setup_script(needs: &DockerNeeds, user: &str) -> String {
    let mut script = String::from(
        "#!/bin/bash\n\
         # AUC installer — preparing this machine for the NVIDIA Nemotron containers.\n\
         set -euo pipefail\n\
         export DEBIAN_FRONTEND=noninteractive\n\n",
    );

    if needs.install_docker || needs.install_compose {
        script.push_str(
            "# Docker from the distribution's own packages rather than Docker's\n\
             # repository: on Ubuntu 24.04 docker.io and docker-compose-v2 are both\n\
             # there, which is one fewer apt source to go wrong. Older Ubuntus call\n\
             # the compose plugin docker-compose-plugin, hence the fallback.\n\
             apt-get update\n\
             apt-get install -y docker.io docker-compose-v2 \\\n\
             \x20   || apt-get install -y docker.io docker-compose-plugin\n\
             systemctl enable --now docker\n\n",
        );
    }

    if needs.install_toolkit {
        script.push_str(
            "# The NVIDIA Container Toolkit is what lets a container reach the GPU.\n\
             # These are NVIDIA's own documented steps for a Debian or Ubuntu machine.\n\
             # A DGX Spark has it already, so this section is usually skipped.\n\
             install -d -m 0755 /usr/share/keyrings\n\
             curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \\\n\
             \x20   | gpg --yes --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg\n\
             curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \\\n\
             \x20   | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \\\n\
             \x20   > /etc/apt/sources.list.d/nvidia-container-toolkit.list\n\
             apt-get update\n\
             apt-get install -y nvidia-container-toolkit\n\
             nvidia-ctk runtime configure --runtime=docker\n\
             systemctl restart docker\n\n",
        );
    }

    if needs.add_group {
        script.push_str(&format!(
            "# So that Docker can be used without a password from the next login\n\
             # onwards. Until then the installer starts the containers with `sg docker`\n\
             # rather than asking for the administrator password a second time.\n\
             usermod -aG docker {}\n\n",
            shell_quote(user)
        ));
    }

    script.push_str("echo 'AUC: this machine is ready to run the Nemotron containers.'\n");
    script
}

/// The `docker` step.
pub fn setup_docker(
    platform: &dyn Platform,
    ctx: &mut Ctx,
    system: &SystemInfo,
    enabled: bool,
) -> Result<()> {
    ctx.progress.start(StepId::Docker);
    if !enabled {
        ctx.progress
            .skipped(StepId::Docker, "The NVIDIA Nemotron engine was not chosen");
        return Ok(());
    }
    platform.install_docker_stack(ctx, system)?;
    ctx.progress.done(StepId::Docker);
    Ok(())
}

// ---------------------------------------------------------------------------
// The `nemotron` step
// ---------------------------------------------------------------------------

/// Where the containers are asked whether they are ready.
#[derive(Debug, Clone)]
pub struct Endpoints {
    pub embed_url: String,
    pub rerank_url: String,
}

impl Default for Endpoints {
    fn default() -> Self {
        Endpoints {
            embed_url: guard::NIM_EMBED_URL.to_string(),
            rerank_url: guard::NIM_RERANK_URL.to_string(),
        }
    }
}

/// Spaces and a stray newline from a paste must not become part of the key.
pub fn trimmed_ngc_key(key: Option<&str>) -> Option<&str> {
    key.map(str::trim).filter(|s| !s.is_empty())
}

/// One line for the step row: the real reason, not the Standard-engine fallback.
pub fn short_nemotron_detail(err: &anyhow::Error) -> String {
    let text = format!("{err:#}").to_lowercase();
    if looks_like_registry_denial(&text) {
        "NVIDIA refused the key — tick NGC Catalog and accept the model pages once".to_string()
    } else if text.contains("signing in") {
        "Could not sign in to NVIDIA's registry with that key".to_string()
    } else if text.contains("downloading the nemotron") || text.contains("could not download") {
        "Could not download the Nemotron images".to_string()
    } else if text.contains("starting the nemotron") {
        "The Nemotron containers did not start".to_string()
    } else if text.contains("stopped instead of starting") {
        "A Nemotron container stopped instead of starting".to_string()
    } else if text.contains("still not ready after") {
        "The containers were not ready after 30 minutes".to_string()
    } else {
        let first = err.to_string();
        let line = first.lines().next().unwrap_or(&first).trim();
        let shortened: String = line.chars().take(120).collect();
        if shortened.is_empty() {
            "Not running yet — see Details".to_string()
        } else if shortened.chars().count() < line.chars().count() {
            format!("{shortened}…")
        } else {
            shortened
        }
    }
}

fn looks_like_registry_denial(text: &str) -> bool {
    text.contains("unauthorized")
        || text.contains("access denied")
        || text.contains("authentication required")
        || text.contains("denied:")
        || text.contains("401")
}

fn with_registry_hint(err: anyhow::Error) -> anyhow::Error {
    err.context(NGC_REGISTRY_HINT)
}

/// Start the containers; if anything goes wrong, say so, write AUC's settings
/// back to the Standard engine and carry on.
///
/// This is the one step whose failure is not the end of the install: the app
/// works on Ollama, and "finish Nemotron later" exists precisely for this.
pub fn start_or_defer(
    ctx: &mut Ctx,
    app: &Path,
    ngc_key: Option<&str>,
    enabled: bool,
) -> Result<()> {
    ctx.progress.start(StepId::Nemotron);
    if !enabled {
        ctx.progress.skipped(
            StepId::Nemotron,
            "The NVIDIA Nemotron engine was not chosen",
        );
        return Ok(());
    }

    let ngc_key = trimmed_ngc_key(ngc_key);
    match start(ctx, app, ngc_key) {
        Ok(()) => {
            ctx.nemotron_pending = false;
            // If an earlier attempt pinned AUC to the Standard engine, that
            // line has done its job and would now hold AUC back.
            super::configure::set_engine_default(ctx, None)?;
            ctx.progress
                .done_with(StepId::Nemotron, "Both containers are answering");
            Ok(())
        }
        Err(err) if crate::engine::cancel::is_cancelled_error(&err) => Err(err),
        Err(err) => {
            ctx.nemotron_pending = true;
            ctx.log(format!("{err:#}"));
            let detail = short_nemotron_detail(&err);
            // Until they answer, AUC must use the Standard engine or every
            // summary fails. This line is removed again by finish_nemotron.
            super::configure::set_engine_default(ctx, Some("ollama"))?;
            ctx.warn(format!(
                "{detail}. AUC will write summaries with the Standard engine for now. Everything \
                 else is installed. You can try Nemotron again from the installer, or with: bash \
                 app/current/start-nemotron.sh"
            ));
            ctx.progress.warning(StepId::Nemotron, detail);
            Ok(())
        }
    }
}

/// Both Nemotron steps, with the same forgiveness applied to each: prepare
/// Docker, then start the containers, and if either goes wrong say so, fall
/// back to the Standard engine and carry on with the install.
pub fn docker_and_start(
    platform: &dyn Platform,
    ctx: &mut Ctx,
    app: &Path,
    system: &SystemInfo,
    ngc_key: Option<&str>,
    enabled: bool,
) -> Result<()> {
    let ngc_key = trimmed_ngc_key(ngc_key);
    if !enabled {
        setup_docker(platform, ctx, system, false)?;
        return start_or_defer(ctx, app, ngc_key, false);
    }

    match setup_docker(platform, ctx, system, true) {
        Ok(()) => start_or_defer(ctx, app, ngc_key, true),
        Err(err) if crate::engine::cancel::is_cancelled_error(&err) => Err(err),
        Err(err) => {
            ctx.nemotron_pending = true;
            ctx.log(format!("{err:#}"));
            let detail = short_nemotron_detail(&err);
            super::configure::set_engine_default(ctx, Some("ollama"))?;
            ctx.warn(format!(
                "Docker could not be prepared for the NVIDIA Nemotron containers ({detail}), so \
                 AUC will write summaries with the Standard engine for now. Everything else is \
                 installed."
            ));
            ctx.progress.failed(StepId::Docker, detail);
            ctx.progress.start(StepId::Nemotron);
            ctx.progress
                .skipped(StepId::Nemotron, "Docker is not ready on this machine");
            Ok(())
        }
    }
}

/// The sequence itself, with no forgiveness: login, pull, up, wait.
pub fn start(ctx: &mut Ctx, app: &Path, ngc_key: Option<&str>) -> Result<()> {
    let compose_file = app.join("nim/docker-compose.yml");
    if !compose_file.is_file() {
        return Err(anyhow!(
            "This copy of AUC has no {}, so the Nemotron containers cannot be started.",
            compose_file.display()
        ));
    }

    // Before anything is started: the containers must answer this machine only.
    guard::check_compose_file(&compose_file)?;
    let endpoints = Endpoints::default();
    guard::check_url_local("AUC_NIM_EMBED_URL", &endpoints.embed_url)?;
    guard::check_url_local("AUC_NIM_RERANK_URL", &endpoints.rerank_url)?;
    ctx.log("Checked: the Nemotron containers will answer this machine only.");

    let cache_dir = ctx.layout.nim_cache_dir();
    for service in SERVICES {
        ensure_dir(&cache_dir.join(service).join("cache"))?;
        ensure_dir(&cache_dir.join(service).join("weights"))?;
    }

    let runner = ComposeRunner::for_this_machine(&compose_file, &cache_dir);
    match runner.access {
        DockerAccess::Sg => ctx.log(
            "This login session is not in the docker group yet — group membership only applies \
             to a new login — so Docker is run with `sg docker` rather than the administrator \
             password.",
        ),
        DockerAccess::Direct => {}
    }

    ctx.progress.detail(
        StepId::Nemotron,
        "Fetching the Nemotron images (several GB the first time)",
    );
    runner.bring_up(ctx, ngc_key)?;

    ctx.progress.detail(
        StepId::Nemotron,
        "Waiting for both containers to be ready (the first start downloads the model weights)",
    );
    wait_until_ready(ctx, &endpoints, &runner)
}

/// How this process is allowed to talk to Docker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum DockerAccess {
    /// root, or this session already has the docker group.
    Direct,
    /// The account is in the group on disk, but this login is older than that
    /// change, so `sg docker` opens a subprocess that can use the socket.
    Sg,
}

/// How `docker compose` is run on this machine.
struct ComposeRunner {
    compose_file: PathBuf,
    cache_dir: PathBuf,
    uid: u32,
    access: DockerAccess,
}

impl ComposeRunner {
    fn for_this_machine(compose_file: &Path, cache_dir: &Path) -> ComposeRunner {
        ComposeRunner {
            compose_file: compose_file.to_path_buf(),
            cache_dir: cache_dir.to_path_buf(),
            uid: current_uid(),
            access: docker_access(),
        }
    }

    /// NIM_UID and NIM_CACHE_DIR are set explicitly and deliberately: the
    /// compose file refuses to start without them, so that a bare
    /// `docker compose up` stops and says so instead of half-working.
    fn env(&self) -> Vec<(String, String)> {
        vec![
            ("NIM_UID".to_string(), self.uid.to_string()),
            (
                "NIM_CACHE_DIR".to_string(),
                self.cache_dir.display().to_string(),
            ),
        ]
    }

    fn docker(&self, args: &[String]) -> Cmd {
        match self.access {
            DockerAccess::Direct => Cmd::new("docker")
                .args(args.iter().cloned())
                .envs(self.env()),
            DockerAccess::Sg => Cmd::new("sg")
                .args(["docker", "-c", &sg_docker_command(args)])
                .envs(self.env()),
        }
    }

    fn compose(&self, args: &[&str]) -> Cmd {
        let file = self.compose_file.display().to_string();
        let mut docker_args = vec!["compose".to_string(), "-f".to_string(), file];
        docker_args.extend(args.iter().map(|s| (*s).to_string()));
        self.docker(&docker_args)
    }

    /// Log in if we have a key, then pull and start.
    ///
    /// Docker itself is never run through `pkexec` here: a graphical password
    /// prompt swallows stdin, which is how the NGC key would otherwise arrive.
    fn bring_up(&self, ctx: &mut Ctx, ngc_key: Option<&str>) -> Result<()> {
        let ngc_key = trimmed_ngc_key(ngc_key);

        if let Some(key) = ngc_key {
            ctx.log("Signing in to NVIDIA's registry (nvcr.io).");
            self.docker(&[
                "login".to_string(),
                "nvcr.io".to_string(),
                "--username".to_string(),
                "$oauthtoken".to_string(),
                "--password-stdin".to_string(),
            ])
            .stdin_secret(key)
            .redact(key)
            .timeout(Duration::from_secs(120))
            .run_ok(
                ctx.emitter.as_ref(),
                &ctx.cancel,
                "Signing in to NVIDIA's registry with the NGC API key",
            )
            .map_err(with_registry_hint)?;
        }

        let mut pull = self
            .compose(&["pull"])
            .timeout(Duration::from_secs(60 * 60));
        if let Some(key) = ngc_key {
            // In the environment only, so a container that has to fetch its
            // weights can use it. Never in the arguments.
            pull = pull.env("NGC_API_KEY", key).redact(key);
        }
        if let Err(err) = pull.run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            "Downloading the Nemotron container images",
        ) {
            if self.images_cached(ctx) {
                ctx.log(format!(
                    "Could not refresh the images from NVIDIA's registry; the copies already on \
                     this machine will be used. {err}"
                ));
            } else {
                return Err(with_registry_hint(err));
            }
        }

        let mut up = self
            .compose(&["up", "-d"])
            .timeout(Duration::from_secs(10 * 60));
        if let Some(key) = ngc_key {
            up = up.env("NGC_API_KEY", key).redact(key);
        }
        up.run_ok(
            ctx.emitter.as_ref(),
            &ctx.cancel,
            "Starting the Nemotron containers",
        )?;
        Ok(())
    }

    fn images_cached(&self, ctx: &Ctx) -> bool {
        IMAGES.iter().all(|image| {
            self.docker(&[
                "image".to_string(),
                "inspect".to_string(),
                (*image).to_string(),
            ])
            .quiet()
            .run(ctx.emitter.as_ref(), &ctx.cancel)
            .map(|out| out.success)
            .unwrap_or(false)
        })
    }

    /// Which containers have stopped or are restarting in a loop, or `None`
    /// when we could not find out.
    fn stopped_containers(&self, ctx: &Ctx) -> Option<String> {
        let output = self
            .compose(&["ps", "--status", "exited", "--status", "restarting", "-q"])
            .quiet()
            .run(ctx.emitter.as_ref(), &ctx.cancel)
            .ok()?;
        if !output.success {
            return None;
        }
        let ids = output.text().trim().to_string();
        if ids.is_empty() {
            None
        } else {
            Some(ids)
        }
    }

    fn logs_tail(&self, ctx: &Ctx, lines: usize) -> String {
        self.compose(&["logs", "--tail", &lines.to_string()])
            .quiet()
            .run(ctx.emitter.as_ref(), &ctx.cancel)
            .map(|out| out.tail(lines))
            .unwrap_or_else(|_| "(the containers' own log could not be read)".to_string())
    }
}

fn docker_access() -> DockerAccess {
    if current_uid() == 0 || detect::in_docker_group() {
        DockerAccess::Direct
    } else if have("sg") {
        DockerAccess::Sg
    } else {
        DockerAccess::Direct
    }
}

/// `sg docker -c` body: `exec` so Docker, not a leftover shell, is the process.
pub fn sg_docker_command(args: &[String]) -> String {
    let mut parts = vec!["docker".to_string()];
    parts.extend(args.iter().map(|a| shell_quote(a)));
    format!("exec {}", parts.join(" "))
}

/// Poll both containers until they answer, and stop early if one has died.
fn wait_until_ready(ctx: &mut Ctx, endpoints: &Endpoints, runner: &ComposeRunner) -> Result<()> {
    let start = Instant::now();
    let mut last_report = start;
    let mut last_container_check = start;

    loop {
        ctx.cancel.check()?;
        if is_ready(&endpoints.embed_url) && is_ready(&endpoints.rerank_url) {
            ctx.log(format!(
                "Nemotron is ready: {} (embedding), {} (reranking).",
                endpoints.embed_url, endpoints.rerank_url
            ));
            return Ok(());
        }

        let now = Instant::now();
        if now.duration_since(last_container_check) >= CRASH_CHECK {
            last_container_check = now;
            if let Some(ids) = runner.stopped_containers(ctx) {
                return Err(container_died(ctx, runner, &ids));
            }
        }

        if now.duration_since(start) >= READY_TIMEOUT {
            if let Some(ids) = runner.stopped_containers(ctx) {
                return Err(container_died(ctx, runner, &ids));
            }
            return Err(anyhow!(
                "The Nemotron containers were still not ready after 30 minutes. They may simply \
                 still be downloading their model weights; watch them with:\n  \
                 docker compose -f {} logs -f\nThe last few lines were:\n{}",
                runner.compose_file.display(),
                runner.logs_tail(ctx, 20)
            ));
        }

        if now.duration_since(last_report) >= Duration::from_secs(60) {
            last_report = now;
            let minutes = now.duration_since(start).as_secs() / 60;
            ctx.progress.detail(
                StepId::Nemotron,
                format!("Still waiting — {minutes} min so far (the model weights are several GB)"),
            );
        }
        std::thread::sleep(Duration::from_secs(5));
    }
}

fn container_died(ctx: &Ctx, runner: &ComposeRunner, ids: &str) -> anyhow::Error {
    anyhow!(
        "A Nemotron container stopped instead of starting, so waiting longer would not help. \
         Its last words were:\n{}\n(container {})",
        runner.logs_tail(ctx, 30),
        ids.lines().next().unwrap_or(ids).trim()
    )
}

fn is_ready(base_url: &str) -> bool {
    net::get_ok(
        &format!("{base_url}/v1/health/ready"),
        Duration::from_secs(3),
    )
}

pub fn privileged_program(privilege: Privilege) -> &'static str {
    match privilege {
        // The graphical prompt, so a person at the machine sees what is being
        // asked for; sudo when there is only a terminal.
        Privilege::Pkexec => "pkexec",
        Privilege::Sudo => "sudo",
    }
}

/// The numeric user id the containers run as, which the compose file insists
/// on being told so that the model cache ends up owned by the right person.
#[cfg(unix)]
pub fn current_uid() -> u32 {
    nix::unistd::Uid::current().as_raw()
}

#[cfg(not(unix))]
pub fn current_uid() -> u32 {
    0
}

/// Quote a value for a shell script. Paths with spaces in them are ordinary.
pub fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::engine::types::{DockerTool, GpuInfo, GpuVendor, OllamaTool, Tools};

    fn system(docker: DockerTool) -> SystemInfo {
        let mut system = SystemInfo::blank();
        system.os = "linux".to_string();
        system.gpu = GpuInfo {
            vendor: GpuVendor::Nvidia,
            name: Some("NVIDIA GB10".to_string()),
            memory_gb: Some(120.0),
            is_gb10: true,
            memory_unified: false,
        };
        system.tools = Tools {
            ollama: OllamaTool {
                present: true,
                version: Some("0.3.12".to_string()),
                running: true,
            },
            docker,
            systemd_user: true,
            pkexec: true,
        };
        system
    }

    #[test]
    fn a_dgx_spark_needs_nothing_but_the_docker_group() {
        // DGX OS ships Docker, compose and the container toolkit.
        let needs = needs_from(
            &system(DockerTool {
                present: true,
                usable_by_user: true,
                nvidia_runtime: true,
                compose: true,
            }),
            true,
        );
        assert!(!needs.anything());
    }

    #[test]
    fn a_bare_ubuntu_machine_needs_all_of_it() {
        let needs = needs_from(
            &system(DockerTool {
                present: false,
                usable_by_user: false,
                nvidia_runtime: false,
                compose: false,
            }),
            false,
        );
        assert_eq!(
            needs,
            DockerNeeds {
                install_docker: true,
                install_compose: false,
                install_toolkit: true,
                add_group: true,
            }
        );
        assert!(needs.anything());
    }

    #[test]
    fn docker_without_compose_gets_just_the_plugin() {
        let needs = needs_from(
            &system(DockerTool {
                present: true,
                usable_by_user: true,
                nvidia_runtime: true,
                compose: false,
            }),
            true,
        );
        assert!(needs.install_compose);
        assert!(!needs.install_docker);
        assert!(!needs.install_toolkit);
    }

    #[test]
    fn docker_present_but_this_session_cannot_use_it_does_not_reinstall_the_toolkit() {
        let needs = needs_from(
            &system(DockerTool {
                present: true,
                usable_by_user: false,
                nvidia_runtime: false,
                compose: true,
            }),
            true,
        );
        assert!(!needs.install_toolkit);
        assert!(!needs.install_docker);
        assert!(!needs.add_group);
    }

    #[test]
    fn the_privileged_script_asks_for_only_what_is_missing() {
        let everything = docker_setup_script(
            &DockerNeeds {
                install_docker: true,
                install_compose: false,
                install_toolkit: true,
                add_group: true,
            },
            "you",
        );
        assert!(everything.contains("apt-get install -y docker.io docker-compose-v2"));
        assert!(
            everything.contains("docker-compose-plugin"),
            "the older name is the fallback"
        );
        assert!(everything.contains("nvidia-container-toolkit"));
        assert!(everything.contains("nvidia-ctk runtime configure --runtime=docker"));
        assert!(everything.contains("systemctl restart docker"));
        assert!(everything.contains("usermod -aG docker 'you'"));
        assert!(everything.contains("sg docker"));
        assert!(everything.starts_with("#!/bin/bash"));
        assert!(everything.contains("set -euo pipefail"));

        let only_group = docker_setup_script(
            &DockerNeeds {
                add_group: true,
                ..Default::default()
            },
            "you",
        );
        assert!(!only_group.contains("apt-get"));
        assert!(!only_group.contains("nvidia-ctk"));
        assert!(only_group.contains("usermod -aG docker 'you'"));
    }

    #[test]
    fn an_awkward_user_name_cannot_run_a_command_of_its_own() {
        let script = docker_setup_script(
            &DockerNeeds {
                add_group: true,
                ..Default::default()
            },
            "you; rm -rf /",
        );
        assert!(script.contains("usermod -aG docker 'you; rm -rf /'"));
    }

    #[test]
    fn sg_docker_quotes_the_compose_file_and_the_literal_oauth_username() {
        let pull = sg_docker_command(&[
            "compose".to_string(),
            "-f".to_string(),
            "/home/you/.local/share/auc/app/current/nim/docker-compose.yml".to_string(),
            "pull".to_string(),
        ]);
        assert_eq!(
            pull,
            "exec docker 'compose' '-f' '/home/you/.local/share/auc/app/current/nim/docker-compose.yml' 'pull'"
        );

        let login = sg_docker_command(&[
            "login".to_string(),
            "nvcr.io".to_string(),
            "--username".to_string(),
            "$oauthtoken".to_string(),
            "--password-stdin".to_string(),
        ]);
        assert!(
            login.contains("'$oauthtoken'"),
            "the dollar must not be expanded by the sg shell: {login}"
        );
        assert!(!login.contains("nvapi-"));
    }

    #[test]
    fn a_pasted_key_drops_surrounding_space_and_a_blank_paste_is_no_key() {
        assert_eq!(trimmed_ngc_key(Some("  nvapi-abc\n")), Some("nvapi-abc"));
        assert_eq!(trimmed_ngc_key(Some("   \n")), None);
        assert_eq!(trimmed_ngc_key(None), None);
    }

    #[test]
    fn nvidia_refusing_the_key_is_said_in_plain_language_on_the_step() {
        let err = anyhow!("unauthorized: authentication required")
            .context("Downloading the Nemotron container images did not work.");
        assert!(short_nemotron_detail(&err).contains("NGC Catalog"));

        let login = anyhow!("Signing in to NVIDIA's registry with the NGC API key did not work.");
        assert!(short_nemotron_detail(&login).contains("sign in"));

        let generic = anyhow!("This copy of AUC has no nim/docker-compose.yml");
        assert!(short_nemotron_detail(&generic).contains("docker-compose.yml"));
    }

    #[test]
    fn the_graphical_prompt_is_used_behind_a_window_and_sudo_in_a_terminal() {
        assert_eq!(privileged_program(Privilege::Pkexec), "pkexec");
        assert_eq!(privileged_program(Privilege::Sudo), "sudo");
    }
}
