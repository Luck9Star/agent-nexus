//! Default platform configuration values.

use crate::models::config::PlatformConfig;

/// Returns the default platform configuration.
#[must_use] 
pub fn default_config() -> PlatformConfig {
    PlatformConfig::default()
}
