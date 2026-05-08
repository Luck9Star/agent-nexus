//! ap-fetcher — Git-based agent distribution: installer, sources, lockfile, supervisor.

pub mod advisory_lock;
pub mod installer;
pub mod lockfile;
pub mod manifest_checker;
pub mod security_audit;
pub mod skill_checker;
pub mod sources;
pub mod traits;
pub mod uv_bridge;

pub use traits::Installer;
