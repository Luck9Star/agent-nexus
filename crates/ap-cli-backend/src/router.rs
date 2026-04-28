//! CLIRouter — 3-strategy routing with fallback.

use crate::backend::GenericCLIBackend;
use crate::registry::CLIBackendRegistry;
use crate::types::{CLIBackendError, RoutingConfig};
use std::sync::Arc;

pub struct CLIRouter {
    config: RoutingConfig,
    registry: CLIBackendRegistry,
}

impl CLIRouter {
    pub fn new(config: RoutingConfig, registry: CLIBackendRegistry) -> Self {
        Self { config, registry }
    }

    pub fn resolve(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
    ) -> Result<Arc<GenericCLIBackend>, CLIBackendError> {
        // 1. Explicit
        if let Some(name) = explicit_backend {
            return self.registry.get(name)
                .map_err(CLIBackendError::NotInstalled);
        }

        // 2. Model rules
        if let Some(model) = model_string {
            for (pattern, backend_name) in &self.config.model_rules {
                if matches_pattern(model, pattern) {
                    if let Ok(backend) = self.registry.get(backend_name) {
                        return Ok(backend);
                    }
                }
            }
        }

        // 3. Default
        self.registry.get(&self.config.default)
            .map_err(CLIBackendError::NotInstalled)
    }

    pub fn resolve_with_fallback(
        &self,
        model_string: Option<&str>,
        explicit_backend: Option<&str>,
    ) -> Result<Arc<GenericCLIBackend>, CLIBackendError> {
        let primary = self.resolve(model_string, explicit_backend);

        if let Ok(backend) = &primary {
            if backend.is_available() {
                return Ok(backend.clone());
            }
        }

        if !self.config.fallback_enabled {
            return Err(CLIBackendError::NoAvailableBackend);
        }

        for name in &self.config.fallback_chain {
            if let Ok(backend) = self.registry.get(name) {
                if backend.is_available() {
                    tracing::info!("Fallback: using backend '{}'", name);
                    return Ok(backend);
                }
            }
        }

        Err(CLIBackendError::AllBackendsUnavailable)
    }
}

fn matches_pattern(model: &str, pattern: &str) -> bool {
    if pattern.contains('*') || pattern.contains('?') {
        let regex_pattern = pattern
            .replace('.', r"\.")
            .replace('*', ".*")
            .replace('?', ".");
        regex::Regex::new(&format!("^{regex_pattern}$"))
            .map(|re| re.is_match(model))
            .unwrap_or(false)
    } else {
        model == pattern
    }
}
