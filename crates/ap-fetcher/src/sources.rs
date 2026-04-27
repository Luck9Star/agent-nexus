//! Source manager for agent package sources.
//!
//! Supports two storage backends:
//! - **YAML** (`sources.yaml`) — legacy, deprecated
//! - **TOML** (`config.toml [sources]`) — primary, aligns with Python platform

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use thiserror::Error;
use tracing::{debug, warn};

use ap_core::models::distribution::SourceEntry;

use crate::advisory_lock::FileLock;

/// Errors from source management operations.
#[derive(Debug, Error)]
pub enum SourceError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("YAML parse error: {0}")]
    Yaml(#[from] serde_yml::Error),
    #[error("TOML parse error: {0}")]
    TomlDe(#[from] toml::de::Error),
    #[error("TOML serialize error: {0}")]
    TomlSer(#[from] toml::ser::Error),
    #[error("source not found: {0}")]
    NotFound(String),
    #[error("validation error: {0}")]
    Validation(String),
}

/// Internal wrapper for the `sources:` map format in YAML.
#[derive(Debug, Serialize, Deserialize)]
struct SourcesYaml {
    sources: Vec<SourceEntry>,
}

/// Storage backend for source entries.
#[derive(Debug, Clone, PartialEq)]
enum StorageMode {
    /// Read/write from `config.toml` `[sources]` section.
    Toml,
    /// Legacy: read/write from `sources.yaml`.
    Yaml,
}

/// Manages agent package sources — supports config.toml and legacy sources.yaml.
#[derive(Debug)]
pub struct SourceManager {
    path: PathBuf,
    mode: StorageMode,
}

impl SourceManager {
    /// Create a new source manager pointing to a `sources.yaml` path (legacy mode).
    #[must_use]
    pub fn new(path: PathBuf) -> Self {
        Self { path, mode: StorageMode::Yaml }
    }

    /// Create a new source manager pointing to a `config.toml` path.
    ///
    /// Sources are read from and written to the `[sources]` section of the
    /// TOML file. Other sections (runtime, models, etc.) are preserved.
    #[must_use]
    pub fn new_toml(config_path: PathBuf) -> Self {
        Self { path: config_path, mode: StorageMode::Toml }
    }

    /// Returns the path to the advisory lock file (sibling of the storage file).
    fn lock_path(&self) -> PathBuf {
        self.path.with_extension("lock")
    }

    /// Static: parse a YAML string into a list of `SourceEntry`.
    ///
    /// Accepts both `{sources: [...]}` (wrapped) and bare `[...]` formats.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn parse(yaml: &str) -> Result<Vec<SourceEntry>, SourceError> {
        let trimmed = yaml.trim();
        if trimmed.is_empty() {
            return Ok(vec![]);
        }

        // Try wrapped format first: { sources: [...] }
        match serde_yml::from_str::<SourcesYaml>(trimmed) {
            Ok(wrapped) => {
                debug!("Parsed sources as wrapped format");
                return Ok(wrapped.sources);
            }
            Err(e) => {
                debug!("Wrapped format parse failed ({}), trying bare array", e);
            }
        }

        // Try bare array format: [...]
        let entries = serde_yml::from_str::<Vec<SourceEntry>>(trimmed)?;
        debug!("Parsed sources as bare array format, count={}", entries.len());
        Ok(entries)
    }

    /// Load sources from the configured backend. Returns empty vec if not found.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn load(&self) -> Result<Vec<SourceEntry>, SourceError> {
        match self.mode {
            StorageMode::Toml => self.load_from_toml(),
            StorageMode::Yaml => self.load_from_yaml(),
        }
    }

    fn load_from_yaml(&self) -> Result<Vec<SourceEntry>, SourceError> {
        if !self.path.exists() {
            debug!("Sources file not found, returning empty: {:?}", self.path);
            return Ok(vec![]);
        }
        let contents = std::fs::read_to_string(&self.path)?;
        let entries = Self::parse(&contents)?;
        for entry in &entries {
            entry.validate().map_err(SourceError::Validation)?;
        }
        Ok(entries)
    }

    fn load_from_toml(&self) -> Result<Vec<SourceEntry>, SourceError> {
        if !self.path.exists() {
            debug!("Config file not found, returning empty: {:?}", self.path);
            return Ok(vec![]);
        }
        let contents = std::fs::read_to_string(&self.path)?;
        let value: toml::Value = toml::from_str(&contents)?;
        let sources = value
            .get("sources")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        let mut entries = Vec::new();
        for item in sources {
            let item_str = toml::to_string(&item).unwrap_or_default();
            let entry: SourceEntry = toml::from_str(&item_str).unwrap_or_else(|e| {
                warn!("Skipping invalid source entry: {e}");
                SourceEntry {
                    name: String::new(),
                    source_type: "git".to_string(),
                    url: String::new(),
                    branch: "main".to_string(),
                }
            });
            if entry.name.is_empty() {
                continue;
            }
            entry.validate().map_err(SourceError::Validation)?;
            entries.push(entry);
        }
        Ok(entries)
    }

    /// Atomically write sources to the configured backend.
    /// Acquires an advisory file lock to prevent TOCTOU races with concurrent add/remove.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn save(&self, sources: &[SourceEntry]) -> Result<(), SourceError> {
        let _lock = FileLock::acquire_exclusive(&self.lock_path())?;
        match self.mode {
            StorageMode::Toml => self.save_to_toml(sources),
            StorageMode::Yaml => self.save_to_yaml(sources),
        }
    }

    /// Save sources to sources.yaml. Caller must hold the file lock if concurrent access is possible.
    fn save_to_yaml(&self, sources: &[SourceEntry]) -> Result<(), SourceError> {
        let wrapped = SourcesYaml {
            sources: sources.to_vec(),
        };
        let yaml = serde_yml::to_string(&wrapped)?;
        atomic_write(&self.path, &yaml)
    }

    /// Save sources to config.toml. Caller must hold the file lock if concurrent access is possible.
    fn save_to_toml(&self, sources: &[SourceEntry]) -> Result<(), SourceError> {
        // Read existing config.toml, update [sources], write back
        let mut config: toml::Value = if self.path.exists() {
            let contents = std::fs::read_to_string(&self.path)?;
            toml::from_str(&contents).unwrap_or_else(|_| toml::Value::Table(toml::map::Map::new()))
        } else {
            toml::Value::Table(toml::map::Map::new())
        };

        // Serialize sources as TOML array of inline tables
        let sources_array: toml::Value = toml::Value::Array(
            sources.iter().map(|s| {
                let toml_str = toml::to_string(s).unwrap_or_default();
                toml::from_str::<toml::Value>(&toml_str).unwrap_or(toml::Value::Table(toml::map::Map::new()))
            }).collect()
        );

        if let Some(table) = config.as_table_mut() {
            table.insert("sources".to_string(), sources_array);
        }

        let new_content = toml::to_string_pretty(&config)?;
        atomic_write(&self.path, &new_content)
    }

    /// Convenience: load sources, or return empty vec on any error.
    #[must_use]
    pub fn list(&self) -> Vec<SourceEntry> {
        self.load().unwrap_or_default()
    }

    /// Add or update a source entry (upsert semantics).
    ///
    /// Lock is acquired here; `save_to_toml`/`save_to_yaml` is called directly
    /// (not via `save()`) to avoid double-locking.
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn add(&self, entry: SourceEntry) -> Result<(), SourceError> {
        entry.validate().map_err(SourceError::Validation)?;

        let _lock = FileLock::acquire_exclusive(&self.lock_path())?;
        let mut sources = self.load()?;
        sources.retain(|s| s.name != entry.name);
        debug!("Adding source: {}", entry.name);
        sources.push(entry);
        match self.mode {
            StorageMode::Toml => self.save_to_toml(&sources),
            StorageMode::Yaml => self.save_to_yaml(&sources),
        }
    }

    /// Remove a source by name.
    ///
    /// Lock is acquired here; `save_to_toml`/`save_to_yaml` is called directly
    /// (not via `save()`) to avoid double-locking.
    ///
    /// # Errors
    /// Returns an error if the source is not found.
    pub fn remove(&self, name: &str) -> Result<(), SourceError> {
        let _lock = FileLock::acquire_exclusive(&self.lock_path())?;
        let mut sources = self.load()?;
        let original_len = sources.len();
        sources.retain(|s| s.name != name);
        if sources.len() == original_len {
            return Err(SourceError::NotFound(name.to_string()));
        }
        debug!("Removed source: {}", name);
        match self.mode {
            StorageMode::Toml => self.save_to_toml(&sources),
            StorageMode::Yaml => self.save_to_yaml(&sources),
        }
    }
}

/// Atomic write: write to a unique tmp file, then rename.
fn atomic_write(path: &PathBuf, content: &str) -> Result<(), SourceError> {
    static ATOMIC_WRITE_COUNTER: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
    let count = ATOMIC_WRITE_COUNTER.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    let ext = path.extension().and_then(|e| e.to_str()).unwrap_or("tmp");
    let tmp_path = path.with_extension(format!("{ext}.tmp.{}.{}", std::process::id(), count));
    std::fs::write(&tmp_path, content)?;
    std::fs::rename(&tmp_path, path)?;
    debug!("Atomic write to {:?}", path);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_entry(name: &str, url: &str) -> SourceEntry {
        SourceEntry {
            name: name.to_string(),
            source_type: "git".to_string(),
            url: url.to_string(),
            branch: "main".to_string(),
        }
    }

    // --- YAML mode tests (legacy) ---

    #[test]
    fn parse_wrapped_format() {
        let yaml = r#"
sources:
  - name: official
    type: git
    url: https://github.com/example/agents
    branch: main
  - name: private
    type: git
    url: https://gitlab.com/example/agents
    branch: dev
"#;
        let entries = SourceManager::parse(yaml).unwrap();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].name, "official");
        assert_eq!(entries[1].name, "private");
    }

    #[test]
    fn parse_bare_array_format() {
        let yaml = r#"
- name: official
  type: git
  url: https://github.com/example/agents
  branch: main
"#;
        let entries = SourceManager::parse(yaml).unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "official");
    }

    #[test]
    fn parse_empty_string() {
        let entries = SourceManager::parse("").unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn yaml_load_missing_file_returns_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);
        let entries = mgr.load().unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn yaml_add_and_list_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.add(make_entry("private", "https://gitlab.com/example/agents")).unwrap();

        let entries = mgr.list();
        assert_eq!(entries.len(), 2);
        assert_eq!(entries[0].name, "official");
        assert_eq!(entries[1].name, "private");
    }

    #[test]
    fn yaml_add_upserts_existing_name() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.add(make_entry("official", "https://github.com/other/repo")).unwrap();

        let entries = mgr.list();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].url, "https://github.com/other/repo");
    }

    #[test]
    fn yaml_remove_existing_source() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.remove("official").unwrap();

        let entries = mgr.list();
        assert!(entries.is_empty());
    }

    #[test]
    fn yaml_remove_nonexistent_source_errors() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        let result = mgr.remove("nonexistent");
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(err_msg.contains("not found"));
    }

    #[test]
    fn yaml_add_validates_git_url() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        let bad_entry = SourceEntry {
            name: "bad".to_string(),
            source_type: "git".to_string(),
            url: String::new(),
            branch: "main".to_string(),
        };
        let result = mgr.add(bad_entry);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("url is empty"));
    }

    #[test]
    fn yaml_load_validates_entries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");

        let yaml = r#"
sources:
  - name: bad
    type: git
    url: ""
    branch: main
"#;
        std::fs::write(&path, yaml).unwrap();

        let mgr = SourceManager::new(path);
        let result = mgr.load();
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("url is empty"));
    }

    #[test]
    fn yaml_save_produces_wrapped_format() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path.clone());

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();

        let contents = std::fs::read_to_string(&path).unwrap();
        assert!(contents.contains("sources:"));
        assert!(contents.contains("name: official"));
    }

    // --- TOML mode tests (primary) ---

    #[test]
    fn toml_load_missing_file_returns_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mgr = SourceManager::new_toml(path);
        let entries = mgr.load().unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn toml_load_reads_sources_section() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, r#"
[runtime]
python_path = "python3"

[models]
default = "openai:gpt-4o"

[[sources]]
name = "official"
type = "git"
url = "https://github.com/example/agents"
branch = "main"
"#).unwrap();

        let mgr = SourceManager::new_toml(path);
        let entries = mgr.list();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "official");
        assert_eq!(entries[0].url, "https://github.com/example/agents");
    }

    #[test]
    fn toml_add_and_list_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

        let mgr = SourceManager::new_toml(path);
        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.add(make_entry("private", "https://gitlab.com/example/agents")).unwrap();

        let entries = mgr.list();
        assert_eq!(entries.len(), 2);

        // Verify other sections preserved
        let content = std::fs::read_to_string(&dir.path().join("config.toml")).unwrap();
        assert!(content.contains("[models]"));
        assert!(content.contains("openai:gpt-4o"));
    }

    #[test]
    fn toml_remove_source() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "[models]\ndefault = \"openai:gpt-4o\"\n").unwrap();

        let mgr = SourceManager::new_toml(path);
        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.remove("official").unwrap();

        let entries = mgr.list();
        assert!(entries.is_empty());
    }
}
