//! Model string resolution: "`provider:model_name`" -> `ResolvedModel`.
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
    /// Whether the resolved model matches the originally requested provider.
    /// `false` means a fallback was used (`AGENT_MODEL`, `DEFAULT_MODEL`, or hardcoded).
    #[serde(default)]
    pub resolved_from_requested: bool,
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
    #[must_use] 
    pub fn new(config: PlatformConfig) -> Self {
        Self { config }
    }

    /// Resolve a model string like "openai:gpt-4o" into provider + model details.
    ///
    /// Format: `"provider:model_name"` or just `"model_name"` (uses default provider).
    /// For providers like Ollama that use colons in model names (e.g. "ollama:qwen2.5-coder:7b"),
    /// only the first colon is used to split provider from model name.
    ///
    /// Resolution priority (6 levels, matching Python behaviour):
    /// 1. Explicit model string passed to `resolve()`
    /// 2. _(Agent's own config — handled at call site, not here)_
    /// 3. Platform default from config
    /// 4. Environment variable `AGENT_MODEL`
    /// 5. Environment variable `DEFAULT_MODEL`
    /// 6. Hardcoded fallback `"openai:gpt-4o"`
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn resolve(&self, model_string: &str) -> Result<ResolvedModel, ModelConfigError> {
        // Level 1: Explicit model string
        let (provider_name, model_name) = if let Some((p, m)) = model_string.split_once(':') { (p.to_string(), m.to_string()) } else {
            // Level 3: Platform default
            let default_provider =
                self.config.models.default.split(':').next().unwrap_or("openai");
            (default_provider.to_string(), model_string.to_string())
        };

        // Try to find provider config
        let provider = self.config.models.providers.get(&provider_name);
        if let Some(p) = provider { Ok(ResolvedModel {
            provider_name,
            model_name,
            base_url: p.base_url.clone(),
            api_key_env: p.api_key_env.clone(),
            api_type: p.api,
            resolved_from_requested: true,
        }) } else {
            // Levels 4-6: Try AGENT_MODEL, DEFAULT_MODEL, hardcoded fallback
            for ref fb in [
                std::env::var("AGENT_MODEL").ok(),
                std::env::var("DEFAULT_MODEL").ok(),
                Some("openai:gpt-4o".to_string()),
            ].into_iter().flatten() {
                if let Some((fb_provider, fb_model)) = fb.split_once(':') {
                    if let Some(p) = self.config.models.providers.get(fb_provider) {
                        tracing::warn!(
                            "Provider '{}' not found, falling back to '{}' provider with model '{}'",
                            provider_name, fb_provider, fb_model
                        );
                        return Ok(ResolvedModel {
                            provider_name: fb_provider.to_string(),
                            model_name: fb_model.to_string(),
                            base_url: p.base_url.clone(),
                            api_key_env: p.api_key_env.clone(),
                            api_type: p.api,
                            resolved_from_requested: false,
                        });
                    }
                }
            }
            Err(ModelConfigError::ProviderNotFound(provider_name))
        }
    }

    /// Look up an API key from an environment variable name.
    #[must_use] 
    pub fn resolve_api_key(&self, env_var: &str) -> Option<String> {
        if env_var.is_empty() {
            return None;
        }
        std::env::var(env_var).ok()
    }

    /// Return the configured default model string.
    #[must_use] 
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
                stages: HashMap::new(),
                streaming_default: true,
                providers: {
                    let mut m = HashMap::new();
                    m.insert(
                        "openai".into(),
                        ProviderConfig {
                            base_url: "https://api.openai.com/v1".into(),
                            api_key_env: "OPENAI_API_KEY".into(),
                            api: ProviderApiType::OpenaiCompatible,
                            streaming: None,
                        },
                    );
                    m.insert(
                        "deepseek".into(),
                        ProviderConfig {
                            base_url: "https://api.deepseek.com/v1".into(),
                            api_key_env: "DEEPSEEK_API_KEY".into(),
                            api: ProviderApiType::OpenaiCompatible,
                            streaming: None,
                        },
                    );
                    m.insert(
                        "ollama".into(),
                        ProviderConfig {
                            base_url: "http://localhost:11434/v1".into(),
                            api_key_env: String::new(),
                            api: ProviderApiType::Ollama,
                            streaming: None,
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
        assert!(resolved.resolved_from_requested);
    }

    #[test]
    fn resolve_unknown_provider_falls_back_to_hardcoded() {
        with_model_env(|| {
            let mgr = ModelConfigManager::new(test_config());
            let resolved = mgr.resolve("unknown:model").unwrap();
            // Falls back to hardcoded openai provider with its own model name
            assert_eq!(resolved.model_name, "gpt-4o");
            assert_eq!(resolved.provider_name, "openai");
            assert_eq!(resolved.base_url, "https://api.openai.com/v1");
            assert!(!resolved.resolved_from_requested);
        });
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

    /// Delegate to the shared lock in config::mod.rs
    fn with_model_env<F, R>(f: F) -> R
    where
        F: FnOnce() -> R,
    {
        crate::config::with_model_env(f)
    }

    #[test]
    fn resolve_env_var_fallback_agent_model() {
        with_model_env(|| {
            // AGENT_MODEL takes priority over DEFAULT_MODEL and hardcoded fallback
            std::env::set_var("AGENT_MODEL", "deepseek:deepseek-chat");
            std::env::set_var("DEFAULT_MODEL", "ollama:qwen2.5-coder:7b");
            let mgr = ModelConfigManager::new(test_config());
            let resolved = mgr.resolve("nonexistent:model").unwrap();
            assert_eq!(resolved.provider_name, "deepseek");
            assert_eq!(resolved.model_name, "deepseek-chat");
            assert_eq!(resolved.base_url, "https://api.deepseek.com/v1");
        });
    }

    #[test]
    fn resolve_env_var_fallback_default_model() {
        with_model_env(|| {
            std::env::set_var("DEFAULT_MODEL", "ollama:qwen2.5-coder:7b");
            let mgr = ModelConfigManager::new(test_config());
            let resolved = mgr.resolve("nonexistent:model").unwrap();
            assert_eq!(resolved.provider_name, "ollama");
            assert_eq!(resolved.model_name, "qwen2.5-coder:7b");
            assert_eq!(resolved.base_url, "http://localhost:11434/v1");
        });
    }

    #[test]
    fn resolve_hardcoded_fallback_when_no_env_vars() {
        with_model_env(|| {
            // No env vars, unknown provider -> hardcoded "openai:gpt-4o" fallback
            let mgr = ModelConfigManager::new(test_config());
            let resolved = mgr.resolve("nonexistent:model").unwrap();
            assert_eq!(resolved.provider_name, "openai");
            assert_eq!(resolved.model_name, "gpt-4o");
            assert_eq!(resolved.base_url, "https://api.openai.com/v1");
        });
    }

    #[test]
    fn resolve_known_provider_skips_env_fallback() {
        with_model_env(|| {
            // Even with env vars set, a known provider should resolve directly
            std::env::set_var("AGENT_MODEL", "deepseek:deepseek-chat");
            let mgr = ModelConfigManager::new(test_config());
            let resolved = mgr.resolve("openai:gpt-4o").unwrap();
            assert_eq!(resolved.provider_name, "openai");
            assert_eq!(resolved.base_url, "https://api.openai.com/v1");
        });
    }
}
