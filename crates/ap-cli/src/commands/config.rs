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
        format!("Key '{key}' not found in config.toml")
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

    set_toml_key(&mut config, key, value).with_context(|| format!("Cannot set key '{key}'"))?;

    let new_content = toml::to_string_pretty(&config)?;

    // Atomic write: write to PID-scoped tmp then rename to prevent corruption on crash.
    // Hidden prefix (.config.tmp) keeps temp files invisible to casual users and VCS.
    let tmp_path = path.with_file_name(format!(".config.tmp.{}", std::process::id()));
    // Clean stale temp from previous crash
    let _ = std::fs::remove_file(&tmp_path);
    std::fs::write(&tmp_path, &new_content)?;
    std::fs::rename(&tmp_path, path)?;

    output.success(&format!("Set {key} = {value}"));
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
                println!("Default model: {default}");
            } else {
                println!("Default model: openai:gpt-4o (built-in default)");
            }

            // Python path
            if let Some(python) = config.get("runtime").and_then(|r| r.get("python_path")).and_then(|v| v.as_str()) {
                println!("Python path: {python}");
            }

            // uv path
            if let Some(uv) = config.get("runtime").and_then(|r| r.get("uv_path")).and_then(|v| v.as_str()) {
                println!("uv path: {uv}");
            }

            // Providers
            if let Some(providers) = config.get("models").and_then(|m| m.get("providers")).and_then(|v| v.as_table()) {
                println!("Providers:");
                for (name, _cfg) in providers {
                    println!("  - {name}");
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

/// Check if the given command name matches a known safe editor.
fn is_known_editor(name: &str) -> bool {
    const KNOWN_EDITORS: &[&str] = &[
        "vi", "vim", "nvim", "neovim", "nano", "pico", "emacs", "emacsclient",
        "code", "codium", "subl", "atom", "gedit", "kate", "micro", "helix",
        "hx", "ed", "joe", "mcedit", "tilde",
    ];
    KNOWN_EDITORS.contains(&name)
}

/// Run `config edit` command -- open config.toml in $EDITOR.
pub fn run_edit(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let path = root.join("config.toml");
    if !path.exists() {
        anyhow::bail!("config.toml not found. Run `agent-nexus init` first.");
    }

    // EDITOR is user-controlled input; validate aggressively.
    let editor = std::env::var("EDITOR")
        .ok()
        .filter(|e| !e.is_empty() && !e.contains('\0') && !e.contains(".."))
        .filter(|e| {
            // Whitelist known editor basenames to prevent command injection.
            // Canonicalize the path if it looks like an absolute/relative path.
            if e.contains('/') {
                // Path-based editor — verify it resolves to an existing file
                let path = std::path::Path::new(e);
                if let Ok(canonical) = path.canonicalize() {
                    let basename = canonical
                        .file_name()
                        .map(|f| f.to_string_lossy().to_string())
                        .unwrap_or_default();
                    is_known_editor(&basename)
                } else {
                    false
                }
            } else {
                // Bare command name (e.g. "vim", "nano")
                is_known_editor(e)
            }
        })
        .unwrap_or_else(|| "vi".to_string());
    let status = std::process::Command::new(&editor)
        .arg(&path)
        .status()
        .with_context(|| format!("Failed to launch editor '{editor}'"))?;

    if !status.success() {
        anyhow::bail!("Editor exited with error");
    }
    output.success("Config edited.");
    Ok(())
}

/// Run `config validate` command -- check config.toml is well-formed.
pub fn run_validate(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let path = root.join("config.toml");
    if !path.exists() {
        anyhow::bail!("config.toml not found. Run `agent-nexus init` first.");
    }

    let content = std::fs::read_to_string(&path)?;
    let _config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;
    output.success("config.toml is valid.");
    Ok(())
}

/// Run `config providers` command -- list configured providers and API key status.
pub fn run_providers(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    let path = root.join("config.toml");

    if !path.exists() {
        output.info("No config.toml found. No providers configured.");
        return Ok(());
    }

    let content = std::fs::read_to_string(&path)?;
    let config: toml::Value = toml::from_str(&content).with_context(|| "Invalid config.toml")?;

    let providers = config
        .get("models")
        .and_then(|m| m.get("providers"))
        .and_then(|v| v.as_table());

    let env_keys = [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
        ("dashscope", "DASHSCOPE_API_KEY"),
        ("ollama", "OLLAMA_HOST"),
    ];

    if output.is_json() {
        let mut arr = Vec::new();
        if let Some(tbl) = providers {
            for (name, _cfg) in tbl {
                let has_key = env_keys.iter().any(|(prefix, key)| {
                    name.starts_with(prefix) && std::env::var(key).is_ok()
                });
                arr.push(serde_json::json!({
                    "name": name,
                    "api_key_set": has_key,
                }));
            }
        }
        // Also check env-only providers
        for (prefix, key) in &env_keys {
            if providers.is_none_or(|t| !t.keys().any(|k| k.starts_with(prefix))) {
                arr.push(serde_json::json!({
                    "name": prefix,
                    "api_key_set": std::env::var(key).is_ok(),
                    "source": "env",
                }));
            }
        }
        output.data(&arr);
    } else {
        if let Some(tbl) = providers {
            println!("Configured providers:");
            for (name, _cfg) in tbl {
                let has_key = env_keys.iter().any(|(prefix, key)| {
                    name.starts_with(prefix) && std::env::var(key).is_ok()
                });
                let status = if has_key { "API key set" } else { "no API key" };
                println!("  {name}: {status}");
            }
        } else {
            output.info("No providers configured in config.toml.");
        }

        // Show env-only keys
        for (prefix, key) in &env_keys {
            let set = std::env::var(key).is_ok();
            if set {
                println!("  {prefix} (env): API key set");
            }
        }
    }

    Ok(())
}

/// Run `config path` command -- print config directory path.
pub fn run_path(output: &OutputFormatter) -> Result<()> {
    let root = commands::find_project_root(&std::env::current_dir()?);
    if output.is_json() {
        output.data(&serde_json::json!({ "config_dir": root.display().to_string() }));
    } else {
        println!("{}", root.display());
    }
    Ok(())
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
