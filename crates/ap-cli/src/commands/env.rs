//! `agent-nexus env` -- display environment information.

use std::process::Command;

use anyhow::Result;

use crate::commands;
use crate::output::OutputFormatter;

/// Run `env` command -- display config dir, python version, git, uv, providers status.
///
/// Marked as returning `Result` for command-handler uniformity; this command
/// cannot fail in practice.
#[allow(clippy::unnecessary_wraps)]
pub fn run(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir().unwrap_or_else(|_| ".".into()));

    let python_version = get_command_output("python3", &["--version"]);
    let git_version = get_command_output("git", &["--version"]);
    let uv_version = get_command_output("uv", &["--version"]);

    // Load providers from config
    let provider_names = load_provider_names(&root);

    if output.is_json() {
        let mut obj = serde_json::Map::new();
        obj.insert("config_dir".into(), serde_json::Value::String(root.display().to_string()));
        obj.insert("python_version".into(), serde_json::Value::String(python_version.clone()));
        obj.insert("git_version".into(), serde_json::Value::String(git_version.clone()));
        obj.insert("uv_version".into(), serde_json::Value::String(uv_version.clone()));
        let providers: Vec<serde_json::Value> = provider_names.iter().map(|n| serde_json::Value::String(n.clone())).collect();
        obj.insert("providers".into(), serde_json::Value::Array(providers));
        println!("{}", serde_json::to_string_pretty(&obj).expect("JSON serialization is infallible"));
    } else {
        println!("Config dir: {}", root.display());
        println!("Python: {python_version}");
        println!("Git: {git_version}");
        println!("uv: {uv_version}");
        if provider_names.is_empty() {
            println!("Providers: (none configured)");
        } else {
            println!("Providers: {}", provider_names.join(", "));
        }
    }

    Ok(())
}

/// Run a command with args and return its stdout trimmed, or "not found" on failure.
fn get_command_output(cmd: &str, args: &[&str]) -> String {
    Command::new(cmd)
        .args(args)
        .output().map_or_else(|_| "not found".to_string(), |o| {
            if o.status.success() {
                String::from_utf8_lossy(&o.stdout).trim().to_string()
            } else {
                String::from_utf8_lossy(&o.stderr).trim().to_string()
            }
        })
}

/// Load provider names from config.toml, falling back to defaults.
fn load_provider_names(root: &std::path::Path) -> Vec<String> {
    let config_path = root.join("config.toml");
    let Ok(content) = std::fs::read_to_string(&config_path) else {
        return vec![];
    };
    let Ok(config): Result<toml::Value, _> = toml::from_str(&content) else {
        return vec![];
    };
    config
        .get("models")
        .and_then(|m| m.get("providers"))
        .and_then(|p| p.as_table())
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default()
}
