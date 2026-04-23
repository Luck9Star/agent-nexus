//! `agent-nexus config` -- read/write config.toml values.

use std::path::Path;

use anyhow::{Context, Result};

use crate::commands;
use crate::output::OutputFormatter;

/// Run `config get <key>` command.
///
/// Reads a dot-separated key from config.toml (e.g., `models.default`).
pub fn run_get(key: &str, output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let path = root.join("config.toml");
    if !path.exists() {
        anyhow::bail!("config.toml not found. Run `agent-nexus init` first.");
    }

    get_from_path(&path, key, output)
}

/// Get a config value from a specific config.toml path (testable without cwd).
fn get_from_path(path: &Path, key: &str, output: &OutputFormatter) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;

    let value = lookup_toml_key(&config, key).with_context(|| {
        format!("Key '{}' not found in config.toml", key)
    })?;

    if output.is_json() {
        let obj = serde_json::json!({
            "key": key,
            "value": value.to_string(),
        });
        println!("{}", serde_json::to_string_pretty(&obj).expect("TOML values are always JSON-serializable"));
    } else {
        println!("{}", format_toml_value(value));
    }

    Ok(())
}

/// Run `config set <key> <value>` command.
///
/// Writes a dot-separated key to config.toml, preserving other values.
pub fn run_set(key: &str, value: &str, output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let path = root.join("config.toml");
    if !path.exists() {
        anyhow::bail!("config.toml not found. Run `agent-nexus init` first.");
    }

    set_in_path(&path, key, value, output)
}

/// Set a config value in a specific config.toml path (testable without cwd).
fn set_in_path(path: &Path, key: &str, value: &str, output: &OutputFormatter) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let mut config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;

    set_toml_key(&mut config, key, value).with_context(|| format!("Cannot set key '{}'", key))?;

    let new_content = toml::to_string_pretty(&config)?;

    // Atomic write: write to .tmp then rename to prevent corruption on crash
    let tmp_path = path.with_extension("toml.tmp");
    std::fs::write(&tmp_path, &new_content)?;
    std::fs::rename(&tmp_path, path)?;

    output.success(&format!("Set {} = {}", key, value));
    Ok(())
}

/// Run `config show` command -- display configuration overview.
pub fn run_show(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let config_path = root.join("config.toml");

    if output.is_json() {
        let mut obj = serde_json::Map::new();
        obj.insert("config_dir".into(), serde_json::Value::String(root.display().to_string()));

        if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            let config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;
            if let Some(models) = config.get("models") {
                if let Some(default) = models.get("default").and_then(|v| v.as_str()) {
                    obj.insert("default_model".into(), serde_json::Value::String(default.to_string()));
                }
                if let Some(providers) = models.get("providers").and_then(|v| v.as_table()) {
                    let provider_names: Vec<serde_json::Value> = providers.keys().map(|k| serde_json::Value::String(k.clone())).collect();
                    obj.insert("providers".into(), serde_json::Value::Array(provider_names));
                }
            }
        } else {
            obj.insert("default_model".into(), serde_json::Value::String("openai:gpt-4o".into()));
        }
        println!("{}", serde_json::to_string_pretty(&obj).expect("JSON serialization is infallible"));
    } else {
        println!("Config dir: {}", root.display());

        if config_path.exists() {
            let content = std::fs::read_to_string(&config_path)?;
            let config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;

            if let Some(default) = config.get("models").and_then(|m| m.get("default")).and_then(|v| v.as_str()) {
                println!("Default model: {}", default);
            } else {
                println!("Default model: openai:gpt-4o (built-in default)");
            }

            // Python path
            if let Some(python) = config.get("runtime").and_then(|r| r.get("python_path")).and_then(|v| v.as_str()) {
                println!("Python path: {}", python);
            }

            // uv path
            if let Some(uv) = config.get("runtime").and_then(|r| r.get("uv_path")).and_then(|v| v.as_str()) {
                println!("uv path: {}", uv);
            }

            // Providers
            if let Some(providers) = config.get("models").and_then(|m| m.get("providers")).and_then(|v| v.as_table()) {
                println!("Providers:");
                for (name, _cfg) in providers {
                    println!("  - {}", name);
                }
            }
        } else {
            println!("No config.toml found");
        }
    }

    Ok(())
}

/// Look up a dot-separated key in a TOML table.
fn lookup_toml_key<'a>(table: &'a toml::Value, key: &str) -> Option<&'a toml::Value> {
    let parts: Vec<&str> = key.split('.').collect();
    let mut current = table;
    for part in parts {
        current = current.as_table()?.get(part)?;
    }
    Some(current)
}

/// Set a dot-separated key in a TOML table.
fn set_toml_key(table: &mut toml::Value, key: &str, value: &str) -> Result<()> {
    let parts: Vec<&str> = key.split('.').collect();
    if parts.is_empty() {
        anyhow::bail!("Empty key");
    }

    // Navigate to the parent table
    let mut current = table;
    for part in &parts[..parts.len() - 1] {
        current = current
            .as_table_mut()
            .context("Expected a table")?
            .entry(part.to_string())
            .or_insert_with(|| toml::Value::Table(toml::map::Map::new()));
    }

    let last_key = parts[parts.len() - 1];
    let tbl = current.as_table_mut().context("Expected a table")?;

    // Try to parse the value as a TOML value (preserves type)
    let parsed: toml::Value = if value == "true" {
        toml::Value::Boolean(true)
    } else if value == "false" {
        toml::Value::Boolean(false)
    } else if let Ok(i) = value.parse::<i64>() {
        toml::Value::Integer(i)
    } else if let Ok(f) = value.parse::<f64>() {
        toml::Value::Float(f)
    } else {
        toml::Value::String(value.to_string())
    };

    tbl.insert(last_key.to_string(), parsed);
    Ok(())
}

/// Format a TOML value for display.
fn format_toml_value(value: &toml::Value) -> String {
    match value {
        toml::Value::String(s) => s.clone(),
        toml::Value::Integer(i) => i.to_string(),
        toml::Value::Float(f) => f.to_string(),
        toml::Value::Boolean(b) => b.to_string(),
        other => other.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn get_existing_key() {
        let dir = tempfile::tempdir().unwrap();
        let config = r#"[models]
default = "openai:gpt-4o"
"#;
        let path = dir.path().join("config.toml");
        std::fs::write(&path, config).unwrap();

        let output = OutputFormatter::new(true, false);
        get_from_path(&path, "models.default", &output).unwrap();
    }

    #[test]
    fn get_missing_key_errors() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[models]\ndefault = \"x\"\n").unwrap();

        let output = OutputFormatter::new(true, false);
        let result = get_from_path(&path, "nonexistent.key", &output);
        assert!(result.is_err());
    }

    #[test]
    fn set_and_get_key() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[models]\ndefault = \"old\"\n").unwrap();

        let output = OutputFormatter::new(true, false);
        set_in_path(&path, "models.default", "new-model", &output).unwrap();

        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("new-model"));
    }

    #[test]
    fn set_preserves_type() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[section]\n").unwrap();

        let output = OutputFormatter::new(true, false);
        set_in_path(&path, "section.int_val", "42", &output).unwrap();

        let content = std::fs::read_to_string(&path).unwrap();
        assert!(content.contains("int_val = 42"));
    }
}
