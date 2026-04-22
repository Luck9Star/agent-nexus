# Phase 10: ap-cli — CLI Entry Point

> **Goal:** Port the CLI from Typer/Python to clap/Rust with colored output and --json/--follow flags.

**Python source:** `src/agent_nexus/platform/local/cli.py` (part of the 4,577-line local/ module)
**Rust target:** `crates/ap-cli/src/`
**Depends on:** All other crates

**Files:**
- Update: `crates/ap-cli/src/main.rs`
- Create: `crates/ap-cli/src/commands/mod.rs`
- Create: `crates/ap-cli/src/commands/init.rs`
- Create: `crates/ap-cli/src/commands/sources.rs`
- Create: `crates/ap-cli/src/commands/install.rs`
- Create: `crates/ap-cli/src/commands/run.rs`
- Create: `crates/ap-cli/src/commands/create.rs`
- Create: `crates/ap-cli/src/commands/check.rs`
- Create: `crates/ap-cli/src/commands/config.rs`
- Create: `crates/ap-cli/src/commands/evolution.rs`
- Create: `crates/ap-cli/src/commands/runtime.rs`
- Create: `crates/ap-cli/src/output.rs`

---

## Task 10.1: CLI framework with clap

**Rust target:** `crates/ap-cli/src/main.rs`

Define the full CLI structure using clap derive macros.

- [ ] **Step 1: Write CLI tests**

```rust
// crates/ap-cli/tests/cli_tests.rs
use assert_cmd::Command;

#[test]
fn help_flag() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--help")
        .assert()
        .success()
        .stdout(predicates::str::contains("agent-nexus"));
}

#[test]
fn version_flag() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("--version")
        .assert()
        .success();
}

#[test]
fn init_command() {
    let dir = tempfile::tempdir().unwrap();
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["init", "--dir", dir.path().to_str().unwrap()])
        .assert()
        .success();
    // Verify config.toml was created
    assert!(dir.path().join("config.toml").exists());
}

#[test]
fn check_command() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .arg("check")
        .assert()
        .success();
}

#[test]
fn sources_subcommands() {
    Command::cargo_bin("agent-nexus")
        .unwrap()
        .args(["sources", "list"])
        .assert()
        .success();
}
```

- [ ] **Step 2: Implement main.rs with clap**

```rust
use clap::{Parser, Subcommand};
use anyhow::Result;

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
        /// Target directory (default: current)
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
        /// Version to install (optional)
        #[arg(short, long)]
        version: Option<String>,
    },

    /// Run an agent with a task
    Run {
        /// Agent name
        agent: String,
        /// Task description
        #[arg(trailing_var_arg = true)]
        task: Vec<String>,
        /// Model override
        #[arg(short, long)]
        model: Option<String>,
    },

    /// Create a new agent
    Create {
        #[command(subcommand)]
        action: CreateAction,
    },

    /// Check environment and configuration
    Check,

    /// Get/set configuration values
    Config {
        #[command(subcommand)]
        action: ConfigAction,
    },

    /// Evolution engine commands
    Evolution {
        #[command(subcommand)]
        action: EvolutionAction,
    },

    /// Runtime execution commands
    Runtime {
        #[command(subcommand)]
        action: RuntimeAction,
    },
}

#[derive(Subcommand)]
enum SourcesAction {
    /// Add a new source
    Add {
        name: String,
        url: String,
        #[arg(short, long, default_value = "main")]
        branch: String,
    },
    /// List all sources
    List,
    /// Remove a source
    Remove { name: String },
}

#[derive(Subcommand)]
enum CreateAction {
    /// Create a new agent from template
    Agent {
        name: String,
        #[arg(short, long, default_value = "atomic")]
        r#type: String,
    },
}

#[derive(Subcommand)]
enum ConfigAction {
    /// Get a config value
    Get { key: String },
    /// Set a config value
    Set { key: String, value: String },
}

#[derive(Subcommand)]
enum EvolutionAction {
    /// Show evolution status
    Status,
    /// Promote a skill to an agent
    Promote { skill: String },
}

#[derive(Subcommand)]
enum RuntimeAction {
    /// Execute code in agent runtime
    Exec {
        #[arg(trailing_var_arg = true)]
        code: Vec<String>,
    },
}

#[tokio::main]
async fn main() -> Result<()> {
    tracing_subscriber::init();
    let cli = Cli::parse();

    match cli.command {
        Commands::Init { dir } => commands::init::run(&dir, cli.json)?,
        Commands::Sources { action } => commands::sources::run(action, cli.json)?,
        Commands::Install { agent, version } => commands::install::run(&agent, version.as_deref(), cli.json).await?,
        Commands::Run { agent, task, model } => commands::run::run(&agent, &task.join(" "), model.as_deref(), cli.json, cli.follow).await?,
        Commands::Create { action } => commands::create::run(action, cli.json)?,
        Commands::Check => commands::check::run(cli.json)?,
        Commands::Config { action } => commands::config::run(action, cli.json)?,
        Commands::Evolution { action } => commands::evolution::run(action, cli.json).await?,
        Commands::Runtime { action } => commands::runtime::run(action, cli.json).await?,
    }
    Ok(())
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-cli
git add crates/ap-cli/src/main.rs
git commit -m "feat(ap-cli): CLI framework with clap derive and full command tree"
```

---

## Task 10.2: Output formatting

**Rust target:** `crates/ap-cli/src/output.rs`

- [ ] **Write output module**

```rust
use owo_colors::OwoColorize;

pub struct OutputFormatter {
    json_mode: bool,
    follow_mode: bool,
}

impl OutputFormatter {
    pub fn new(json: bool, follow: bool) -> Self {
        Self { json_mode: json, follow_mode: follow }
    }

    pub fn success(&self, msg: &str) {
        if self.json_mode {
            println!(r#"{{"status":"ok","message":"{}"}}"#, msg);
        } else {
            eprintln!("{} {}", "✓".green(), msg);
        }
    }

    pub fn error(&self, msg: &str) {
        if self.json_mode {
            println!(r#"{{"status":"error","message":"{}"}}"#, msg);
        } else {
            eprintln!("{} {}", "✗".red(), msg);
        }
    }

    pub fn info(&self, msg: &str) {
        if self.json_mode {
            println!(r#"{{"status":"info","message":"{}"}}"#, msg);
        } else {
            eprintln!("{}", msg.dimmed());
        }
    }

    pub fn json<T: serde::Serialize>(&self, data: &T) {
        if self.json_mode {
            println!("{}", serde_json::to_string_pretty(data).unwrap());
        }
    }
}
```

- [ ] **Commit**

```bash
git add crates/ap-cli/src/output.rs
git commit -m "feat(ap-cli): output formatter with --json and colored modes"
```

---

## Task 10.3: Command implementations

Each command file is a thin layer that calls the appropriate crate.

**Pattern:**

```rust
// crates/ap-cli/src/commands/init.rs
use anyhow::Result;
use crate::output::OutputFormatter;

pub fn run(dir: &str, json: bool) -> Result<()> {
    let fmt = OutputFormatter::new(json, false);
    let path = std::path::Path::new(dir);

    // Create config.toml with defaults
    let config = ap_core::config::defaults::default_config();
    let toml_str = toml::to_string_pretty(&config)?;
    std::fs::write(path.join("config.toml"), &toml_str)?;

    // Create sources.yaml (empty)
    std::fs::write(path.join("sources.yaml"), "sources: []\n")?;

    fmt.success(&format!("Initialized agent-nexus project in {}", dir));
    Ok(())
}
```

Implement each command following this pattern:
- `init.rs` — Create config.toml + sources.yaml
- `sources.rs` — Delegate to `ap_fetcher::sources::SourceManager`
- `install.rs` — Delegate to `ap_fetcher::installer::GitInstaller` + `LockfileManager`
- `run.rs` — Delegate to `ap_core::router::PlatformRouter`
- `create.rs` — Generate agent scaffold
- `check.rs` — Check uv, python, config validity
- `config.rs` — Read/write config.toml
- `evolution.rs` — Delegate to `ap_evolution::EvolutionEngine`
- `runtime.rs` — Delegate to `ap_runtime`

- [ ] **Implement each command + verify + commit individually**

```bash
# After implementing all commands:
cargo test -p ap-cli
cargo clippy -p ap-cli -- -D warnings
git add crates/ap-cli/
git commit -m "feat(ap-cli): all CLI command implementations"
```

---

## Final Verification

- [ ] `cargo test -p ap-cli`
- [ ] `cargo clippy -p ap-cli -- -D warnings`
- [ ] `cargo build --release` — verify the binary builds
