//! Git-based distribution models: PackageSource, SourceEntry, LockfileEntry, InstallationStatus.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

use super::agent::AgentType;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceType {
    Official,
    Private,
    Direct,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum InstallationStatus {
    Installed,
    Outdated,
    NotInstalled,
    Installing,
    Failed,
}

/// A package source entry from sources.yaml.
///
/// Python source: models/distribution.py:33-57
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SourceEntry {
    #[serde(default)]
    pub name: String,
    #[serde(rename = "type", default = "default_git")]
    pub source_type: String,
    #[serde(default)]
    pub url: String,
    #[serde(default = "default_branch")]
    pub branch: String,
}

fn default_git() -> String { "git".to_string() }
fn default_branch() -> String { "main".to_string() }
fn utc_now() -> DateTime<Utc> { Utc::now() }

impl SourceEntry {
    /// Validate: git-type sources must have non-empty URL.
    pub fn validate(&self) -> Result<(), String> {
        if self.source_type == "git" && self.url.trim().is_empty() {
            return Err(format!(
                "Git-type source requires a non-empty 'url'. Source '{}' has type='git' but url is empty.",
                self.name
            ));
        }
        Ok(())
    }
}

/// A single Agent entry in lockfile.json.
///
/// Python source: models/distribution.py:60-94
/// IMPORTANT (F-03 fix): `commit_sha` not `git_hash`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct LockfileEntry {
    pub version: String,
    pub source: String,
    /// Commit SHA — 40/64 hex chars, or sentinel 'latest'/'head'.
    pub commit_sha: String,
    pub agent_type: AgentType,
    #[serde(default = "utc_now")]
    pub installed_at: DateTime<Utc>,
    #[serde(default)]
    pub venv_path: String,
    #[serde(default)]
    pub dependencies: Vec<String>,
}

impl LockfileEntry {
    /// Validate commit_sha format: 40/64 hex or 'latest'/'head'.
    pub fn validate_commit_sha(&self) -> Result<(), String> {
        let valid = self.commit_sha.len() == 40
            || self.commit_sha.len() == 64
            || self.commit_sha == "latest"
            || self.commit_sha == "head";
        if valid
            && self.commit_sha.len() >= 40
            && !self.commit_sha.chars().all(|c| c.is_ascii_hexdigit())
        {
            return Err(format!("commit_sha '{}' is not valid hex", self.commit_sha));
        }
        Ok(())
    }
}

/// The complete lockfile.json structure.
///
/// Python source: models/distribution.py:97-105
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Lockfile {
    #[serde(default = "default_lockfile_version")]
    pub version: u32,
    #[serde(default)]
    pub agents: HashMap<String, LockfileEntry>,
}

fn default_lockfile_version() -> u32 { 1 }

impl Default for Lockfile {
    fn default() -> Self {
        Self { version: 1, agents: HashMap::new() }
    }
}

/// Git package source with local cache path.
///
/// Python source: models/distribution.py:108-115
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PackageSource {
    #[serde(flatten)]
    pub entry: SourceEntry,
    #[serde(default)]
    pub local_cache: String,
}

/// A single Agent entry from a source's index.yaml.
///
/// Python source: models/distribution.py:118-150
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct IndexEntry {
    pub name: String,
    pub version: String,
    #[serde(rename = "type")]
    pub agent_type: AgentType,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub path: String,
}

impl IndexEntry {
    /// Reject path traversal sequences in the path field.
    pub fn validate_path(&self) -> Result<(), String> {
        if !self.path.is_empty() && self.path.contains("..") {
            return Err(format!("IndexEntry.path must not contain '..': got '{}'", self.path));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_entry_validates_git_url() {
        let entry = SourceEntry {
            name: "test".into(),
            source_type: "git".into(),
            url: "".into(),
            branch: "main".into(),
        };
        assert!(entry.validate().is_err());
    }

    #[test]
    fn source_entry_passes_with_url() {
        let entry = SourceEntry {
            name: "test".into(),
            source_type: "git".into(),
            url: "https://github.com/test/repo".into(),
            branch: "main".into(),
        };
        assert!(entry.validate().is_ok());
    }

    #[test]
    fn lockfile_entry_valid_commit_sha() {
        let entry = LockfileEntry {
            version: "1.0.0".into(),
            source: "official".into(),
            commit_sha: "abc123def456abc123def456abc123def456abc1".into(),
            agent_type: AgentType::Atomic,
            installed_at: "2026-04-22T00:00:00Z".parse().unwrap(),
            venv_path: String::new(),
            dependencies: vec![],
        };
        assert!(entry.validate_commit_sha().is_ok());
    }

    #[test]
    fn lockfile_entry_latest_sentinel() {
        let entry = LockfileEntry {
            version: "1.0.0".into(),
            source: "official".into(),
            commit_sha: "latest".into(),
            agent_type: AgentType::Atomic,
            installed_at: Utc::now(),
            venv_path: String::new(),
            dependencies: vec![],
        };
        assert!(entry.validate_commit_sha().is_ok());
    }

    #[test]
    fn index_entry_rejects_path_traversal() {
        let entry = IndexEntry {
            name: "test".into(),
            version: "1.0.0".into(),
            agent_type: AgentType::Atomic,
            description: String::new(),
            tags: vec![],
            dependencies: vec![],
            path: "../etc/passwd".into(),
        };
        assert!(entry.validate_path().is_err());
    }

    #[test]
    fn deserialize_python_lockfile() {
        let json = r#"{
            "version": 1,
            "agents": {
                "code-reviewer": {
                    "version": "1.2.0",
                    "source": "official",
                    "commit_sha": "abc123def456abc123def456abc123def456abc1",
                    "agent_type": "atomic",
                    "installed_at": "2026-04-20T10:00:00Z",
                    "venv_path": "~/.agent-nexus/venvs/doc-filler",
                    "dependencies": ["pydantic>=2.0"]
                }
            }
        }"#;
        let lockfile: Lockfile = serde_json::from_str(json).unwrap();
        assert!(lockfile.agents.contains_key("code-reviewer"));
        let entry = &lockfile.agents["code-reviewer"];
        assert_eq!(entry.version, "1.2.0");
        assert_eq!(entry.agent_type, AgentType::Atomic);
        assert_eq!(entry.venv_path, "~/.agent-nexus/venvs/doc-filler");
        assert_eq!(entry.dependencies.len(), 1);
    }

    #[test]
    fn source_entry_type_field_renamed() {
        let yaml = r#"
name: official
type: git
url: https://github.com/test/repo
branch: main
"#;
        let entry: SourceEntry = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(entry.source_type, "git");
        // Verify serialization uses "type" key
        let serialized = serde_json::to_string(&entry).unwrap();
        assert!(serialized.contains(r#""type":"git""#));
        assert!(!serialized.contains("source_type"));
    }

    #[test]
    fn index_entry_type_field_renamed() {
        let json = r#"{"name":"test","version":"1.0.0","type":"atomic","description":"test agent"}"#;
        let entry: IndexEntry = serde_json::from_str(json).unwrap();
        assert_eq!(entry.agent_type, AgentType::Atomic);
        // Verify serialization uses "type" key
        let serialized = serde_json::to_string(&entry).unwrap();
        assert!(serialized.contains(r#""type":"atomic""#));
        assert!(!serialized.contains("agent_type"));
    }
}
