pub mod check;
pub mod config;
pub mod create;
pub mod evolution;
pub mod env;
pub mod init;
pub mod install;
pub mod run;
pub mod runtime;
pub mod sources;

use std::path::{Path, PathBuf};

/// Find the project root directory by walking up from `start` looking for config.toml.
/// Falls back to `start` if no marker file is found.
pub fn find_project_root(start: &Path) -> PathBuf {
    let mut dir = start;
    loop {
        if dir.join("config.toml").exists() {
            return dir.to_path_buf();
        }
        match dir.parent() {
            Some(parent) => dir = parent,
            None => break,
        }
    }
    eprintln!("Warning: No config.toml found. Using current directory as project root.");
    start.to_path_buf()
}

/// Validate that a name is safe for use as a filesystem directory component.
/// Rejects path traversal attempts (.., /, \).
pub fn validate_fs_name(name: &str) -> anyhow::Result<()> {
    if name.is_empty() {
        anyhow::bail!("Name cannot be empty");
    }
    if name.contains("..") || name.contains('/') || name.contains('\\') {
        anyhow::bail!("Invalid name (path traversal characters): {}", name);
    }
    Ok(())
}
