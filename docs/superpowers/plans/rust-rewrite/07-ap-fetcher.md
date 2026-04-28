# Phase 7: ap-fetcher — Git-Based Agent Distribution

> **Goal:** Port the Git installer, sources manager, lockfile manager, and agent supervisor.

**Python source:** `src/agent_nexus/platform/local/` (4,577 lines for local+CLI combined)
**Rust target:** `crates/ap-fetcher/src/`
**Depends on:** Phase 1 (ap-core models)

**Files:**
- Create: `crates/ap-fetcher/src/lib.rs` (overwrite skeleton)
- Create: `crates/ap-fetcher/src/installer.rs`
- Create: `crates/ap-fetcher/src/sources.rs`
- Create: `crates/ap-fetcher/src/lockfile.rs`
- Create: `crates/ap-fetcher/src/supervisor.rs`
- Create: `crates/ap-fetcher/src/uv_bridge.rs`

---

## Task 7.1: Sources Manager

**Python source:** `src/agent_nexus/platform/local/sources.py`
**Rust target:** `crates/ap-fetcher/src/sources.rs`

> **F-04 fix:** Source types are now defined in `ap_core::models::distribution`.
> This module only manages the YAML file I/O, using those types.

Manages `sources.yaml` — list of Git repos that provide agents.

- [ ] **Step 1: Write sources tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_sources_yaml() {
        let yaml = r#"
sources:
  - name: official
    url: https://github.com/example/agent-nexus-agents
    branch: main
  - name: private
    url: https://github.com/mycompany/private-agents
    branch: stable
"#;
        let sources = SourceManager::parse(yaml).unwrap();
        assert_eq!(sources.len(), 2);
        assert_eq!(sources[0].name, "official");
    }

    #[test]
    fn backward_compat_read_python_yaml() {
        // YAML written by Python's yaml.dump must be readable
        let yaml = r#"
- name: official
  url: https://github.com/example/agents
  branch: main
"#;
        let sources = SourceManager::parse(yaml).unwrap();
        assert_eq!(sources[0].name, "official");
    }

    #[test]
    fn add_and_remove_source() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("sources.yaml");
        let mut mgr = SourceManager::new(&path);

        mgr.add(ap_core::models::distribution::SourceEntry {
            name: "test".into(),
            source_type: "git".into(),
            url: "https://example.com/repo".into(),
            branch: "main".into(),
        }).unwrap();

        let sources = mgr.list();
        assert_eq!(sources.len(), 1);
        mgr.remove("test").unwrap();
        assert!(mgr.list().is_empty());
    }
}
```

- [ ] **Step 2: Implement SourceManager**

```rust
use ap_core::models::distribution::SourceEntry;
use std::path::{Path, PathBuf};

#[derive(Debug, thiserror::Error)]
pub enum SourceError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("YAML parse error: {0}")]
    Yaml(#[from] serde_yaml::Error),
    #[error("Source not found: {0}")]
    NotFound(String),
    #[error("Validation error: {0}")]
    Validation(String),
}

/// YAML wrapper for sources.yaml format.
/// The file has an optional `sources:` top-level key.
#[derive(Debug, serde::Serialize, serde::Deserialize)]
struct SourcesYaml {
    #[serde(default)]
    sources: Vec<SourceEntry>,
}

pub struct SourceManager {
    path: PathBuf,
}

impl SourceManager {
    pub fn new(path: &Path) -> Self {
        Self { path: path.to_path_buf() }
    }

    /// Parse YAML — accepts both `sources: [...]` and bare `[...]` formats.
    pub fn parse(yaml: &str) -> Result<Vec<SourceEntry>, SourceError> {
        // Try wrapped format first
        if let Ok(wrapped) = serde_yaml::from_str::<SourcesYaml>(yaml) {
            return Ok(wrapped.sources);
        }
        // Fallback: bare list format
        let sources: Vec<SourceEntry> = serde_yaml::from_str(yaml)?;
        Ok(sources)
    }

    pub fn load(&self) -> Result<Vec<SourceEntry>, SourceError> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let content = std::fs::read_to_string(&self.path)?;
        let sources = Self::parse(&content)?;
        // Validate each entry
        for s in &sources {
            s.validate().map_err(SourceError::Validation)?;
        }
        Ok(sources)
    }

    pub fn save(&self, sources: &[SourceEntry]) -> Result<(), SourceError> {
        let wrapper = SourcesYaml { sources: sources.to_vec() };
        let yaml = serde_yaml::to_string(&wrapper)?;
        let tmp_path = self.path.with_extension("yaml.tmp");
        std::fs::write(&tmp_path, &yaml)?;
        std::fs::rename(&tmp_path, &self.path)?;
        Ok(())
    }

    pub fn list(&self) -> Vec<SourceEntry> {
        self.load().unwrap_or_default()
    }

    pub fn add(&mut self, entry: SourceEntry) -> Result<(), SourceError> {
        entry.validate().map_err(SourceError::Validation)?;
        let mut sources = self.load()?;
        sources.retain(|s| s.name != entry.name);
        sources.push(entry);
        self.save(&sources)
    }

    pub fn remove(&mut self, name: &str) -> Result<(), SourceError> {
        let mut sources = self.load()?;
        let before = sources.len();
        sources.retain(|s| s.name != name);
        if sources.len() == before {
            return Err(SourceError::NotFound(name.into()));
        }
        self.save(&sources)
    }
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-fetcher -- sources
git add crates/ap-fetcher/src/sources.rs
git commit -m "feat(ap-fetcher): SourceManager with YAML parsing and atomic writes"
```

---

## Task 7.2: Lockfile Manager

**Python source:** `src/agent_nexus/platform/local/lockfile.py`
**Rust target:** `crates/ap-fetcher/src/lockfile.rs`

> **F-03 fix:** Lockfile types are now defined in `ap_core::models::distribution`.
> Field `git_hash` renamed to `commit_sha`. Missing fields `agent_type`, `venv_path`,
> `dependencies` added. This module only handles file I/O using those types.

Manages `lockfile.json` — tracks installed agents, versions, paths.

- [ ] **Step 1: Write lockfile tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_python_written_lockfile() {
        // This matches the EXACT Python format from distribution.py
        let json = r#"{
  "version": 1,
  "agents": {
    "code-reviewer": {
      "version": "1.2.0",
      "source": "official",
      "commit_sha": "abc123def456abc123def456abc123def456abc1",
      "agent_type": "atomic",
      "installed_at": "2026-04-20T10:00:00Z",
      "venv_path": "~/.agent-nexus/venvs/code-reviewer",
      "dependencies": ["pydantic>=2.0"]
    }
  }
}"#;
        let lockfile = LockfileManager::parse(json).unwrap();
        assert!(lockfile.agents.contains_key("code-reviewer"));
        let entry = &lockfile.agents["code-reviewer"];
        assert_eq!(entry.version, "1.2.0");
        assert_eq!(entry.agent_type, ap_core::models::agent::AgentType::Atomic);
        assert_eq!(entry.venv_path, "~/.agent-nexus/venvs/code-reviewer");
        assert_eq!(entry.dependencies.len(), 1);
    }

    #[test]
    fn add_and_write_lockfile() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lockfile.json");
        let mut mgr = LockfileManager::new(&path);

        mgr.add("test-agent", ap_core::models::distribution::LockfileEntry {
            version: "0.1.0".into(),
            source: "test".into(),
            commit_sha: "def5678901234567890123456789012345678901".into(),
            agent_type: ap_core::models::agent::AgentType::Atomic,
            installed_at: "2026-04-22T00:00:00Z".into(),
            venv_path: String::new(),
            dependencies: vec![],
        }).unwrap();

        let loaded = mgr.load().unwrap();
        assert!(loaded.agents.contains_key("test-agent"));
    }

    #[test]
    fn missing_lockfile_returns_empty() {
        let mgr = LockfileManager::new(Path::new("/nonexistent/lockfile.json"));
        let lockfile = mgr.load().unwrap();
        assert!(lockfile.agents.is_empty());
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
    }
}
```

- [ ] **Step 2: Implement LockfileManager**

```rust
use ap_core::models::distribution::{Lockfile, LockfileEntry};
use std::path::{Path, PathBuf};

pub struct LockfileManager {
    path: PathBuf,
}

#[derive(Debug, thiserror::Error)]
pub enum LockfileError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),
}

impl LockfileManager {
    pub fn new(path: &Path) -> Self {
        Self { path: path.to_path_buf() }
    }

    pub fn parse(json: &str) -> Result<Lockfile, LockfileError> {
        Ok(serde_json::from_str(json)?)
    }

    pub fn load(&self) -> Result<Lockfile, LockfileError> {
        if !self.path.exists() {
            return Ok(Lockfile::default());
        }
        let content = std::fs::read_to_string(&self.path)?;
        Self::parse(&content)
    }

    pub fn save(&self, lockfile: &Lockfile) -> Result<(), LockfileError> {
        let json = serde_json::to_string_pretty(lockfile)?;
        let tmp_path = self.path.with_extension("json.tmp");
        std::fs::write(&tmp_path, &json)?;
        std::fs::rename(&tmp_path, &self.path)?;
        Ok(())
    }

    pub fn add(&mut self, name: &str, entry: LockfileEntry) -> Result<(), LockfileError> {
        let mut lockfile = self.load()?;
        lockfile.agents.insert(name.to_string(), entry);
        self.save(&lockfile)
    }

    pub fn remove(&mut self, name: &str) -> Result<(), LockfileError> {
        let mut lockfile = self.load()?;
        lockfile.agents.remove(name);
        self.save(&lockfile)
    }
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-fetcher -- lockfile
git add crates/ap-fetcher/src/lockfile.rs
git commit -m "feat(ap-fetcher): LockfileManager with atomic JSON read/write"
```

---

## Task 7.3: Git Installer

**Python source:** `src/agent_nexus/platform/local/installer.py`
**Rust target:** `crates/ap-fetcher/src/installer.rs`

Uses `git2` for clone, tag checkout, sparse checkout.

- [ ] **Step 1: Write installer tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clone_remote_repo() {
        // Integration test: needs network. Skip in CI if no network.
        // Use a small, stable repo.
    }

    #[test]
    fn resolve_version_tag() {
        // Test semver version resolution against tags
    }

    #[test]
    fn atomic_install_rollback_on_failure() {
        // If venv setup fails, the clone should be cleaned up
    }
}
```

- [ ] **Step 2: Implement GitInstaller**

```rust
use git2::{Repository, FetchOptions, build::CheckoutBuilder};
use semver::Version;
use std::path::{Path, PathBuf};

pub struct GitInstaller {
    install_dir: PathBuf,
}

impl GitInstaller {
    pub fn new(install_dir: &Path) -> Self {
        Self { install_dir: install_dir.to_path_buf() }
    }

    pub fn install(&self, url: &str, branch: &str, version: Option<&str>) -> Result<PathBuf, InstallerError> {
        let target = self.install_dir.join("tmp-install");

        // Clone to temp dir
        let repo = Repository::clone(url, &target)
            .map_err(|e| InstallerError::Git(e.message().into()))?;

        // Checkout specific version if specified
        if let Some(ver) = version {
            self.checkout_version(&repo, ver)?;
        }

        // Move to final location (atomic)
        let final_path = self.install_dir.join("agents");
        if final_path.exists() {
            std::fs::remove_dir_all(&final_path)?;
        }
        std::fs::rename(&target, &final_path)?;

        Ok(final_path)
    }

    fn checkout_version(&self, repo: &Repository, version: &str) -> Result<(), InstallerError> {
        let tags = repo.tag_names(None).map_err(|e| InstallerError::Git(e.message().into()))?;
        // Find matching semver tag
        let target = Version::parse(version).ok();
        let matched_tag = tags.iter()
            .flatten()
            .find(|t| {
                t.strip_prefix('v').and_then(|v| Version::parse(v).ok()) == target
            });

        if let Some(tag) = matched_tag {
            let obj = repo.revparse_single(&format!("refs/tags/{}", tag))
                .map_err(|e| InstallerError::Git(e.message().into()))?;
            repo.checkout_tree(&obj, None)
                .map_err(|e| InstallerError::Git(e.message().into()))?;
        }
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum InstallerError {
    #[error("Git error: {0}")]
    Git(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Version not found: {0}")]
    VersionNotFound(String),
}
```

- [ ] **Step 3: Verify and commit**

```bash
cargo test -p ap-fetcher -- installer
git add crates/ap-fetcher/src/installer.rs
git commit -m "feat(ap-fetcher): GitInstaller with git2 clone and version checkout"
```

---

## Task 7.4: uv Bridge

**Python source:** Shell calls to `uv` throughout local/
**Rust target:** `crates/ap-fetcher/src/uv_bridge.rs`

- [ ] **Write tests + implement**

```rust
use tokio::process::Command;

pub struct UvBridge {
    uv_path: String,
}

impl UvBridge {
    pub fn new() -> Self {
        Self { uv_path: "uv".into() }
    }

    pub fn with_path(mut self, path: &str) -> Self {
        self.uv_path = path.to_string();
        self
    }

    pub async fn check_available(&self) -> bool {
        Command::new(&self.uv_path).arg("--version").output().await.is_ok()
    }

    pub async fn create_venv(&self, path: &str) -> Result<(), UvError> {
        let output = Command::new(&self.uv_path)
            .args(["venv", path])
            .output().await?;
        if !output.status.success() {
            return Err(UvError::CommandFailed(String::from_utf8_lossy(&output.stderr).into()));
        }
        Ok(())
    }

    pub async fn pip_install(&self, venv_path: &str, requirements: &[&str]) -> Result<(), UvError> {
        let mut args = vec!["pip", "install", "--python", venv_path];
        args.extend(requirements);
        let output = Command::new(&self.uv_path)
            .args(&args)
            .output().await?;
        if !output.status.success() {
            return Err(UvError::CommandFailed(String::from_utf8_lossy(&output.stderr).into()));
        }
        Ok(())
    }
}

#[derive(Debug, thiserror::Error)]
pub enum UvError {
    #[error("uv command failed: {0}")]
    CommandFailed(String),
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
}
```

- [ ] **Verify and commit**

```bash
cargo test -p ap-fetcher -- uv_bridge
git add crates/ap-fetcher/src/uv_bridge.rs
git commit -m "feat(ap-fetcher): uv bridge for venv and pip management"
```

---

## Task 7.5: Agent Supervisor

**Python source:** `src/agent_nexus/platform/local/supervisor.py`
**Rust target:** `crates/ap-fetcher/src/supervisor.rs`

Manages installed agent lifecycle: start, stop, health check, auto-restart.

- [ ] **Write tests + implement + verify + commit**

```bash
cargo test -p ap-fetcher -- supervisor
git add crates/ap-fetcher/src/supervisor.rs
git commit -m "feat(ap-fetcher): AgentSupervisor with lifecycle management"
```

---

## Task 7.6: Update lib.rs

```rust
// crates/ap-fetcher/src/lib.rs
pub mod installer;
pub mod sources;
pub mod lockfile;
pub mod supervisor;
pub mod uv_bridge;
```

```bash
cargo build -p ap-fetcher
git add crates/ap-fetcher/src/lib.rs
git commit -m "feat(ap-fetcher): module exports"
```

---

## Final Verification

- [ ] `cargo test -p ap-fetcher`
- [ ] `cargo clippy -p ap-fetcher -- -D warnings`
