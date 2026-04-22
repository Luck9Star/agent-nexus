//! Source manager for `sources.yaml` — manages the list of Git repos providing agents.

use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use thiserror::Error;
use tracing::debug;

use ap_core::models::distribution::SourceEntry;

/// Errors from source management operations.
#[derive(Debug, Error)]
pub enum SourceError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("YAML parse error: {0}")]
    Yaml(#[from] serde_yaml::Error),
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

/// Manages `sources.yaml` — the list of Git repos that provide agents.
#[derive(Debug)]
pub struct SourceManager {
    path: PathBuf,
}

impl SourceManager {
    /// Create a new source manager pointing to the given `sources.yaml` path.
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    /// Static: parse a YAML string into a list of `SourceEntry`.
    ///
    /// Accepts both `{sources: [...]}` (wrapped) and bare `[...]` formats.
    pub fn parse(yaml: &str) -> Result<Vec<SourceEntry>, SourceError> {
        let trimmed = yaml.trim();
        if trimmed.is_empty() {
            return Ok(vec![]);
        }

        // Try wrapped format first: { sources: [...] }
        if let Ok(wrapped) = serde_yaml::from_str::<SourcesYaml>(trimmed) {
            debug!("Parsed sources as wrapped format");
            return Ok(wrapped.sources);
        }

        // Try bare array format: [...]
        let entries = serde_yaml::from_str::<Vec<SourceEntry>>(trimmed)?;
        debug!("Parsed sources as bare array format, count={}", entries.len());
        Ok(entries)
    }

    /// Load sources from file. Returns empty vec if the file doesn't exist.
    pub fn load(&self) -> Result<Vec<SourceEntry>, SourceError> {
        if !self.path.exists() {
            debug!("Sources file not found, returning empty: {:?}", self.path);
            return Ok(vec![]);
        }
        let contents = std::fs::read_to_string(&self.path)?;
        let entries = Self::parse(&contents)?;
        // Validate each entry
        for entry in &entries {
            entry
                .validate()
                .map_err(SourceError::Validation)?;
        }
        Ok(entries)
    }

    /// Atomically write sources to the YAML file (write to `.tmp`, then rename).
    pub fn save(&self, sources: &[SourceEntry]) -> Result<(), SourceError> {
        let wrapped = SourcesYaml {
            sources: sources.to_vec(),
        };
        let yaml = serde_yaml::to_string(&wrapped)?;

        // Atomic write: write to tmp file, then rename
        let tmp_path = self.path.with_extension("yaml.tmp");
        std::fs::write(&tmp_path, &yaml)?;
        std::fs::rename(&tmp_path, &self.path)?;
        debug!("Saved {} sources to {:?}", sources.len(), self.path);
        Ok(())
    }

    /// Convenience: load sources, or return empty vec on any error.
    pub fn list(&self) -> Vec<SourceEntry> {
        self.load().unwrap_or_default()
    }

    /// Add or update a source entry (upsert semantics).
    ///
    /// Validates the entry, loads existing sources, removes any existing entry
    /// with the same name (matching Python's upsert behavior), appends, and saves.
    pub fn add(&self, entry: SourceEntry) -> Result<(), SourceError> {
        entry
            .validate()
            .map_err(SourceError::Validation)?;

        let mut sources = self.load()?;
        // Upsert: remove existing entry with same name (matches Python behavior)
        sources.retain(|s| s.name != entry.name);
        debug!("Adding source: {}", entry.name);
        sources.push(entry);
        self.save(&sources)
    }

    /// Remove a source by name.
    ///
    /// Returns an error if the source is not found.
    pub fn remove(&self, name: &str) -> Result<(), SourceError> {
        let mut sources = self.load()?;
        let original_len = sources.len();
        sources.retain(|s| s.name != name);
        if sources.len() == original_len {
            return Err(SourceError::NotFound(name.to_string()));
        }
        debug!("Removed source: {}", name);
        self.save(&sources)
    }
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
    fn load_missing_file_returns_empty() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);
        let entries = mgr.load().unwrap();
        assert!(entries.is_empty());
    }

    #[test]
    fn add_and_list_roundtrip() {
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
    fn add_upserts_existing_name() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        // Upsert: adding same name replaces the entry
        mgr.add(make_entry("official", "https://github.com/other/repo")).unwrap();

        let entries = mgr.list();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].url, "https://github.com/other/repo");
    }

    #[test]
    fn remove_existing_source() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();
        mgr.remove("official").unwrap();

        let entries = mgr.list();
        assert!(entries.is_empty());
    }

    #[test]
    fn remove_nonexistent_source_errors() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        let result = mgr.remove("nonexistent");
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(err_msg.contains("not found"));
    }

    #[test]
    fn add_validates_git_url() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path);

        let bad_entry = SourceEntry {
            name: "bad".to_string(),
            source_type: "git".to_string(),
            url: "".to_string(),
            branch: "main".to_string(),
        };
        let result = mgr.add(bad_entry);
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("url is empty"));
    }

    #[test]
    fn load_validates_entries() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");

        // Write a YAML with an invalid entry (git type but empty url)
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
    fn save_produces_wrapped_format() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mgr = SourceManager::new(path.clone());

        mgr.add(make_entry("official", "https://github.com/example/agents")).unwrap();

        let contents = std::fs::read_to_string(&path).unwrap();
        assert!(contents.contains("sources:"));
        assert!(contents.contains("name: official"));
    }
}
