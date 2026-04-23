//! Lockfile manager for `lockfile.json` — tracks installed agents.

use std::path::PathBuf;

use thiserror::Error;
use tracing::debug;

use ap_core::models::distribution::{Lockfile, LockfileEntry};

/// Errors from lockfile management operations.
#[derive(Debug, Error)]
pub enum LockfileError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
}

/// Manages `lockfile.json` — tracks installed agents and their metadata.
#[derive(Debug)]
pub struct LockfileManager {
    path: PathBuf,
}

impl LockfileManager {
    /// Create a new lockfile manager pointing to the given `lockfile.json` path.
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }

    /// Static: parse a JSON string into a `Lockfile`.
    pub fn parse(json: &str) -> Result<Lockfile, LockfileError> {
        let lockfile: Lockfile = serde_json::from_str(json)?;
        Ok(lockfile)
    }

    /// Load lockfile from disk. Returns default (empty) lockfile if file is missing.
    pub fn load(&self) -> Result<Lockfile, LockfileError> {
        if !self.path.exists() {
            debug!("Lockfile not found, returning default: {:?}", self.path);
            return Ok(Lockfile::default());
        }
        let contents = std::fs::read_to_string(&self.path)?;
        if contents.trim().is_empty() {
            debug!("Lockfile is empty, returning default");
            return Ok(Lockfile::default());
        }
        Self::parse(&contents)
    }

    /// Atomically write the lockfile as pretty-printed JSON (write to `.tmp`, then rename).
    pub fn save(&self, lockfile: &Lockfile) -> Result<(), LockfileError> {
        let json = serde_json::to_string_pretty(lockfile)?;

        let tmp_path = {
            let mut p = self.path.clone();
            p.set_extension("json.tmp");
            p
        };

        // Clean stale tmp from previous crash
        let _ = std::fs::remove_file(&tmp_path);
        std::fs::write(&tmp_path, &json)?;
        std::fs::rename(&tmp_path, &self.path)?;
        debug!(
            "Saved lockfile with {} agents to {:?}",
            lockfile.agents.len(),
            self.path
        );
        Ok(())
    }

    /// Add or update an agent entry in the lockfile.
    pub fn add(&self, name: &str, entry: LockfileEntry) -> Result<(), LockfileError> {
        let mut lockfile = self.load()?;
        lockfile.agents.insert(name.to_string(), entry);
        self.save(&lockfile)
    }

    /// Remove an agent entry from the lockfile.
    pub fn remove(&self, name: &str) -> Result<(), LockfileError> {
        let mut lockfile = self.load()?;
        lockfile.agents.remove(name);
        self.save(&lockfile)
    }

    /// Check if an agent exists in the lockfile.
    pub fn has(&self, name: &str) -> Result<bool, LockfileError> {
        let lockfile = self.load()?;
        Ok(lockfile.agents.contains_key(name))
    }

    /// Get a specific agent entry from the lockfile, if present.
    pub fn get(&self, name: &str) -> Result<Option<LockfileEntry>, LockfileError> {
        let lockfile = self.load()?;
        Ok(lockfile.agents.get(name).cloned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ap_core::models::agent::AgentType;

    fn fixed_now() -> chrono::DateTime<chrono::Utc> {
        "2026-04-22T00:00:00Z".parse().unwrap()
    }

    fn make_entry(name: &str, commit: &str) -> (String, LockfileEntry) {
        (
            name.to_string(),
            LockfileEntry {
                version: "1.0.0".to_string(),
                source: "official".to_string(),
                commit_sha: commit.to_string(),
                agent_type: AgentType::Atomic,
                installed_at: fixed_now(),
                venv_path: format!(".venvs/{}", name),
                dependencies: vec![],
            },
        )
    }

    #[test]
    fn parse_python_written_format() {
        // This matches what the Python runtime would produce
        let json = r#"{
  "version": 1,
  "agents": {
    "doc-filler": {
      "version": "1.0.0",
      "source": "official",
      "commit_sha": "abc123def456abc123def456abc123def456abc1",
      "agent_type": "atomic",
      "installed_at": "2026-04-22T00:00:00Z",
      "venv_path": ".venvs/doc-filler",
      "dependencies": []
    }
  }
}"#;
        let lockfile = LockfileManager::parse(json).unwrap();
        assert_eq!(lockfile.version, 1);
        assert!(lockfile.agents.contains_key("doc-filler"));
        let entry = &lockfile.agents["doc-filler"];
        assert_eq!(entry.version, "1.0.0");
        assert_eq!(entry.agent_type, AgentType::Atomic);
        assert_eq!(
            entry.commit_sha,
            "abc123def456abc123def456abc123def456abc1"
        );
    }

    #[test]
    fn commit_sha_accepts_latest() {
        let json = r#"{
  "version": 1,
  "agents": {
    "test": {
      "version": "1.0.0",
      "source": "official",
      "commit_sha": "latest",
      "agent_type": "atomic",
      "installed_at": "2026-04-22T00:00:00Z",
      "venv_path": "",
      "dependencies": []
    }
  }
}"#;
        let lockfile = LockfileManager::parse(json).unwrap();
        let entry = &lockfile.agents["test"];
        assert_eq!(entry.commit_sha, "latest");
        assert!(entry.validate_commit_sha().is_ok());
    }

    #[test]
    fn add_and_read_roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let (name, entry) = make_entry("my-agent", "abc123def456abc123def456abc123def456abc1");
        mgr.add(&name, entry).unwrap();

        let loaded = mgr.load().unwrap();
        assert!(loaded.agents.contains_key("my-agent"));
        assert_eq!(loaded.agents["my-agent"].version, "1.0.0");
    }

    #[test]
    fn add_multiple_agents() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let (n1, e1) = make_entry("agent-a", "aaa0000000000000000000000000000000000aaa");
        let (n2, e2) = make_entry("agent-b", "bbb0000000000000000000000000000000000bbb");
        mgr.add(&n1, e1).unwrap();
        mgr.add(&n2, e2).unwrap();

        let loaded = mgr.load().unwrap();
        assert_eq!(loaded.agents.len(), 2);
    }

    #[test]
    fn remove_agent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let (name, entry) = make_entry("to-remove", "ccc0000000000000000000000000000000000ccc");
        mgr.add(&name, entry).unwrap();
        mgr.remove("to-remove").unwrap();

        let loaded = mgr.load().unwrap();
        assert!(!loaded.agents.contains_key("to-remove"));
    }

    #[test]
    fn missing_file_returns_default() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let lockfile = mgr.load().unwrap();
        assert_eq!(lockfile.version, 1);
        assert!(lockfile.agents.is_empty());
    }

    #[test]
    fn has_checks_existence() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        assert!(!mgr.has("agent-x").unwrap());

        let (name, entry) = make_entry("agent-x", "ddd0000000000000000000000000000000000ddd");
        mgr.add(&name, entry).unwrap();
        assert!(mgr.has("agent-x").unwrap());
    }

    #[test]
    fn get_returns_entry() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let (name, entry) = make_entry("agent-y", "eee0000000000000000000000000000000000eee");
        mgr.add(&name, entry.clone()).unwrap();

        let retrieved = mgr.get("agent-y").unwrap();
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().commit_sha, entry.commit_sha);

        let missing = mgr.get("nonexistent").unwrap();
        assert!(missing.is_none());
    }

    #[test]
    fn save_produces_pretty_json() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path.clone());

        let (name, entry) = make_entry("pretty", "fff0000000000000000000000000000000000fff");
        mgr.add(&name, entry).unwrap();

        let contents = std::fs::read_to_string(&path).unwrap();
        // Pretty JSON has newlines and indentation
        assert!(contents.contains('\n'));
        assert!(contents.contains("  "));
    }

    #[test]
    fn overwrite_existing_agent() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mgr = LockfileManager::new(path);

        let (name, mut entry) = make_entry("updatable", "1110000000000000000000000000000000000111");
        mgr.add(&name, entry.clone()).unwrap();

        // Update version
        entry.version = "2.0.0".to_string();
        mgr.add(&name, entry).unwrap();

        let loaded = mgr.load().unwrap();
        assert_eq!(loaded.agents["updatable"].version, "2.0.0");
        assert_eq!(loaded.agents.len(), 1);
    }

    #[test]
    fn parse_empty_agents_map() {
        let json = r#"{"version": 1, "agents": {}}"#;
        let lockfile = LockfileManager::parse(json).unwrap();
        assert!(lockfile.agents.is_empty());
    }
}
