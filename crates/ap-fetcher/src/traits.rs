//! Trait abstraction for agent installation.
//!
//! Enables mocking and alternative backends. The concrete `GitInstaller`
//! implements this trait.

use std::path::PathBuf;

use crate::installer::InstallerError;

/// Core operations for installing agents from Git repositories.
///
/// This trait captures the methods used by CLI commands. Additional installer
/// methods (validation helpers) remain on the concrete `GitInstaller` type.
pub trait Installer: Send + Sync {
    /// Install an agent from a Git URL.
    ///
    /// Returns the path to the installed agent directory.
    fn install(
        &self,
        url: &str,
        branch: Option<&str>,
        version: Option<&str>,
    ) -> Result<PathBuf, InstallerError>;
}
