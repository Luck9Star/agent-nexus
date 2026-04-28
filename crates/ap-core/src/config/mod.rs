//! Configuration loading and model resolution.

pub mod defaults;
pub mod loader;
pub mod model_config;

pub use defaults::default_config;
pub use loader::{ConfigError, ConfigLoader};
pub use model_config::{ModelConfigError, ModelConfigManager, ResolvedModel};

/// Shared lock for tests that read/write AGENT_MODEL or DEFAULT_MODEL env vars.
/// Prevents cross-test contamination since Rust tests run in parallel within
/// the same process and env vars are process-global.
#[cfg(test)]
pub(crate) fn with_model_env<F, R>(f: F) -> R
where
    F: FnOnce() -> R,
{
    use std::sync::Mutex;
    static LOCK: std::sync::OnceLock<Mutex<()>> = std::sync::OnceLock::new();
    let guard = LOCK.get_or_init(|| Mutex::new(())).lock().unwrap_or_else(|e| e.into_inner());
    std::env::remove_var("AGENT_MODEL");
    std::env::remove_var("DEFAULT_MODEL");
    let result = f();
    std::env::remove_var("AGENT_MODEL");
    std::env::remove_var("DEFAULT_MODEL");
    drop(guard);
    result
}
