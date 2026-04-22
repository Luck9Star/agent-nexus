//! Model string resolution: "provider:model_name" -> ResolvedModel.
//!
//! Python source: `src/agent_nexus/platform/config/model_config.py` (~320 lines)

use crate::models::config::{PlatformConfig, ProviderApiType};
use serde::{Deserialize, Serialize};

/// A fully resolved model with concrete provider details.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ResolvedModel {
    pub provider_name: String,
    pub model_name: String,
    pub base_url: String,
    pub api_key_env: String,
    pub api_type: ProviderApiType,
}

#[derive(Debug, thiserror::Error)]
pub enum ModelConfigError {
    #[error("No provider '{0}' configured and no default available")]
    ProviderNotFound(String),
}

pub struct ModelConfigManager {
    config: PlatformConfig,
}

impl ModelConfigManager {
    pub fn new(config: PlatformConfig) -> Self {
        Self { config }
    }

    /// Resolve a model string like "openai:gpt-4o" into provider + model details.
    ///
    /// Format: `"provider:model_name"` or just `"model_name"` (uses default provider).
    /// For providers like Ollama that use colons in model names (e.g. "ollama:qwen2.5-coder:7b"),
    /// only the first colon is used to split provider from model name.
    pub fn resolve(&self, model_string: &str) -> Result<ResolvedModel, ModelConfigError> {
        let (provider_name, model_name) = match model_string.split_once(':') {
            Some((p, m)) => (p.to_string(), m.to_string()),
            None => {
                // No colon: extract provider from default string
                let default_provider =
                    self.config.models.default.split(':').next().unwrap_or("openai");
                (default_provider.to_string(), model_string.to_string())
            }
        };

        let provider = self.config.models.providers.get(&provider_name);
        let (base_url, api_key_env, api_type) = match provider {
            Some(p) => (p.base_url.clone(), p.api_key_env.clone(), p.api),
            None => {
                // Try default provider
                let default = self.config.models.providers.get(
                    self.config
                        .models
                        .default
                        .split(':')
                        .next()
                        .unwrap_or("openai"),
                );
                match default {
                    Some(d) => (d.base_url.clone(), d.api_key_env.clone(), d.api),
                    None => (String::new(), String::new(), ProviderApiType::default()),
                }
            }
        };

        Ok(ResolvedModel {
            provider_name,
            model_name,
            base_url,
            api_key_env,
            api_type,
        })
    }

    /// Look up an API key from an environment variable name.
    pub fn resolve_api_key(&self, env_var: &str) -> Option<String> {
        if env_var.is_empty() {
            return None;
        }
        std::env::var(env_var).ok()
    }

    /// Return the configured default model string.
    pub fn default_model(&self) -> &str {
        &self.config.models.default
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use crate::models::config::{ModelConfig, ProviderConfig};

    fn test_config() -> PlatformConfig {
        PlatformConfig {
            models: ModelConfig {
                default: "openai:gpt-4o".into(),
                providers: {
                    let mut m = HashMap::new();
                    m.insert(
                        "openai".into(),
                        ProviderConfig {
                            base_url: "https://api.openai.com/v1".into(),
                            api_key_env: "OPENAI_API_KEY".into(),
                            api: ProviderApiType::OpenaiCompatible,
                        },
                    );
                    m.insert(
                        "deepseek".into(),
                        ProviderConfig {
                            base_url: "https://api.deepseek.com/v1".into(),
                            api_key_env: "DEEPSEEK_API_KEY".into(),
                            api: ProviderApiType::OpenaiCompatible,
                        },
                    );
                    m.insert(
                        "ollama".into(),
                        ProviderConfig {
                            base_url: "http://localhost:11434/v1".into(),
                            api_key_env: String::new(),
                            api: ProviderApiType::Ollama,
                        },
                    );
                    m
                },
            },
            ..Default::default()
        }
    }

    #[test]
    fn resolve_standard_model_string() {
        let mgr = ModelConfigManager::new(test_config());
        let resolved = mgr.resolve("openai:gpt-4o").unwrap();
        assert_eq!(resolved.model_name, "gpt-4o");
        assert_eq!(resolved.base_url, "https://api.openai.com/v1");
        assert_eq!(resolved.api_key_env, "OPENAI_API_KEY");
    }

    #[test]
    fn resolve_unknown_provider_returns_default() {
        let mgr = ModelConfigManager::new(test_config());
        let resolved = mgr.resolve("unknown:model").unwrap();
        // Falls back to default provider (openai)
        assert_eq!(resolved.model_name, "model");
        assert_eq!(resolved.provider_name, "unknown");
        assert_eq!(resolved.base_url, "https://api.openai.com/v1");
    }

    #[test]
    fn resolve_no_colon_uses_default_provider() {
        let mgr = ModelConfigManager::new(test_config());
        let resolved = mgr.resolve("gpt-4o-mini").unwrap();
        assert_eq!(resolved.model_name, "gpt-4o-mini");
        assert_eq!(resolved.provider_name, "openai");
        assert_eq!(resolved.base_url, "https://api.openai.com/v1");
    }

    #[test]
    fn env_var_lookup() {
        std::env::set_var("TEST_API_KEY_123", "sk-test-key");
        let mgr = ModelConfigManager::new(test_config());
        let key = mgr.resolve_api_key("TEST_API_KEY_123");
        assert_eq!(key.as_deref(), Some("sk-test-key"));
        std::env::remove_var("TEST_API_KEY_123");
    }

    #[test]
    fn resolve_ollama_no_api_key() {
        let mgr = ModelConfigManager::new(test_config());
        let resolved = mgr.resolve("ollama:qwen2.5-coder:7b").unwrap();
        assert_eq!(resolved.model_name, "qwen2.5-coder:7b");
        assert_eq!(resolved.api_type, ProviderApiType::Ollama);
        assert!(resolved.api_key_env.is_empty());
    }
}
