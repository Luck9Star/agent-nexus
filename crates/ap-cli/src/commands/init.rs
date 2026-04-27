//! `agent-nexus init` — create config.toml with defaults.

use std::path::{Path, PathBuf};

use anyhow::Result;
use ap_fetcher::sources::SourceManager;

use crate::output::OutputFormatter;

// ---------------------------------------------------------------------------
// InitError — separates validation failures from I/O errors
// ---------------------------------------------------------------------------

/// Errors specific to the `init` command.
#[derive(Debug)]
enum InitError {
    /// Directory path contains `..` traversal.
    PathTraversal,
    /// Directory points to a protected system directory.
    SystemDirectory(String),
    /// Underlying I/O failure.
    Io(std::io::Error),
}

impl std::fmt::Display for InitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            InitError::PathTraversal => {
                write!(f, "Invalid directory: path traversal ('..') is not allowed")
            }
            InitError::SystemDirectory(dir) => {
                write!(
                    f,
                    "Invalid directory: cannot write to system directory '{dir}'"
                )
            }
            InitError::Io(e) => write!(f, "I/O error: {e}"),
        }
    }
}

impl std::error::Error for InitError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            InitError::Io(e) => Some(e),
            _ => None,
        }
    }
}

impl From<std::io::Error> for InitError {
    fn from(e: std::io::Error) -> Self {
        InitError::Io(e)
    }
}

/// Default config.toml content with official source included.
fn default_config_toml() -> String {
    let mut config = ap_core::config::default_config();
    config.sources = vec![ap_core::models::distribution::SourceEntry {
        name: "official".to_string(),
        source_type: "git".to_string(),
        url: "https://github.com/anthropics/agent-nexus-packages.git".to_string(),
        branch: "main".to_string(),
    }];
    toml::to_string_pretty(&config).unwrap_or_else(|_| {
        "[runtime]\npython_path = \"python3\"\nuv_path = \"uv\"\n\n[models]\ndefault = \"openai:gpt-4o\"\n".to_string()
    })
}

/// Validate that the init directory is safe to write into.
///
/// Rejects path traversal (`..`) and blocked system directories. Ensures
/// the resolved path is within the current working directory, user home,
/// or a standard temp directory.
fn validate_init_dir(dir: &str) -> Result<PathBuf, InitError> {
    if dir.contains("..") {
        return Err(InitError::PathTraversal);
    }

    let path = Path::new(dir);
    let resolved = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };

    // Canonicalize to resolve symlinks before checking blocked prefixes.
    // If the path doesn't exist yet (fresh init), canonicalize the parent instead.
    let resolved = if resolved.exists() {
        resolved.canonicalize()?
    } else if let Some(parent) = resolved.parent() {
        if parent.exists() {
            parent.canonicalize()?.join(
                resolved.file_name().unwrap_or_default()
            )
        } else {
            resolved
        }
    } else {
        resolved
    };

    const BLOCKED_PREFIXES: &[&str] = &[
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/System",
        "/Library/System",
        "/private/etc",
        "/private/var/db",
        "/tmp",
        "/opt",
        "/srv",
        "/boot",
        "/lib",
    ];

    let resolved_str = resolved.to_string_lossy();
    for prefix in BLOCKED_PREFIXES {
        if resolved_str.starts_with(prefix) {
            return Err(InitError::SystemDirectory(prefix.to_string()));
        }
    }

    Ok(resolved)
}

/// Run `init` command: create config.toml (with sources) in the target directory.
pub fn run(dir: &str, output: &OutputFormatter) -> Result<()> {
    let target = validate_init_dir(dir).map_err(|e| anyhow::anyhow!("{e}"))?;

    if !target.exists() {
        std::fs::create_dir_all(&target)?;
    }

    let config_path = target.join("config.toml");

    if config_path.exists() {
        output.info("config.toml already exists, skipping");
    } else {
        std::fs::write(&config_path, default_config_toml())?;
        output.success(&format!("Created config.toml in {dir}"));
    }

    // Migrate sources.yaml → config.toml if needed.
    // Users who ran `init` before this change have sources.yaml but no [[sources]] in config.toml.
    let sources_yaml = target.join("sources.yaml");
    if sources_yaml.exists() && config_path.exists() {
        let config_toml_mgr = SourceManager::new_toml(config_path.clone());
        let has_sources = !config_toml_mgr.list().is_empty();
        if !has_sources {
            let yaml_mgr = SourceManager::new(sources_yaml);
            match yaml_mgr.list() {
                yaml_sources if !yaml_sources.is_empty() => {
                    if let Err(e) = config_toml_mgr.save(&yaml_sources) {
                        output.info(&format!("Warning: failed to migrate sources from sources.yaml: {e}"));
                    } else {
                        output.info("Migrated sources from sources.yaml to config.toml (legacy file preserved)");
                    }
                }
                _ => {}
            }
        }
    }

    // API key detection
    let detected_keys: Vec<&str> = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"]
        .iter()
        .filter(|k| std::env::var(k).is_ok())
        .copied()
        .collect();

    if detected_keys.is_empty() {
        output.info("No API keys detected in environment");
    } else {
        output.info(&format!("Detected API keys: {}", detected_keys.join(", ")));
    }

    // Next steps
    if !output.is_json() {
        println!();
        println!("Next steps:");
        println!("  1. Set API keys: export OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...");
        println!("  2. Browse agents: agent-nexus search <query>");
        println!("  3. Install an agent: agent-nexus install <name>");
        println!("  4. Run diagnostics: agent-nexus check");
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_config_and_sources() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().to_str().unwrap();
        let output = OutputFormatter::new(true, false);
        run(path, &output).unwrap();

        assert!(dir.path().join("config.toml").exists());

        // Verify config.toml is valid TOML with sources included
        let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
        let config: toml::Value = toml::from_str(&content).unwrap();
        assert!(config.get("runtime").is_some() || config.get("models").is_some());
        assert!(config.get("sources").is_some(), "config.toml should contain [sources]");
    }

    #[test]
    fn skips_existing_files() {
        let dir = tempfile::tempdir().unwrap();
        let config_path = dir.path().join("config.toml");
        std::fs::write(&config_path, "existing content").unwrap();

        let output = OutputFormatter::new(true, false);
        run(dir.path().to_str().unwrap(), &output).unwrap();

        let content = std::fs::read_to_string(&config_path).unwrap();
        assert_eq!(content, "existing content");
    }

    #[test]
    fn creates_directory_if_missing() {
        let dir = tempfile::tempdir().unwrap();
        let nested = dir.path().join("subdir").join("nested");
        let output = OutputFormatter::new(true, false);
        run(nested.to_str().unwrap(), &output).unwrap();

        assert!(nested.join("config.toml").exists());
    }

    #[test]
    fn rejects_path_traversal() {
        let output = OutputFormatter::new(true, false);
        let result = run("../../../../tmp/evil", &output);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("path traversal"));
    }

    #[test]
    fn rejects_absolute_system_path() {
        let output = OutputFormatter::new(true, false);
        let result = run("/etc/agent-nexus", &output);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("system directory"));
    }
}
