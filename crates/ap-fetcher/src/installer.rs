//! Git-based agent installer: clone, checkout version, move to final location.

use std::path::PathBuf;

use thiserror::Error;
use tracing::{debug, info};

/// Errors from git installer operations.
#[derive(Debug, Error)]
pub enum InstallerError {
    #[error("Git error: {0}")]
    Git(#[from] git2::Error),
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),
    #[error("version not found: {0}")]
    VersionNotFound(String),
    #[error("validation error: {0}")]
    Validation(String),
}

/// Git-based agent installer. Clones repos and checks out specific versions.
#[derive(Debug)]
pub struct GitInstaller {
    install_dir: PathBuf,
}

impl GitInstaller {
    /// Create a new installer that installs agents under `install_dir`.
    pub fn new(install_dir: PathBuf) -> Self {
        Self { install_dir }
    }

    /// Install an agent from a Git URL.
    ///
    /// Clones into a temporary directory (under `install_dir`), optionally checks
    /// out a specific semver version tag, then moves the result to the final
    /// location atomically.
    pub fn install(
        &self,
        url: &str,
        branch: Option<&str>,
        version: Option<&str>,
    ) -> Result<PathBuf, InstallerError> {
        // Validate URL scheme: reject dangerous schemes like file://
        let allowed_schemes = ["https://", "http://", "git://", "ssh://"];
        let is_local_path = url.starts_with('/') || url.starts_with('.') || url.starts_with('~');
        if !allowed_schemes.iter().any(|s| url.starts_with(s)) && !is_local_path {
            return Err(InstallerError::Validation(format!(
                "Invalid git URL scheme. Allowed: {}, or a local path",
                allowed_schemes.join(", ")
            )));
        }

        // Ensure install_dir exists
        std::fs::create_dir_all(&self.install_dir)?;

        // Determine final destination path from the URL
        let dir_name = Self::url_to_dirname(url);
        let final_path = self.install_dir.join(&dir_name);

        // Create a temp directory under install_dir for atomic clone
        let tmp_path = self.install_dir.join(format!(".tmp-{}", dir_name));
        if tmp_path.exists() {
            std::fs::remove_dir_all(&tmp_path)?;
        }

        debug!("Cloning {} into {:?}", url, tmp_path);

        let repo = self.clone_repo(url, branch, &tmp_path)?;

        // Checkout specific version if requested
        if let Some(ver) = version {
            Self::checkout_version(&repo, ver)?;
        }

        // Close the repo (releases file handles) before moving
        drop(repo);

        // Atomic move: remove old install, rename temp to final
        if final_path.exists() {
            std::fs::remove_dir_all(&final_path)?;
        }
        std::fs::rename(&tmp_path, &final_path)?;
        info!("Installed agent to {:?}", final_path);

        Ok(final_path)
    }

    /// Find a semver tag matching the given version and check it out.
    pub fn checkout_version(repo: &git2::Repository, version: &str) -> Result<(), InstallerError> {
        let target = semver::Version::parse(version).map_err(|e| {
            InstallerError::VersionNotFound(format!("invalid semver '{}': {}", version, e))
        })?;

        let tags = repo.tag_names(None)?;
        let mut found_tag: Option<String> = None;

        for tag_name in tags.iter().flatten() {
            // Try "v1.2.3" prefix or bare "1.2.3"
            let candidate = tag_name.strip_prefix('v').unwrap_or(tag_name);
            if let Ok(parsed) = semver::Version::parse(candidate) {
                if parsed == target {
                    found_tag = Some(tag_name.to_string());
                    break;
                }
            }
        }

        let tag = found_tag.ok_or_else(|| {
            InstallerError::VersionNotFound(format!(
                "no tag found matching version '{}' (available tags: {:?})",
                version,
                tags.iter().flatten().collect::<Vec<_>>()
            ))
        })?;

        let refname = format!("refs/tags/{}", tag);
        let obj = repo.revparse_single(&refname)?;
        repo.checkout_tree(&obj, None)?;
        repo.set_head(&refname)?;
        debug!("Checked out version {} (tag: {})", version, tag);
        Ok(())
    }

    /// Extract a directory name from a Git URL, sanitizing unsafe characters.
    fn url_to_dirname(url: &str) -> String {
        let url = url.trim_end_matches('/');
        let raw = url
            .rsplit('/')
            .next()
            .unwrap_or("agent")
            .trim_end_matches(".git");
        let sanitized: String = raw
            .chars()
            .map(|c| {
                if c.is_alphanumeric() || c == '-' || c == '_' {
                    c
                } else {
                    '_'
                }
            })
            .collect();
        if sanitized.is_empty() {
            "agent".to_string()
        } else {
            sanitized
        }
    }

    /// Clone a repository with the given options.
    fn clone_repo(
        &self,
        url: &str,
        branch: Option<&str>,
        dest: &std::path::Path,
    ) -> Result<git2::Repository, InstallerError> {
        let mut remote_callbacks = git2::RemoteCallbacks::new();
        remote_callbacks.credentials(|_url, username, _allowed_types| {
            git2::Cred::default()
                .or_else(|_| {
                    if let Some(user) = username {
                        git2::Cred::ssh_key_from_agent(user)
                    } else {
                        Err(git2::Error::from_str("no username provided"))
                    }
                })
                .or_else(|_| git2::Cred::default())
        });

        let mut fetch_opts = git2::FetchOptions::new();
        fetch_opts.remote_callbacks(remote_callbacks);

        let mut builder = git2::build::RepoBuilder::new();
        builder.fetch_options(fetch_opts);
        if let Some(b) = branch {
            builder.branch(b);
        }

        let repo = builder.clone(url, dest)?;
        Ok(repo)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    /// Create a minimal local Git repo with optional tags for testing.
    fn create_test_repo(dir: &Path, tags: &[&str]) -> git2::Repository {
        let repo = git2::Repository::init(dir).unwrap();
        let sig = git2::Signature::new("Test", "test@test.com", &git2::Time::new(0, 0)).unwrap();

        // Create an initial commit with a dummy file
        std::fs::write(dir.join("agent.toml"), "name = \"test-agent\"\n").unwrap();
        let mut index = repo.index().unwrap();
        index.add_path(Path::new("agent.toml")).unwrap();
        index.write().unwrap();
        let tree_id = index.write_tree().unwrap();
        let tree = repo.find_tree(tree_id).unwrap();

        repo.commit(Some("HEAD"), &sig, &sig, "Initial commit", &tree, &[])
            .unwrap();
        drop(tree);

        // Create tags
        for tag in tags {
            let head = repo.head().unwrap().target().unwrap();
            let obj = repo.find_object(head, None).unwrap();
            let msg = format!("Release {}", tag);
            repo.tag(tag, &obj, &sig, &msg, false)
                .unwrap();
        }

        repo
    }

    #[test]
    fn clone_from_local_repo() {
        let src_dir = tempfile::tempdir().unwrap();
        create_test_repo(src_dir.path(), &[]);

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let result = installer.install(src_url, None, None).unwrap();

        assert!(result.exists());
        assert!(result.join("agent.toml").exists());
    }

    #[test]
    fn clone_with_branch() {
        let src_dir = tempfile::tempdir().unwrap();
        let repo = create_test_repo(src_dir.path(), &[]);

        // Create a branch "dev"
        let head = repo.head().unwrap().target().unwrap();
        let commit = repo.find_commit(head).unwrap();
        repo.branch("dev", &commit, false).unwrap();

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let result = installer.install(src_url, Some("dev"), None).unwrap();
        assert!(result.exists());
    }

    #[test]
    fn checkout_semver_tag_with_v_prefix() {
        let src_dir = tempfile::tempdir().unwrap();
        create_test_repo(src_dir.path(), &["v1.0.0", "v1.1.0"]);

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let result = installer.install(src_url, None, Some("1.1.0")).unwrap();
        assert!(result.exists());
    }

    #[test]
    fn checkout_semver_tag_bare() {
        let src_dir = tempfile::tempdir().unwrap();
        create_test_repo(src_dir.path(), &["1.0.0"]);

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let result = installer.install(src_url, None, Some("1.0.0")).unwrap();
        assert!(result.exists());
    }

    #[test]
    fn version_not_found_errors() {
        let src_dir = tempfile::tempdir().unwrap();
        create_test_repo(src_dir.path(), &["v1.0.0"]);

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let result = installer.install(src_url, None, Some("99.0.0"));
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(err_msg.contains("version not found"));
    }

    #[test]
    fn url_to_dirname_extracts_name() {
        assert_eq!(
            GitInstaller::url_to_dirname("https://github.com/foo/my-agent"),
            "my-agent"
        );
        assert_eq!(
            GitInstaller::url_to_dirname("https://github.com/foo/my-agent.git"),
            "my-agent"
        );
        assert_eq!(
            GitInstaller::url_to_dirname("/local/path/agent-repo"),
            "agent-repo"
        );
    }

    #[test]
    fn url_to_dirname_sanitizes_path_traversal() {
        // Path traversal "../" in URL — we only take the last segment,
        // and sanitize non-alphanumeric characters
        let dirname = GitInstaller::url_to_dirname("https://github.com/foo/../evil");
        assert_eq!(dirname, "evil");
        assert!(!dirname.contains('/'));

        // Special characters are sanitized
        let dirname2 = GitInstaller::url_to_dirname("https://github.com/foo/agent@v2");
        assert_eq!(dirname2, "agent_v2");
    }

    #[test]
    fn install_rejects_file_url() {
        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let result = installer.install("file:///etc/passwd", None, None);
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("Invalid git URL scheme"),
            "Expected scheme validation error, got: {err_msg}"
        );
    }

    #[test]
    fn install_rejects_unknown_scheme() {
        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let result = installer.install("ftp://example.com/repo", None, None);
        assert!(result.is_err());
        let err_msg = result.unwrap_err().to_string();
        assert!(
            err_msg.contains("Invalid git URL scheme"),
            "Expected scheme validation error, got: {err_msg}"
        );
    }

    #[test]
    fn reinstall_overwrites_existing() {
        let src_dir = tempfile::tempdir().unwrap();
        create_test_repo(src_dir.path(), &[]);

        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        let src_url = src_dir.path().to_str().unwrap();
        let first = installer.install(src_url, None, None).unwrap();
        assert!(first.exists());

        // Install again should succeed (overwrite)
        let second = installer.install(src_url, None, None).unwrap();
        assert!(second.exists());
    }

    // Network tests — mark with #[ignore] so they don't run in CI
    #[test]
    #[ignore]
    fn clone_remote_repo() {
        let dest_base = tempfile::tempdir().unwrap();
        let installer = GitInstaller::new(dest_base.path().to_path_buf());

        // Use a small, stable public repo
        let result = installer.install(
            "https://github.com/rust-lang/hello-world",
            None,
            None,
        );
        assert!(result.is_ok());
    }
}
