//! CLI Backend Setup — load config and produce a ready-to-use (CLIRouter, CLIBackendRegistry) pair.

use std::collections::HashMap;
use std::path::Path;

use crate::backend::GenericCLIBackend;
use crate::registry::CLIBackendRegistry;
use crate::router::CLIRouter;
use crate::types::{BackendConfig, CLIBackendError, CLIResult, RoutingConfig};

/// Parsed CLI backend configuration from a TOML file.
///
/// Maps to the `[cli_backends.<name>]` and `[cli_routing]` sections.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct CLIConfigFile {
    #[serde(default)]
    cli_backends: HashMap<String, BackendConfig>,
    #[serde(default)]
    cli_routing: Option<RoutingConfig>,
}

/// Setup result: a configured router backed by a registry of backends.
pub struct CLISetup {
    pub router: CLIRouter,
    pub registry: CLIBackendRegistry,
}

impl CLISetup {
    /// Load CLI backend configuration from a TOML file.
    ///
    /// Parses `[cli_backends.<name>]` sections into `BackendConfig` instances
    /// and `[cli_routing]` into a `RoutingConfig`, then builds the registry
    /// and router.
    pub fn from_file(path: &Path) -> Result<Self, CLIBackendError> {
        let content = std::fs::read_to_string(path)
            .map_err(|e| CLIBackendError::Io(e))?;
        Self::from_str(&content)
    }

    /// Load CLI backend configuration from a TOML string.
    pub fn from_str(content: &str) -> Result<Self, CLIBackendError> {
        let config: CLIConfigFile = toml::from_str(content)
            .map_err(|e| CLIBackendError::JsonParse(format!("Config parse error: {e}")))?;

        let mut registry = CLIBackendRegistry::new();
        for (name, backend_config) in &config.cli_backends {
            let backend = GenericCLIBackend::new(backend_config.clone());
            tracing::debug!(
                "Registered CLI backend '{}' (command='{}', available={})",
                name,
                backend_config.command,
                backend.is_available()
            );
            registry.register(name.clone(), backend);
        }

        let routing = config.cli_routing.unwrap_or_else(|| RoutingConfig {
            default: registry
                .available_backends()
                .first()
                .map(|b| b.name().to_string())
                .unwrap_or_default(),
            fallback_enabled: true,
            fallback_chain: vec![],
            model_rules: HashMap::new(),
        });

        let router = CLIRouter::new(routing, registry.clone());

        Ok(Self { router, registry })
    }

    /// Load config, falling back to empty defaults on any error.
    pub fn from_file_or_default(path: &Path) -> Self {
        match Self::from_file(path) {
            Ok(setup) => setup,
            Err(e) => {
                tracing::warn!("Failed to load CLI backend config from {}: {e}", path.display());
                Self::empty()
            }
        }
    }

    /// Create an empty setup with no backends.
    pub fn empty() -> Self {
        Self {
            registry: CLIBackendRegistry::new(),
            router: CLIRouter::new(
                RoutingConfig {
                    default: String::new(),
                    fallback_enabled: false,
                    fallback_chain: vec![],
                    model_rules: HashMap::new(),
                },
                CLIBackendRegistry::new(),
            ),
        }
    }
}

/// Convenience: call an LLM through the CLI backend setup.
///
/// Resolves the backend based on model string and routing config,
/// then invokes the CLI and returns the result.
pub async fn call_llm(
    setup: &CLISetup,
    system_prompt: &str,
    user_message: &str,
    model_string: Option<&str>,
    session_id: Option<&str>,
) -> Result<CLIResult, CLIBackendError> {
    let backend = setup.router.resolve_with_fallback(model_string, None)?;
    backend.call(system_prompt, user_message, session_id).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_setup_has_no_backends() {
        let setup = CLISetup::empty();
        assert_eq!(setup.registry.len(), 0);
    }

    #[test]
    fn from_str_parses_backends() {
        let toml = r#"
[cli_backends.claude-code]
command = "claude"
args = ["-p"]
system_prompt_flag = "--system-prompt"
session_flag = "--resume"
output_format = "json"
timeout_secs = 120

[cli_backends.gemini-cli]
command = "gemini"
args = []
output_format = "text"

[cli_routing]
default = "claude-code"
fallback_chain = ["gemini-cli"]
"#;
        let setup = CLISetup::from_str(toml).unwrap();
        assert_eq!(setup.registry.len(), 2);

        let claude = setup.registry.get("claude-code").unwrap();
        assert_eq!(claude.config().command, "claude");
        assert_eq!(claude.config().timeout_secs, 120);
    }

    #[test]
    fn from_str_without_routing_uses_first_available() {
        let toml = r#"
[cli_backends.echo-test]
command = "echo"
args = []
output_format = "text"
"#;
        let setup = CLISetup::from_str(toml).unwrap();
        assert_eq!(setup.registry.len(), 1);
    }

    #[test]
    fn from_str_empty_is_valid() {
        let setup = CLISetup::from_str("").unwrap();
        assert_eq!(setup.registry.len(), 0);
    }
}
