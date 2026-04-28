pub mod check;
pub mod config;
pub mod create;
pub mod env;
pub mod evolution;
pub mod init;
pub mod install;
pub mod run;
pub mod runtime;
pub mod sources;

use std::path::{Path, PathBuf};

/// Find the project root directory by walking up from `start` looking for config.toml.
/// Falls back to `start` if no marker file is found within 10 levels.
pub fn find_project_root(start: &Path) -> PathBuf {
    const MAX_DEPTH: usize = 10;
    let mut dir = start;
    let mut depth = 0;
    loop {
        if dir.join("config.toml").exists() {
            return dir.to_path_buf();
        }
        depth += 1;
        if depth >= MAX_DEPTH {
            break;
        }
        match dir.parent() {
            Some(parent) => dir = parent,
            None => break,
        }
    }
    eprintln!("Warning: No config.toml found within {MAX_DEPTH} levels. Using current directory as project root.");
    start.to_path_buf()
}

/// Validate that a name is safe for use as a filesystem directory component.
/// Rejects path traversal attempts (.., /, \), null bytes, and leading hyphens.
pub fn validate_fs_name(name: &str) -> anyhow::Result<()> {
    if name.is_empty() {
        anyhow::bail!("Name cannot be empty");
    }
    if name.contains('\0') {
        anyhow::bail!("Invalid name (contains null byte): {name}");
    }
    if name.contains("..") || name.contains('/') || name.contains('\\') {
        anyhow::bail!("Invalid name (path traversal characters): {name}");
    }
    // Reject leading hyphens that could be interpreted as flags
    if name.starts_with('-') {
        anyhow::bail!("Invalid name (starts with hyphen): {name}");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_names_pass() {
        assert!(validate_fs_name("my-agent").is_ok());
        assert!(validate_fs_name("agent_123").is_ok());
        assert!(validate_fs_name("a").is_ok());
    }

    #[test]
    fn empty_name_rejected() {
        assert!(validate_fs_name("").is_err());
    }

    #[test]
    fn path_traversal_rejected() {
        assert!(validate_fs_name("../etc/passwd").is_err());
        assert!(validate_fs_name("foo/bar").is_err());
        assert!(validate_fs_name("foo\\bar").is_err());
        assert!(validate_fs_name("..").is_err());
    }

    #[test]
    fn null_byte_rejected() {
        let name = "agent\0malicious";
        let result = validate_fs_name(name);
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("null byte"),
            "Expected null byte message, got: {}",
            err
        );
    }

    #[test]
    fn leading_hyphen_rejected() {
        let result = validate_fs_name("--verbose");
        assert!(result.is_err());
        let err = result.unwrap_err().to_string();
        assert!(
            err.contains("hyphen"),
            "Expected hyphen message, got: {}",
            err
        );
    }
}
