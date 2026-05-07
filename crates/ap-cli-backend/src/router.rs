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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::BackendConfig;
    use std::collections::HashMap;

    fn echo_config(_name: &str) -> BackendConfig {
        BackendConfig {
            command: "echo".into(),
            ..Default::default()
        }
    }

    fn unavailable_config() -> BackendConfig {
        BackendConfig {
            command: "nonexistent_cmd_xyz_123".into(),
            ..Default::default()
        }
    }

    fn make_registry(backends: &[(&str, BackendConfig)]) -> CLIBackendRegistry {
        let mut reg = CLIBackendRegistry::new();
        for (name, config) in backends {
            reg.register(name.to_string(), GenericCLIBackend::new(config.clone()));
        }
        reg
    }

    fn make_routing_config(default: &str, fallback: &[&str], rules: HashMap<String, String>) -> RoutingConfig {
        RoutingConfig {
            default: default.to_string(),
            fallback_enabled: true,
            fallback_chain: fallback.iter().map(|s| s.to_string()).collect(),
            model_rules: rules,
        }
    }

    // --- matches_pattern ---

    #[test]
    fn matches_pattern_exact() {
        assert!(matches_pattern("claude-sonnet-4", "claude-sonnet-4"));
        assert!(!matches_pattern("claude-sonnet-4", "gpt-4o"));
    }

    #[test]
    fn matches_pattern_wildcard() {
        assert!(matches_pattern("claude-sonnet-4", "claude-*"));
        assert!(matches_pattern("claude-opus-3", "claude-*"));
        assert!(!matches_pattern("gpt-4o", "claude-*"));
    }

    #[test]
    fn matches_pattern_question_mark() {
        assert!(matches_pattern("gpt-4", "gpt-?"));
        assert!(!matches_pattern("gpt-4o", "gpt-?"));
    }

    // --- resolve ---

    #[test]
    fn resolve_explicit_backend() {
        let registry = make_registry(&[
            ("echo", echo_config("echo")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("echo", &[], HashMap::new()),
            registry,
        );
        let backend = router.resolve(None, Some("echo")).unwrap();
        assert_eq!(backend.name(), "echo");
    }

    #[test]
    fn resolve_explicit_not_found() {
        let registry = make_registry(&[
            ("echo", echo_config("echo")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("echo", &[], HashMap::new()),
            registry,
        );
        let result = router.resolve(None, Some("nonexistent"));
        assert!(matches!(result, Err(CLIBackendError::NotInstalled(_))));
    }

    #[test]
    fn resolve_by_model_rule() {
        let mut rules = HashMap::new();
        rules.insert("claude-*".into(), "echo".into());
        let registry = make_registry(&[
            ("echo", echo_config("echo")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("echo", &[], rules),
            registry,
        );
        let backend = router.resolve(Some("claude-sonnet-4"), None).unwrap();
        assert_eq!(backend.name(), "echo");
    }

    #[test]
    fn resolve_default_when_no_match() {
        let registry = make_registry(&[
            ("echo", echo_config("echo")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("echo", &[], HashMap::new()),
            registry,
        );
        let backend = router.resolve(Some("gpt-4o"), None).unwrap();
        assert_eq!(backend.name(), "echo");
    }

    #[test]
    fn resolve_no_default_no_backends() {
        let registry = CLIBackendRegistry::new();
        let router = CLIRouter::new(
            make_routing_config("", &[], HashMap::new()),
            registry,
        );
        let result = router.resolve(None, None);
        assert!(result.is_err());
    }

    // --- resolve_with_fallback ---

    #[test]
    fn resolve_with_fallback_primary_available() {
        let registry = make_registry(&[
            ("echo", echo_config("echo")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("echo", &[], HashMap::new()),
            registry,
        );
        let backend = router.resolve_with_fallback(None, None).unwrap();
        assert_eq!(backend.name(), "echo");
    }

    #[test]
    fn resolve_with_fallback_primary_unavailable_uses_fallback() {
        let registry = make_registry(&[
            ("primary", unavailable_config()),
            ("fallback", echo_config("fallback")),
        ]);
        let router = CLIRouter::new(
            make_routing_config("primary", &["fallback"], HashMap::new()),
            registry,
        );
        let backend = router.resolve_with_fallback(None, None).unwrap();
        assert_eq!(backend.name(), "echo"); // echo command is available
    }

    #[test]
    fn resolve_with_fallback_disabled_returns_error() {
        let registry = make_registry(&[
            ("primary", unavailable_config()),
        ]);
        let config = RoutingConfig {
            default: "primary".into(),
            fallback_enabled: false,
            fallback_chain: vec![],
            model_rules: HashMap::new(),
        };
        let router = CLIRouter::new(config, registry);
        let result = router.resolve_with_fallback(None, None);
        assert!(matches!(result, Err(CLIBackendError::NoAvailableBackend)));
    }

    #[test]
    fn resolve_with_fallback_all_unavailable() {
        let registry = make_registry(&[
            ("a", unavailable_config()),
            ("b", unavailable_config()),
        ]);
        let router = CLIRouter::new(
            make_routing_config("a", &["b"], HashMap::new()),
            registry,
        );
        let result = router.resolve_with_fallback(None, None);
        assert!(matches!(result, Err(CLIBackendError::AllBackendsUnavailable)));
    }
}
