//! The same installer with no window, for a machine reached over SSH — which
//! is how most DGX Sparks are set up.
//!
//! It prints one line per step and the commands' own output underneath, asks
//! before it starts unless told not to, and exits 0 only if everything worked.

use std::io::{IsTerminal, Write};
use std::sync::Arc;

use clap::{Args, Parser, Subcommand, ValueEnum};

use crate::engine::cancel::Cancel;
use crate::engine::emitter::{ConsoleEmitter, LogSink};
use crate::engine::layout::Layout;
use crate::engine::recommend::recommend;
use crate::engine::types::{Action, InstallOptions, NemotronMode, NetworkScope, Recommendation};
use crate::engine::{self as engine, Privilege};

#[derive(Parser, Debug)]
#[command(
    name = "auc-installer",
    version,
    about = "Installs AUC — Assessments Under Curve",
    long_about = "Installs, updates, repairs and removes AUC. Run with no arguments for the \
                  window; add --headless on a machine you are reached over SSH."
)]
pub struct Cli {
    /// Run without a window, printing progress to this terminal.
    #[arg(long)]
    pub headless: bool,

    /// Do not ask for confirmation before starting.
    #[arg(long, short = 'y', global = true)]
    pub yes: bool,

    /// Install from this .tar.gz file or URL instead of the newest release.
    #[arg(long, global = true, value_name = "PATH_OR_URL")]
    pub source: Option<String>,

    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Subcommand, Debug)]
pub enum Command {
    /// Install AUC on this machine.
    Install(InstallArgs),
    /// Update to the newest release, keeping your data and settings.
    Update,
    /// Run the install steps again over the top of what is here.
    Repair,
    /// Finish setting up the NVIDIA Nemotron containers.
    FinishNemotron,
    /// Remove AUC. Your data is kept unless you say otherwise.
    Uninstall {
        /// Also delete the database, photos and backups. There is no undo.
        #[arg(long)]
        delete_data: bool,
        /// Also delete the downloaded Nemotron model files (several GB).
        #[arg(long)]
        delete_nemotron_cache: bool,
    },
    /// Print what this machine looks like, and what is installed, as JSON.
    Status,
}

#[derive(Args, Debug)]
pub struct InstallArgs {
    /// Do not set up the AI summary features.
    #[arg(long)]
    pub no_ai: bool,
    /// Use this model instead of the recommended one.
    #[arg(long, value_name = "NAME")]
    pub model: Option<String>,
    /// Set up the NVIDIA Nemotron retrieval engine.
    #[arg(long)]
    pub nemotron: bool,
    /// Do not set up the NVIDIA Nemotron retrieval engine.
    #[arg(long, conflicts_with = "nemotron")]
    pub no_nemotron: bool,
    /// Who can reach AUC: this machine only, or the local network.
    #[arg(long, value_enum, default_value_t = NetworkArg::Local)]
    pub network: NetworkArg,
    /// Take over an AUC install that was set up by hand with setup.sh.
    #[arg(long)]
    pub adopt: bool,
}

#[derive(ValueEnum, Clone, Copy, Debug)]
pub enum NetworkArg {
    /// 127.0.0.1 — only this machine can reach AUC.
    Local,
    /// 0.0.0.0 — anyone who can reach this machine can reach AUC.
    Lan,
}

impl From<NetworkArg> for NetworkScope {
    fn from(arg: NetworkArg) -> NetworkScope {
        match arg {
            NetworkArg::Local => NetworkScope::Local,
            NetworkArg::Lan => NetworkScope::Lan,
        }
    }
}

/// Parse the command line, but only when this is a command-line run.
///
/// A windowed launch can be handed arguments of its own by the desktop or by
/// `tauri dev`, and clap would reject them; so unless the user actually asked
/// for the terminal, the command line is left alone.
pub fn parse_if_headless() -> Option<Cli> {
    let wants_cli = std::env::args().skip(1).any(|arg| {
        matches!(
            arg.as_str(),
            "--headless" | "--help" | "-h" | "--version" | "-V"
        )
    });
    if !wants_cli {
        return None;
    }
    Some(Cli::parse())
}

/// Run the command line version. Returns the process exit code.
pub fn run(cli: Cli) -> i32 {
    // One way in for the source override, so the engine only has to look in
    // one place for it.
    if let Some(source) = &cli.source {
        std::env::set_var("AUC_INSTALLER_SOURCE", source);
    }

    let command = cli.command.unwrap_or(Command::Status);
    if matches!(command, Command::Status) {
        return print_status();
    }

    let system = engine::detect_system();
    if !system.supported {
        eprintln!();
        eprintln!(
            "  {}",
            system
                .unsupported_reason
                .unwrap_or_else(|| "AUC cannot be installed on this computer.".to_string())
        );
        eprintln!();
        return 1;
    }
    let suggestion = recommend(&system);

    let action = match build_action(&command, &suggestion, cli.yes) {
        Ok(action) => action,
        Err(message) => {
            eprintln!("  {message}");
            return 1;
        }
    };

    if !cli.yes {
        print_plan(&command, &action, &suggestion);
        if !confirm() {
            println!("  Nothing was changed.");
            return 1;
        }
    }

    let layout = match Layout::detect() {
        Ok(layout) => layout,
        Err(err) => {
            eprintln!("  {err}");
            return 1;
        }
    };
    let emitter = Arc::new(ConsoleEmitter::new(LogSink::open(&layout)));
    // In a terminal the administrator password is asked for with sudo; the
    // graphical prompt would have nowhere to appear.
    let outcome = engine::run_action(action, emitter, Cancel::new(), Privilege::Sudo);
    i32::from(!outcome.ok)
}

fn print_status() -> i32 {
    let system = engine::detect_system();
    let state = Layout::detect().ok().and_then(|l| engine::state::read(&l));
    let report = serde_json::json!({
        "system": system,
        "state": state,
        "recommendation": recommend(&system),
    });
    match serde_json::to_string_pretty(&report) {
        Ok(text) => {
            println!("{text}");
            0
        }
        Err(err) => {
            eprintln!("Could not describe this machine: {err}");
            1
        }
    }
}

fn build_action(
    command: &Command,
    suggestion: &Recommendation,
    assume_yes: bool,
) -> Result<Action, String> {
    match command {
        Command::Install(args) => {
            let ai_enabled = !args.no_ai && (suggestion.ai.recommended || args.model.is_some());
            let model = if ai_enabled {
                args.model
                    .clone()
                    .or_else(|| suggestion.default_model().map(str::to_string))
            } else {
                None
            };
            if ai_enabled && model.is_none() {
                return Err(
                    "No model was given and this machine has no recommendation, so there is \
                     nothing to install. Use --model NAME, or --no-ai."
                        .to_string(),
                );
            }

            let nemotron_enabled = if args.nemotron {
                true
            } else if args.no_nemotron {
                false
            } else {
                suggestion.nemotron.mode == NemotronMode::Default
            };
            if args.nemotron && suggestion.nemotron.mode == NemotronMode::Unavailable {
                return Err(format!(
                    "The NVIDIA Nemotron engine cannot run on this machine. {}",
                    suggestion.nemotron.reason
                ));
            }

            Ok(Action::Install {
                options: InstallOptions {
                    ai_enabled,
                    model,
                    nemotron_enabled,
                    ngc_key: if nemotron_enabled {
                        ngc_key(assume_yes)
                    } else {
                        None
                    },
                    network_scope: args.network.into(),
                    adopt_existing: args.adopt,
                },
            })
        }
        Command::Update => Ok(Action::Update),
        Command::Repair => Ok(Action::Repair),
        Command::FinishNemotron => Ok(Action::FinishNemotron {
            ngc_key: ngc_key(assume_yes),
        }),
        Command::Uninstall {
            delete_data,
            delete_nemotron_cache,
        } => Ok(Action::Uninstall {
            delete_data: *delete_data,
            delete_nemotron_cache: *delete_nemotron_cache,
        }),
        Command::Status => Err("Status does not change anything.".to_string()),
    }
}

/// The NGC API key: from the environment, or typed in with the echo off.
/// Never a command-line argument — arguments are visible to everyone on the
/// machine in the process list.
fn ngc_key(assume_yes: bool) -> Option<String> {
    if let Ok(key) = std::env::var("NGC_API_KEY") {
        let key = key.trim().to_string();
        if !key.is_empty() {
            println!("  Using the NGC API key from NGC_API_KEY.");
            return Some(key);
        }
    }
    if assume_yes || !std::io::stdin().is_terminal() {
        println!(
            "  No NGC_API_KEY is set. The Nemotron images will be fetched without signing in, \
             which works once they have been downloaded before."
        );
        return None;
    }

    println!();
    println!("  The Nemotron images come from NVIDIA's registry, which needs a one-time key.");
    println!("  Get one at ngc.nvidia.com, then Setup and Generate API Key.");
    println!("  Leave it blank to try without one (fine if the images are already here).");
    let key = rpassword::prompt_password("  NGC API key: ").ok()?;
    let key = key.trim().to_string();
    if key.is_empty() {
        None
    } else {
        Some(key)
    }
}

fn print_plan(command: &Command, action: &Action, suggestion: &Recommendation) {
    println!();
    println!("  {}", suggestion.best_message);
    println!();
    match action {
        Action::Install { options } => {
            println!("  This will install AUC on this machine:");
            println!(
                "    AI summaries      {}",
                if options.ai_enabled { "yes" } else { "no" }
            );
            if let Some(model) = &options.model {
                println!("    Model             {model}");
                println!("    Embedding model   {}", engine::recommend::EMBED_MODEL);
            }
            println!(
                "    NVIDIA Nemotron   {}",
                if options.nemotron_enabled {
                    "yes"
                } else {
                    "no"
                }
            );
            println!(
                "    Reachable from    {}",
                match options.network_scope {
                    NetworkScope::Local => "this machine only",
                    NetworkScope::Lan => "any machine on this network",
                }
            );
            if options.adopt_existing {
                println!("    Existing install  taken over (its data is copied, nothing deleted)");
            }
            println!("    {}", suggestion.ai.reason);
        }
        Action::Update => println!("  This will update AUC, after backing up your data first."),
        Action::Repair => println!("  This will run the install steps again over what is here."),
        Action::FinishNemotron { .. } => {
            println!("  This will finish setting up the NVIDIA Nemotron containers.");
            println!("    {}", suggestion.nemotron.reason);
        }
        Action::Uninstall {
            delete_data,
            delete_nemotron_cache,
        } => {
            println!("  This will remove AUC.");
            println!(
                "    Your data         {}",
                if *delete_data {
                    "DELETED — the database, photos and backups. There is no undo."
                } else {
                    "kept"
                }
            );
            println!(
                "    Nemotron models   {}",
                if *delete_nemotron_cache {
                    "deleted"
                } else {
                    "kept"
                }
            );
        }
    }
    if matches!(command, Command::Install(_)) {
        println!();
        println!("  Ollama and, if you chose Nemotron, Docker will be installed with your");
        println!("  administrator password. Nothing else on this machine is changed.");
    }
    println!();
}

fn confirm() -> bool {
    print!("  Proceed? [y/N] ");
    let _ = std::io::stdout().flush();
    let mut answer = String::new();
    if std::io::stdin().read_line(&mut answer).is_err() {
        return false;
    }
    matches!(answer.trim().to_ascii_lowercase().as_str(), "y" | "yes")
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::CommandFactory;

    #[test]
    fn the_command_line_is_the_one_the_contract_documents() {
        Cli::command().debug_assert();

        let cli = Cli::parse_from([
            "auc-installer",
            "--headless",
            "install",
            "--no-ai",
            "--network",
            "lan",
            "--adopt",
            "--yes",
        ]);
        assert!(cli.headless);
        assert!(cli.yes);
        let Some(Command::Install(args)) = cli.command else {
            panic!("install should parse");
        };
        assert!(args.no_ai);
        assert!(args.adopt);
        assert!(matches!(args.network, NetworkArg::Lan));
    }

    #[test]
    fn the_other_subcommands_parse_with_their_flags() {
        let cli = Cli::parse_from(["auc-installer", "--headless", "finish-nemotron"]);
        assert!(matches!(cli.command, Some(Command::FinishNemotron)));

        let cli = Cli::parse_from([
            "auc-installer",
            "--headless",
            "uninstall",
            "--delete-data",
            "--delete-nemotron-cache",
        ]);
        let Some(Command::Uninstall {
            delete_data,
            delete_nemotron_cache,
        }) = cli.command
        else {
            panic!("uninstall should parse");
        };
        assert!(delete_data && delete_nemotron_cache);

        let cli = Cli::parse_from(["auc-installer", "--headless", "status"]);
        assert!(matches!(cli.command, Some(Command::Status)));
    }

    #[test]
    fn a_source_can_be_given_for_an_offline_install() {
        let cli = Cli::parse_from([
            "auc-installer",
            "--headless",
            "--source",
            "/media/usb/auc-1.4.0.tar.gz",
            "install",
        ]);
        assert_eq!(cli.source.as_deref(), Some("/media/usb/auc-1.4.0.tar.gz"));
    }

    #[test]
    fn asking_for_nemotron_both_ways_at_once_is_refused() {
        let parsed = Cli::try_parse_from([
            "auc-installer",
            "--headless",
            "install",
            "--nemotron",
            "--no-nemotron",
        ]);
        assert!(parsed.is_err(), "the two flags contradict each other");
    }

    #[test]
    fn a_key_is_never_a_command_line_argument() {
        let parsed = Cli::try_parse_from([
            "auc-installer",
            "--headless",
            "install",
            "--ngc-key",
            "nvapi-secret",
        ]);
        assert!(
            parsed.is_err(),
            "there must be no way to pass a key in the arguments"
        );
    }
}
