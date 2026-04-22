//! `agent-nexus init` — create config.toml with defaults and sources.yaml.

use std::path::Path;

use anyhow::Result;

use crate::output::OutputFormatter;

/// Default config.toml content.
fn default_config_toml() -> String {
    let config = ap_core::config::default_config();
    toml::to_string_pretty(&config).unwrap_or_else(|_| {
        "[runtime]\npython_path = \"python3\"\nuv_path = \"uv\"\n\n[models]\ndefault = \"openai:gpt-4o\"\n".to_string()
    })
}

/// Default sources.yaml content (empty sources list).
fn default_sources_yaml() -> String {
    "sources: []\n".to_string()
}

/// Run `init` command: create config.toml and sources.yaml in the target directory.
pub fn run(dir: &str, output: &OutputFormatter) -> Result<()> {
    let target = Path::new(dir);

    if !target.exists() {
        std::fs::create_dir_all(target)?;
    }

    let config_path = target.join("config.toml");
    let sources_path = target.join("sources.yaml");

    if config_path.exists() {
        output.info("config.toml already exists, skipping");
    } else {
        std::fs::write(&config_path, default_config_toml())?;
        output.success(&format!("Created config.toml in {}", dir));
    }

    if sources_path.exists() {
        output.info("sources.yaml already exists, skipping");
    } else {
        std::fs::write(&sources_path, default_sources_yaml())?;
        output.success(&format!("Created sources.yaml in {}", dir));
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
        assert!(dir.path().join("sources.yaml").exists());

        // Verify config.toml is valid TOML
        let content = std::fs::read_to_string(dir.path().join("config.toml")).unwrap();
        let config: toml::Value = toml::from_str(&content).unwrap();
        assert!(config.get("runtime").is_some() || config.get("models").is_some());
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
}
