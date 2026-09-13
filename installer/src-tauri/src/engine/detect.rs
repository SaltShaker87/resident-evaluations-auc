//! Working out what this machine is.
//!
//! Everything here either reads a file, asks a program a question, or parses
//! what came back. The parsing is separated out so it can be tested against
//! real output from a Spark and from an ordinary desktop, neither of which is
//! available on the machine this code is written on.

use std::collections::BTreeMap;
use std::path::Path;
use std::time::Duration;

use crate::engine::layout::Layout;
use crate::engine::process::{capture, have, succeeds};
use crate::engine::types::{
    Distro, DockerTool, ExistingInstall, GpuInfo, GpuVendor, OllamaTool, Tools,
};
use crate::engine::{net, state};

/// Where we check that this machine can see the outside world. GitHub,
/// because that is where the release actually comes from.
pub const INTERNET_PROBE: &str = "https://api.github.com";

// ---------------------------------------------------------------------------
// /etc/os-release
// ---------------------------------------------------------------------------

/// Which Linux this is. Only the three fields the screens show are read.
pub fn parse_os_release(text: &str) -> Option<Distro> {
    let mut fields: BTreeMap<&str, String> = BTreeMap::new();
    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            fields.insert(key.trim(), strip_quotes(value.trim()).to_string());
        }
    }
    let id = fields.get("ID").cloned().unwrap_or_default();
    let version = fields.get("VERSION_ID").cloned().unwrap_or_default();
    let pretty = fields
        .get("PRETTY_NAME")
        .cloned()
        .unwrap_or_else(|| format!("{id} {version}").trim().to_string());
    if id.is_empty() && pretty.is_empty() {
        return None;
    }
    Some(Distro {
        id,
        version,
        pretty,
    })
}

fn strip_quotes(value: &str) -> &str {
    for quote in ['"', '\''] {
        if value.len() >= 2 && value.starts_with(quote) && value.ends_with(quote) {
            return &value[1..value.len() - 1];
        }
    }
    value
}

pub fn read_os_release() -> Option<Distro> {
    for path in ["/etc/os-release", "/usr/lib/os-release"] {
        if let Ok(text) = std::fs::read_to_string(path) {
            if let Some(distro) = parse_os_release(&text) {
                return Some(distro);
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// The GPU
// ---------------------------------------------------------------------------

/// Parse `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`.
///
/// Memory is added up across cards, because the tiers in CONTRACT.md are about
/// how much GPU memory the machine has in total. nvidia-smi reports MiB; the
/// screens talk in GB, and the number people expect for a 24 GB card is 24, so
/// the conversion is by 1024 rather than by 1000.
pub fn parse_nvidia_smi(text: &str) -> Option<GpuInfo> {
    let mut names: Vec<String> = Vec::new();
    let mut total_mib: f64 = 0.0;
    let mut saw_memory = false;

    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let mut parts = line.split(',');
        let Some(name) = parts.next().map(str::trim) else {
            continue;
        };
        if name.is_empty() {
            continue;
        }
        names.push(name.to_string());
        // "[N/A]" and "[Not Supported]" happen on some virtualised cards.
        if let Some(mib) = parts
            .next()
            .map(str::trim)
            .and_then(|m| m.parse::<f64>().ok())
        {
            total_mib += mib;
            saw_memory = true;
        }
    }

    if names.is_empty() {
        return None;
    }
    let is_gb10 = names.iter().any(|n| n.contains("GB10"));
    Some(GpuInfo {
        vendor: GpuVendor::Nvidia,
        name: Some(names[0].clone()),
        memory_gb: if saw_memory {
            Some((total_mib / 1024.0).round())
        } else {
            None
        },
        is_gb10,
    })
}

/// What the GPU is, as far as we can tell without installing anything.
pub fn detect_gpu() -> GpuInfo {
    if have("nvidia-smi") {
        if let Some(out) = capture(
            "nvidia-smi",
            &[
                "--query-gpu=name,memory.total",
                "--format=csv,noheader,nounits",
            ],
        ) {
            if let Some(gpu) = parse_nvidia_smi(&out) {
                return gpu;
            }
        }
    }
    // Not an NVIDIA machine. AMD is worth naming because the screens can then
    // say "this GPU cannot run the AI features" rather than "no GPU found".
    if Path::new("/sys/module/amdgpu").exists() {
        return GpuInfo {
            vendor: GpuVendor::Amd,
            name: None,
            memory_gb: None,
            is_gb10: false,
        };
    }
    GpuInfo::none()
}

// ---------------------------------------------------------------------------
// Memory and disk
// ---------------------------------------------------------------------------

/// MemTotal from /proc/meminfo, in GB, to one decimal place.
pub fn parse_meminfo(text: &str) -> Option<f64> {
    for line in text.lines() {
        if let Some(rest) = line.strip_prefix("MemTotal:") {
            let kb: f64 = rest
                .split_whitespace()
                .next()
                .and_then(|n| n.parse().ok())?;
            return Some(round1(kb / 1024.0 / 1024.0));
        }
    }
    None
}

pub fn read_memory_gb() -> f64 {
    std::fs::read_to_string("/proc/meminfo")
        .ok()
        .and_then(|text| parse_meminfo(&text))
        .unwrap_or(0.0)
}

/// Free space on the filesystem that will hold $AUC_HOME.
///
/// $AUC_HOME itself usually does not exist yet on a first install, so the
/// question is asked of the nearest folder above it that does.
pub fn disk_free_gb(path: &Path) -> f64 {
    let mut candidate = path;
    loop {
        if candidate.exists() {
            break;
        }
        match candidate.parent() {
            Some(parent) => candidate = parent,
            None => break,
        }
    }
    free_gb(candidate).unwrap_or(0.0)
}

#[cfg(unix)]
fn free_gb(path: &Path) -> Option<f64> {
    let stats = nix::sys::statvfs::statvfs(path).ok()?;
    let block = stats.fragment_size() as f64;
    let free = stats.blocks_available() as f64;
    Some(round1(free * block / 1_073_741_824.0))
}

#[cfg(not(unix))]
fn free_gb(_path: &Path) -> Option<f64> {
    None
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

// ---------------------------------------------------------------------------
// The tools AUC leans on
// ---------------------------------------------------------------------------

/// `ollama --version` prints a sentence; the screens want the number.
pub fn parse_ollama_version(text: &str) -> Option<String> {
    text.split_whitespace()
        .find(|word| word.chars().next().is_some_and(|c| c.is_ascii_digit()) && word.contains('.'))
        .map(|word| {
            word.trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '.')
                .to_string()
        })
}

/// The groups `id -nG` says this user is in.
pub fn parse_groups(text: &str) -> Vec<String> {
    text.split_whitespace().map(str::to_string).collect()
}

pub fn in_docker_group() -> bool {
    capture("id", &["-nG"])
        .map(|out| parse_groups(&out).iter().any(|g| g == "docker"))
        .unwrap_or(false)
}

pub fn detect_tools() -> Tools {
    let ollama_present = have("ollama");
    let ollama = OllamaTool {
        present: ollama_present,
        version: if ollama_present {
            capture("ollama", &["--version"]).and_then(|out| parse_ollama_version(&out))
        } else {
            None
        },
        // Asked over HTTP rather than of systemd: what matters is whether we
        // can pull a model through it, not how it was started.
        running: net::get_ok(
            &format!(
                "{}/api/tags",
                crate::engine::steps::ollama::DEFAULT_OLLAMA_URL
            ),
            Duration::from_secs(3),
        ),
    };

    let docker_present = have("docker");
    let usable = docker_present && succeeds("docker", &["info"]);
    let nvidia_runtime = usable
        && capture("docker", &["info", "--format", "{{json .Runtimes}}"])
            .map(|out| out.contains("nvidia"))
            .unwrap_or(false);
    let docker = DockerTool {
        present: docker_present,
        usable_by_user: usable,
        nvidia_runtime,
        compose: docker_present && succeeds("docker", &["compose", "version"]),
    };

    Tools {
        ollama,
        docker,
        // The single most common headless surprise: no user session means no
        // user services, so auto-start cannot be set up at all.
        systemd_user: have("systemctl") && succeeds("systemctl", &["--user", "show-environment"]),
        pkexec: have("pkexec"),
    }
}

pub fn has_internet() -> bool {
    net::can_reach(INTERNET_PROBE, Duration::from_secs(5))
}

// ---------------------------------------------------------------------------
// An AUC that is already here
// ---------------------------------------------------------------------------

/// What a hand-made `setup.sh` install told systemd about itself.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ManualUnit {
    pub working_directory: Option<String>,
    pub env: BTreeMap<String, String>,
}

/// Read `WorkingDirectory=` and `Environment=` out of a unit file.
///
/// Those two lines are the whole of a `setup.sh` install's configuration:
/// where the code is, and the settings that live nowhere else.
pub fn parse_manual_unit(text: &str) -> ManualUnit {
    let mut unit = ManualUnit::default();
    for line in text.lines() {
        let line = line.trim();
        if let Some(dir) = line.strip_prefix("WorkingDirectory=") {
            unit.working_directory = Some(dir.trim().to_string());
        } else if let Some(assignment) = line.strip_prefix("Environment=") {
            // systemd allows several on one line, and allows quotes.
            for pair in split_environment(assignment) {
                if let Some((key, value)) = pair.split_once('=') {
                    unit.env.insert(
                        key.trim().to_string(),
                        strip_quotes(value.trim()).to_string(),
                    );
                }
            }
        }
    }
    unit
}

/// Split `Environment=` into its assignments, respecting quotes.
fn split_environment(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut current = String::new();
    let mut quote: Option<char> = None;
    for ch in text.trim().chars() {
        match quote {
            Some(q) if ch == q => quote = None,
            Some(_) => current.push(ch),
            None if ch == '"' || ch == '\'' => quote = Some(ch),
            None if ch.is_whitespace() => {
                if !current.is_empty() {
                    out.push(std::mem::take(&mut current));
                }
            }
            None => current.push(ch),
        }
    }
    if !current.is_empty() {
        out.push(current);
    }
    out
}

/// Is AUC already on this machine, and did we put it there?
pub fn detect_existing(layout: &Layout) -> Option<ExistingInstall> {
    if let Some(state) = state::read(layout) {
        return Some(ExistingInstall::Installer {
            version: state.version.clone(),
            state,
        });
    }
    let service = layout.unit_path("auc.service");
    let text = std::fs::read_to_string(&service).ok()?;
    let unit = parse_manual_unit(&text);
    Some(ExistingInstall::Manual {
        service_path: service.display().to_string(),
        working_directory: unit.working_directory.unwrap_or_default(),
        env: unit.env,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const UBUNTU: &str = r#"PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
VERSION="24.04.1 LTS (Noble Numbat)"
VERSION_CODENAME=noble
ID=ubuntu
ID_LIKE=debian
HOME_URL="https://www.ubuntu.com/"
"#;

    const DGX: &str = r#"PRETTY_NAME="NVIDIA DGX OS 7.2.3"
NAME="NVIDIA DGX OS"
VERSION_ID="7.2.3"
ID=dgx
ID_LIKE="ubuntu debian"
"#;

    #[test]
    fn os_release_is_read_the_way_the_screens_want_it() {
        let distro = parse_os_release(UBUNTU).expect("ubuntu should parse");
        assert_eq!(distro.id, "ubuntu");
        assert_eq!(distro.version, "24.04");
        assert_eq!(distro.pretty, "Ubuntu 24.04.1 LTS");

        let spark = parse_os_release(DGX).expect("dgx os should parse");
        assert_eq!(spark.id, "dgx");
        assert_eq!(spark.pretty, "NVIDIA DGX OS 7.2.3");
    }

    #[test]
    fn os_release_without_a_pretty_name_still_says_something() {
        let distro = parse_os_release("ID=fedora\nVERSION_ID=41\n").expect("should parse");
        assert_eq!(distro.pretty, "fedora 41");
    }

    #[test]
    fn an_empty_os_release_is_no_answer_rather_than_a_blank_one() {
        assert!(parse_os_release("").is_none());
        assert!(parse_os_release("# nothing here\n").is_none());
    }

    #[test]
    fn a_spark_is_recognised_by_its_gb10() {
        let gpu = parse_nvidia_smi("NVIDIA GB10, 122880\n").expect("should parse");
        assert_eq!(gpu.vendor, GpuVendor::Nvidia);
        assert_eq!(gpu.name.as_deref(), Some("NVIDIA GB10"));
        assert_eq!(gpu.memory_gb, Some(120.0));
        assert!(gpu.is_gb10);
    }

    #[test]
    fn two_cards_are_added_together() {
        let gpu =
            parse_nvidia_smi("NVIDIA GeForce RTX 3090, 24576\nNVIDIA GeForce RTX 3090, 24576\n")
                .expect("should parse");
        assert_eq!(gpu.memory_gb, Some(48.0));
        assert!(!gpu.is_gb10);
        assert_eq!(gpu.name.as_deref(), Some("NVIDIA GeForce RTX 3090"));
    }

    #[test]
    fn a_card_that_will_not_say_how_much_memory_it_has_is_reported_as_unknown() {
        let gpu = parse_nvidia_smi("NVIDIA GeForce RTX 3090, [N/A]\n").expect("should parse");
        assert_eq!(gpu.memory_gb, None);
        assert_eq!(gpu.vendor, GpuVendor::Nvidia);
    }

    #[test]
    fn no_nvidia_output_is_no_gpu() {
        assert!(parse_nvidia_smi("").is_none());
        assert!(parse_nvidia_smi("\n\n").is_none());
    }

    #[test]
    fn memory_comes_from_meminfo() {
        let text = "MemTotal:       131923248 kB\nMemFree:         2000000 kB\n";
        assert_eq!(parse_meminfo(text), Some(125.8));
        assert_eq!(parse_meminfo("MemFree: 100 kB\n"), None);
    }

    #[test]
    fn the_ollama_version_is_pulled_out_of_its_sentence() {
        assert_eq!(
            parse_ollama_version("ollama version is 0.3.12\n").as_deref(),
            Some("0.3.12")
        );
        assert_eq!(parse_ollama_version("ollama version").as_deref(), None);
    }

    #[test]
    fn group_membership_is_read_from_id() {
        let groups = parse_groups("you adm sudo docker plugdev\n");
        assert!(groups.iter().any(|g| g == "docker"));
        assert!(!parse_groups("you adm sudo\n").iter().any(|g| g == "docker"));
    }

    #[test]
    fn a_hand_made_install_tells_us_where_it_lives_and_how_it_is_configured() {
        let unit = parse_manual_unit(
            "[Unit]\n\
             Description=AUC\n\
             [Service]\n\
             WorkingDirectory=/home/you/resident-evaluations-auc/auc/backend\n\
             ExecStart=/home/you/resident-evaluations-auc/auc/run.sh\n\
             Environment=OLLAMA_MODEL=qwen3:8b\n\
             Environment=AUC_BACKUP_DIR=\"/mnt/One Drive/auc\"\n\
             Environment=AUC_PORT=3000 AUC_HOST=0.0.0.0\n",
        );
        assert_eq!(
            unit.working_directory.as_deref(),
            Some("/home/you/resident-evaluations-auc/auc/backend")
        );
        assert_eq!(
            unit.env.get("OLLAMA_MODEL").map(String::as_str),
            Some("qwen3:8b")
        );
        assert_eq!(
            unit.env.get("AUC_BACKUP_DIR").map(String::as_str),
            Some("/mnt/One Drive/auc")
        );
        assert_eq!(unit.env.get("AUC_PORT").map(String::as_str), Some("3000"));
        assert_eq!(
            unit.env.get("AUC_HOST").map(String::as_str),
            Some("0.0.0.0")
        );
    }

    #[test]
    fn free_space_is_asked_of_the_nearest_folder_that_exists() {
        let dir = tempfile::tempdir().expect("temp dir");
        let missing = dir.path().join("not/created/yet/auc");
        // The number depends on the machine; that it is a number is the point.
        assert!(disk_free_gb(&missing) >= 0.0);
    }
}
