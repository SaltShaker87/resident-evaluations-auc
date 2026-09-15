//! Every shape that crosses the line between the engine and the screens.
//!
//! The field names here ARE the JSON keys in CONTRACT.md sections 3 and 4 —
//! snake_case, spelled identically in the React half. Renaming anything in
//! this file breaks the other half of the installer, so don't.

use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

// ---------------------------------------------------------------------------
// SystemInfo — what we found on this machine
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Distro {
    pub id: String,
    pub version: String,
    pub pretty: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GpuVendor {
    Nvidia,
    Amd,
    Apple,
    None,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GpuInfo {
    pub vendor: GpuVendor,
    pub name: Option<String>,
    /// Total across every NVIDIA card. `None` when nothing could be read —
    /// which is not the same as zero, and the screens say so differently.
    pub memory_gb: Option<f64>,
    /// The same rule as `backend/retrieval_engine.py is_spark()`.
    pub is_gb10: bool,
    /// True when `memory_gb` is the machine's memory rather than a graphics
    /// card's own: a GB10 has one pool shared by processor and graphics, and
    /// nvidia-smi reports it as "[N/A]".
    #[serde(default)]
    pub memory_unified: bool,
}

impl GpuInfo {
    pub fn none() -> Self {
        GpuInfo {
            vendor: GpuVendor::None,
            name: None,
            memory_gb: None,
            is_gb10: false,
            memory_unified: false,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OllamaTool {
    pub present: bool,
    pub version: Option<String>,
    pub running: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct DockerTool {
    pub present: bool,
    pub usable_by_user: bool,
    pub nvidia_runtime: bool,
    pub compose: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Tools {
    pub ollama: OllamaTool,
    pub docker: DockerTool,
    pub systemd_user: bool,
    pub pkexec: bool,
}

impl Tools {
    /// Nothing found. The starting point for a scan, and all a stub platform
    /// can honestly report.
    pub fn none() -> Self {
        Tools {
            ollama: OllamaTool {
                present: false,
                version: None,
                running: false,
            },
            docker: DockerTool {
                present: false,
                usable_by_user: false,
                nvidia_runtime: false,
                compose: false,
            },
            systemd_user: false,
            pkexec: false,
        }
    }
}

/// An AUC that is already on this machine: either one we installed, or one
/// made by hand with `setup.sh`, which we can offer to take over.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ExistingInstall {
    Installer {
        version: String,
        state: InstallerState,
    },
    Manual {
        service_path: String,
        working_directory: String,
        env: BTreeMap<String, String>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SystemInfo {
    pub os: String,
    pub arch: String,
    pub distro: Option<Distro>,
    pub supported: bool,
    pub unsupported_reason: Option<String>,
    pub gpu: GpuInfo,
    pub memory_gb: f64,
    pub disk_free_gb: f64,
    pub internet: bool,
    pub tools: Tools,
    pub existing: Option<ExistingInstall>,
}

impl SystemInfo {
    /// A blank report for this machine, to be filled in by whichever platform
    /// is doing the looking.
    pub fn blank() -> Self {
        SystemInfo {
            os: crate::engine::os_name().to_string(),
            arch: crate::engine::arch_name().to_string(),
            distro: None,
            supported: false,
            unsupported_reason: None,
            gpu: GpuInfo::none(),
            memory_gb: 0.0,
            disk_free_gb: 0.0,
            internet: false,
            tools: Tools::none(),
            existing: None,
        }
    }
}

// ---------------------------------------------------------------------------
// Recommendation — what we suggest, and why
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AiRecommendation {
    pub recommended: bool,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelChoice {
    /// The exact name Ollama knows it by.
    pub name: String,
    pub label: String,
    pub description: String,
    pub min_memory_gb: u32,
    pub recommended: bool,
    pub fits: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NemotronMode {
    Default,
    Optional,
    Unavailable,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NemotronRecommendation {
    pub mode: NemotronMode,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Recommendation {
    pub ai: AiRecommendation,
    pub models: Vec<ModelChoice>,
    pub best_message: String,
    pub nemotron: NemotronRecommendation,
}

impl Recommendation {
    /// The model we would pick if the user just presses Continue.
    pub fn default_model(&self) -> Option<&str> {
        self.models
            .iter()
            .find(|m| m.recommended)
            .map(|m| m.name.as_str())
    }
}

// ---------------------------------------------------------------------------
// Action — what the screens asked us to do
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NetworkScope {
    /// 127.0.0.1 — only this machine.
    Local,
    /// 0.0.0.0 — anyone who can reach this machine.
    Lan,
}

impl NetworkScope {
    pub fn host(self) -> &'static str {
        match self {
            NetworkScope::Local => "127.0.0.1",
            NetworkScope::Lan => "0.0.0.0",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InstallOptions {
    pub ai_enabled: bool,
    pub model: Option<String>,
    pub nemotron_enabled: bool,
    /// May be absent even with Nemotron chosen: the images may already be
    /// cached, so we try without it and fall back rather than refusing.
    pub ngc_key: Option<String>,
    pub network_scope: NetworkScope,
    pub adopt_existing: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Action {
    Install {
        options: InstallOptions,
    },
    Update,
    Repair,
    FinishNemotron {
        ngc_key: Option<String>,
    },
    Uninstall {
        delete_data: bool,
        delete_nemotron_cache: bool,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct UpdateInfo {
    pub installed: Option<String>,
    pub latest: Option<String>,
    pub available: bool,
    pub notes: Option<String>,
    pub error: Option<String>,
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepId {
    // install / repair / finish_nemotron / update
    Prepare,
    Backup,
    Download,
    Python,
    Configure,
    Ollama,
    Models,
    Docker,
    Nemotron,
    Index,
    Autostart,
    Switch,
    Start,
    Preflight,
    // uninstall
    Stop,
    RemoveAutostart,
    RemoveApp,
    RemoveData,
    RemoveNemotronCache,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepStatus {
    Pending,
    Running,
    Done,
    Warning,
    Failed,
    Skipped,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StepEvent {
    pub id: StepId,
    pub label: String,
    pub status: StepStatus,
    pub detail: Option<String>,
    /// 0..1 where we can honestly say, `None` where we cannot.
    pub progress: Option<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PreflightLevel {
    Pass,
    Warn,
    Fail,
    Info,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PreflightLine {
    pub level: PreflightLevel,
    pub text: String,
    /// The indented "→ do this" line preflight.sh prints under a problem.
    pub hint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PreflightReport {
    pub lines: Vec<PreflightLine>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct OutcomeError {
    pub message: String,
    pub hint: Option<String>,
    pub details: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Outcome {
    pub ok: bool,
    pub cancelled: bool,
    pub summary: String,
    pub warnings: Vec<String>,
    pub app_url: Option<String>,
    pub nemotron_pending: bool,
    pub error: Option<OutcomeError>,
}

// ---------------------------------------------------------------------------
// InstallerState — the one file that remembers what we did
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Choices {
    pub ai_enabled: bool,
    pub model: Option<String>,
    pub nemotron_enabled: bool,
    pub network_scope: NetworkScope,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct InstallerState {
    pub schema: u32,
    pub version: String,
    pub installed_at: String,
    pub auc_home: String,
    pub choices: Choices,
    /// True while Nemotron was chosen but is not yet answering, so the app
    /// runs on Ollama and the screens can offer "finish Nemotron later".
    pub nemotron_pending: bool,
    /// The path of a hand-made setup.sh install we took over, or none.
    pub adopted_from: Option<String>,
}

/// The current shape of installer-state.json. Bump it only alongside code
/// that can read the older shape.
pub const STATE_SCHEMA: u32 = 1;
