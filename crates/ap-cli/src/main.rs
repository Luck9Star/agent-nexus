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

        /// Version to install (tag or semver)
        #[arg(short, long)]
        version: Option<String>,

        /// Agent type (atomic or composite)
        #[arg(short, long, default_value = "atomic")]
        r#type: String,
    },

    /// Run an agent with a task
    Run {
        /// Agent name to run
        agent: String,

        /// Task arguments
        #[arg(trailing_var_arg = true)]
        task: Vec<String>,

        /// Model to use (e.g. openai:gpt-4o)
        #[arg(short, long)]
        model: Option<String>,
    },

    /// Create new agents or resources
    Create {
        #[command(subcommand)]
        action: CreateAction,
    },

    /// Check environment and configuration health
    Check,

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

    /// Runtime commands
    Runtime {
        #[command(subcommand)]
        action: RuntimeAction,
    },

    /// Show environment information
    Env,

    /// Print version information
    Version,
}

#[derive(Subcommand)]
enum SourcesAction {
    /// List all configured sources
    List,

    /// Add a new source
    Add {
        /// Source name
        name: String,

        /// Git repository URL
        url: String,

        /// Branch to track
        #[arg(short, long, default_value = "main")]
        branch: String,
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
        /// Agent name
        name: String,
    },
}

#[derive(Subcommand)]
enum ConfigAction {
    /// Get a configuration value
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

    /// Show configuration overview
    Show,
}

#[derive(Subcommand)]
enum EvolutionAction {
    /// Show evolution engine status
    Status,

    /// Promote a skill to an agent
    Promote {
        /// Skill name to promote
        skill: String,
    },
}

#[derive(Subcommand)]
enum RuntimeAction {
    /// Execute an agent in the runtime
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
            SourcesAction::Add { name, url, branch } => {
                commands::sources::run_add(&name, &url, Some(&branch), &output)?;
            }
            SourcesAction::Remove { name } => commands::sources::run_remove(&name, &output)?,
        },

        Commands::Install { agent, version, r#type } => {
            commands::install::run(&agent, version.as_deref(), &r#type, &output)?;
        }

        Commands::Run {
            agent,
            task,
            model,
        } => commands::run::run(&agent, &task, model.as_deref(), &output)?,

        Commands::Create { action } => match action {
            CreateAction::Agent { name } => commands::create::run_agent(&name, &output)?,
        },

        Commands::Check => commands::check::run(&output)?,

        Commands::Config { action } => match action {
            ConfigAction::Get { key } => commands::config::run_get(&key, &output)?,
            ConfigAction::Set { key, value } => commands::config::run_set(&key, &value, &output)?,
            ConfigAction::Show => commands::config::run_show(&output)?,
        },

        Commands::Evolution { action } => match action {
            EvolutionAction::Status => commands::evolution::run_status(&output)?,
            EvolutionAction::Promote { skill } => {
                commands::evolution::run_promote(&skill, &output)?;
            }
        },

        Commands::Runtime { action } => match action {
            RuntimeAction::Exec { agent, args } => {
                commands::runtime::run_exec(&agent, &args, &output)?;
            }
        },

        Commands::Env => commands::env::run(&output)?,

        Commands::Version => {
            println!("agent-nexus {}", env!("CARGO_PKG_VERSION"));
        }
    }

    Ok(())
}
