//! agent-nexus CLI -- MCP-native Agent Platform command-line interface.

mod commands;
mod output;

use anyhow::Result;
use clap::{Parser, Subcommand};
use output::OutputFormatter;

#[derive(Parser)]
#[command(name = "agent-nexus")]
#[command(version, about = "MCP-native Agent Platform")]
struct Cli {
    #[command(subcommand)]
    command: Commands,

    /// Output as JSON
    #[arg(long, global = true)]
    json: bool,

    /// Follow output stream
    #[arg(long, global = true)]
    follow: bool,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize a new agent-nexus project
    Init {
        /// Target directory
        #[arg(short, long, default_value = ".")]
        dir: String,
    },

    /// Manage agent sources
    Sources {
        #[command(subcommand)]
        action: SourcesAction,
    },

    /// Install an agent from a source
    Install {
        /// Agent name to install
        agent: String,

        /// Specific version to install
        #[arg(short, long)]
        version: Option<String>,

        /// Git URL for direct install
        #[arg(short, long)]
        source: Option<String>,

        /// Install from local project agents/ directory
        #[arg(short, long)]
        local: bool,
    },

    /// Uninstall an agent
    Uninstall {
        /// Agent name to uninstall
        agent: String,
    },

    /// Update an installed agent to the latest version
    Update {
        /// Agent name to update
        agent: Option<String>,

        /// Update all installed agents
        #[arg(long)]
        all: bool,
    },

    /// Run an agent in the specified mode
    Run {
        /// Agent name to run
        agent: String,

        /// Run mode: mcp, router, cli
        #[arg(short, long, default_value = "mcp")]
        mode: String,

        /// Transport: stdio, sse
        #[arg(short, long, default_value = "stdio")]
        transport: String,

        /// Extra arguments forwarded to the agent (CLI mode only)
        #[arg(trailing_var_arg = true)]
        extra: Vec<String>,
    },

    /// List installed agents
    List,

    /// Search for available agents
    Search {
        /// Search query
        query: String,
    },

    /// Show detailed information about an agent
    Info {
        /// Agent name
        agent: String,
    },

    /// Create new agents or resources
    Create {
        #[command(subcommand)]
        action: CreateAction,
    },

    /// Validate an agent package for completeness and correctness
    Check {
        /// Path to agent package directory
        path: Option<String>,
    },

    /// Read/write configuration values
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },

    /// Self-Evolution Engine commands
    Evolution {
        #[command(subcommand)]
        action: EvolutionAction,
    },

    /// Runtime management commands
    Runtime {
        #[command(subcommand)]
        action: RuntimeAction,
    },

    /// Print resolved environment snapshot
    Env,

    /// Run diagnostic checks on the agent-nexus installation
    Doctor,

    /// Print the agent-nexus version
    Version,
}

#[derive(Subcommand)]
enum SourcesAction {
    /// List all configured sources
    List,

    /// Add a new source
    Add {
        /// Source name
        #[arg(long)]
        name: String,

        /// Git repository URL
        #[arg(long)]
        url: String,

        /// Source type (default: git)
        #[arg(long)]
        r#type: Option<String>,

        /// Branch to track (default: main)
        #[arg(long)]
        branch: Option<String>,
    },

    /// Remove a source by name
    Remove {
        /// Source name to remove
        name: String,
    },
}

#[derive(Subcommand)]
enum CreateAction {
    /// Create a new agent scaffold
    Agent {
        /// Agent name (kebab-case, e.g. my-agent)
        name: String,

        /// Agent description (required without --wizard)
        #[arg(short, long)]
        description: Option<String>,

        /// Tool pattern: simple (run) or pipeline (analyze/execute/report)
        #[arg(short, long, default_value = "simple")]
        tools: String,

        /// Run interactive wizard
        #[arg(short, long)]
        wizard: bool,

        /// Output directory (default: agents/atomic/)
        #[arg(short, long)]
        output: Option<String>,
    },
}

#[derive(Subcommand)]
enum ConfigAction {
    /// Show merged configuration
    Show,

    /// Get a specific config value by dot-path key
    Get {
        /// Dot-separated key (e.g. models.default)
        key: String,
    },

    /// Set a configuration value
    Set {
        /// Dot-separated key
        key: String,

        /// Value to set
        value: String,
    },

    /// Open config.toml in $EDITOR
    Edit,

    /// Validate the current configuration
    Validate,

    /// List all configured providers and their API key status
    Providers,

    /// Print the config directory path
    Path,
}

#[derive(Subcommand)]
enum EvolutionAction {
    /// Show evolution subsystem status summary
    Status,

    /// Show health diagnostics for skills
    Health {
        /// Skill name for detailed view
        skill_name: Option<String>,

        /// Show threshold details
        #[arg(short, long)]
        verbose: bool,
    },

    /// List skills in the evolution system
    List {
        /// Show all skills including inactive
        #[arg(long)]
        all: bool,
    },

    /// Show version lineage for a skill
    History {
        /// Skill name or ID to trace ancestry
        skill_name: String,
    },

    /// Show evolution quality metrics
    Metrics {
        /// Filter by agent name
        #[arg(short, long)]
        agent: Option<String>,
    },

    /// Trigger a FIX evolution on an unhealthy skill
    Fix {
        /// Skill ID to fix
        skill_id: String,
    },

    /// Promote a skill candidate to a standalone agent
    Promote {
        /// Skill ID to promote
        skill_id: String,
    },
}

#[derive(Subcommand)]
enum RuntimeAction {
    /// Start an agent or all agents
    Start {
        /// Agent name to start
        agent: Option<String>,

        /// Start all installed agents
        #[arg(long)]
        all: bool,
    },

    /// Stop a running agent or all agents
    Stop {
        /// Agent name to stop
        agent: Option<String>,

        /// Stop all running agents
        #[arg(long)]
        all: bool,
    },

    /// Restart a running agent
    Restart {
        /// Agent name to restart
        agent: String,
    },

    /// Show status of all agents
    Status,

    /// Show recent log output for an agent
    Logs {
        /// Agent name
        agent: String,

        /// Number of lines to show
        #[arg(short = 'n', long, default_value = "50")]
        lines: usize,

        /// Follow log output
        #[arg(short, long)]
        follow: bool,
    },

    /// Show running agents (alias for status)
    Ps,

    /// Execute an agent in the runtime via IPC
    Exec {
        /// Agent name
        agent: String,

        /// Arguments to pass to the agent
        #[arg(trailing_var_arg = true)]
        args: Vec<String>,
    },
}

fn main() -> Result<()> {
    // Initialize tracing (minimal default)
    // If init fails (e.g. already initialized), that's acceptable in tests or
    // when embedded -- log a debug message but don't crash.
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("warn")),
        )
        .try_init()
        .inspect_err(|e| eprintln!("tracing init failed (non-fatal): {e}"));

    let cli = Cli::parse();
    let output = OutputFormatter::new(cli.json, cli.follow);

    match cli.command {
        Commands::Init { dir } => commands::init::run(&dir, &output)?,

        Commands::Sources { action } => match action {
            SourcesAction::List => commands::sources::run_list(&output)?,
            SourcesAction::Add { name, url, r#type, branch } => {
                commands::sources::run_add(&name, &url, r#type.as_deref(), branch.as_deref(), &output)?;
            }
            SourcesAction::Remove { name } => commands::sources::run_remove(&name, &output)?,
        },

        Commands::Install { agent, version, source, local } => {
            commands::install::run(&agent, version.as_deref(), source.as_deref(), local, &output)?;
        }

        Commands::Uninstall { agent } => {
            commands::install::run_uninstall(&agent, &output)?;
        }

        Commands::Update { agent, all } => {
            commands::install::run_update(agent.as_deref(), all, &output)?;
        }

        Commands::Run {
            agent,
            mode,
            transport,
            extra,
        } => commands::run::run(&agent, &mode, &transport, &extra, &output)?,

        Commands::List => commands::install::run_list(&output)?,

        Commands::Search { query } => commands::install::run_search(&query, &output)?,

        Commands::Info { agent } => commands::install::run_info(&agent, &output)?,

        Commands::Create { action } => match action {
            CreateAction::Agent {
                name,
                description,
                tools,
                wizard,
                output: out_dir,
            } => commands::create::run_agent(
                &name,
                description.as_deref(),
                &tools,
                wizard,
                out_dir.as_deref(),
                &output,
            )?,
        },

        Commands::Check { path } => {
            match path {
                Some(p) => commands::check::run_check_package(&p, &output)?,
                None => commands::check::run(&output)?,
            }
        }

        Commands::Config { action } => match action {
            ConfigAction::Show => commands::config::run_show(&output)?,
            ConfigAction::Get { key } => commands::config::run_get(&key, &output)?,
            ConfigAction::Set { key, value } => commands::config::run_set(&key, &value, &output)?,
            ConfigAction::Edit => commands::config::run_edit(&output)?,
            ConfigAction::Validate => commands::config::run_validate(&output)?,
            ConfigAction::Providers => commands::config::run_providers(&output)?,
            ConfigAction::Path => commands::config::run_path(&output)?,
        },

        Commands::Evolution { action } => match action {
            EvolutionAction::Status => commands::evolution::run_status(&output)?,
            EvolutionAction::Health { skill_name, verbose } => {
                commands::evolution::run_health(skill_name.as_deref(), verbose, &output)?;
            }
            EvolutionAction::List { all } => {
                commands::evolution::run_list(all, &output)?;
            }
            EvolutionAction::History { skill_name } => {
                commands::evolution::run_history(&skill_name, &output)?;
            }
            EvolutionAction::Metrics { agent } => {
                commands::evolution::run_metrics(agent.as_deref(), &output)?;
            }
            EvolutionAction::Fix { skill_id } => {
                commands::evolution::run_fix(&skill_id, &output)?;
            }
            EvolutionAction::Promote { skill_id } => {
                commands::evolution::run_promote(&skill_id, &output)?;
            }
        },

        Commands::Runtime { action } => match action {
            RuntimeAction::Start { agent, all } => {
                commands::runtime::run_start(agent.as_deref(), all, &output)?;
            }
            RuntimeAction::Stop { agent, all } => {
                commands::runtime::run_stop(agent.as_deref(), all, &output)?;
            }
            RuntimeAction::Restart { agent } => {
                commands::runtime::run_restart(&agent, &output)?;
            }
            RuntimeAction::Status => {
                commands::runtime::run_status(&output)?;
            }
            RuntimeAction::Logs { agent, lines, follow } => {
                commands::runtime::run_logs(&agent, lines, follow, &output)?;
            }
            RuntimeAction::Ps => {
                commands::runtime::run_status(&output)?;
            }
            RuntimeAction::Exec { agent, args } => {
                commands::runtime::run_exec(&agent, &args, &output)?;
            }
        },

        Commands::Env => commands::env::run(&output)?,

        Commands::Doctor => commands::check::run(&output)?,

        Commands::Version => {
            println!("agent-nexus {}", env!("CARGO_PKG_VERSION"));
        }
    }

    Ok(())
}
