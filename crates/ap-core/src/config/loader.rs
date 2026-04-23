//! `ConfigLoader`: load `config.toml` and return a `PlatformConfig`.
//!
//! Mirrors the Python `ConfigLoader.load_config()` behaviour:
//! missing fields fall back to built-in defaults via serde defaults.

use std::path::Path;

use crate::models::config::{PlatformConfig, ProviderApiType, ProviderConfig};

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("Failed to read config file: {0}")]
    Io(#[from] std::io::Error),
    #[error("Failed to parse config.toml: {0}")]
    Parse(#[from] toml::de::Error),
}

// ---------------------------------------------------------------------------
// ConfigLoader
// ---------------------------------------------------------------------------

pub struct ConfigLoader;

impl ConfigLoader {
    /// Load `PlatformConfig` from a TOML file at `path`.
    ///
    /// Missing fields fall back to their serde defaults (identical to
    /// `PlatformConfig::default()`).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn load_from_path(path: &Path) -> Result<PlatformConfig, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        Self::load_from_str(&content)
    }

    /// Parse a TOML string into `PlatformConfig`.
    ///
    /// After parsing, built-in provider defaults are merged (for any providers
    /// not already in the config) and environment variable overrides for the
    /// default model are applied (`AGENT_MODEL` > `DEFAULT_MODEL`).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn load_from_str(content: &str) -> Result<PlatformConfig, ConfigError> {
        let mut config: PlatformConfig = toml::from_str(content)?;
        apply_builtin_providers(&mut config);
        apply_env_overrides(&mut config);
        Ok(config)
    }

    /// Load from `path`, returning full defaults on any error (missing file,
    /// parse error, etc.).
    #[must_use] 
    pub fn load_or_default(path: &Path) -> PlatformConfig {
        Self::load_from_path(path).unwrap_or_default()
    }
}

// ---------------------------------------------------------------------------
// Post-load processing
// ---------------------------------------------------------------------------

/// Merge built-in provider defaults for any providers not already configured.
///
/// Mirrors Python's `DEFAULT_PROVIDERS`: openai, anthropic, deepseek, minimax, qwen, ollama.
fn apply_builtin_providers(config: &mut PlatformConfig) {
    let defaults: [(&str, ProviderConfig); 6] = [
        (
            "openai",
            ProviderConfig {
                base_url: String::new(),
                api_key_env: "OPENAI_API_KEY".into(),
                api: ProviderApiType::OpenaiCompatible,
            },
        ),
        (
            "anthropic",
            ProviderConfig {
                base_url: String::new(),
                api_key_env: "ANTHROPIC_API_KEY".into(),
                api: ProviderApiType::AnthropicMessages,
            },
        ),
        (
            "deepseek",
            ProviderConfig {
                base_url: "https://api.deepseek.com/v1".into(),
                api_key_env: "DEEPSEEK_API_KEY".into(),
                api: ProviderApiType::OpenaiCompatible,
            },
        ),
        (
            "minimax",
            ProviderConfig {
                base_url: String::new(),
                api_key_env: "MINIMAX_API_KEY".into(),
                api: ProviderApiType::OpenaiCompatible,
            },
        ),
        (
            "qwen",
            ProviderConfig {
                base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1".into(),
                api_key_env: "DASHSCOPE_API_KEY".into(),
                api: ProviderApiType::OpenaiCompatible,
            },
        ),
        (
            "ollama",
            ProviderConfig {
                base_url: "http://localhost:11434/v1".into(),
                api_key_env: String::new(),
                api: ProviderApiType::Ollama,
            },
        ),
    ];

    for (name, provider) in defaults {
        config
            .models
            .providers
            .entry(name.to_string())
            .or_insert(provider);
    }
}

/// Apply environment variable overrides for the default model.
///
/// Priority: `AGENT_MODEL` > `DEFAULT_MODEL` (both must be non-empty).
fn apply_env_overrides(config: &mut PlatformConfig) {
    if let Ok(model) = std::env::var("AGENT_MODEL") {
        if !model.is_empty() {
            config.models.default = model;
            return;
        }
    }
    if let Ok(model) = std::env::var("DEFAULT_MODEL") {
        if !model.is_empty() {
            config.models.default = model;
        }
    }
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::config::ProviderApiType;
    use std::io::Write;

    /// Write `content` to a temporary file and return the handle (keeps file
    /// alive for the duration of the test).
    fn write_temp_toml(content: &str) -> tempfile::NamedTempFile {
        let mut f = tempfile::NamedTempFile::new().unwrap();
        write!(f, "{}", content).unwrap();
        f
    }

    /// Delegate to the shared lock in config::mod.rs
    fn with_model_env<F, R>(f: F) -> R
    where
        F: FnOnce() -> R,
    {
        crate::config::with_model_env(f)
    }

    #[test]
    fn load_minimal_config() {
        with_model_env(|| {
            let f = write_temp_toml("");
            let config = ConfigLoader::load_from_path(f.path()).unwrap();
            assert_eq!(config.models.default, "openai:gpt-4o");
            assert_eq!(config.runtime.python_path, "python3");
            assert_eq!(config.runtime.uv_path, "uv");
        });
    }

    #[test]
    fn load_full_config() {
        with_model_env(|| {
            let f = write_temp_toml(
                r#"
[runtime]
python_path = "python3.12"
uv_path = "/usr/local/bin/uv"

[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
"#,
            );
            let config = ConfigLoader::load_from_path(f.path()).unwrap();
            assert_eq!(config.runtime.python_path, "python3.12");
            assert_eq!(config.models.default, "anthropic:claude-sonnet-4-20250514");
            assert!(config.models.providers.contains_key("deepseek"));
            let ds = &config.models.providers["deepseek"];
            assert_eq!(ds.base_url, "https://api.deepseek.com/v1");
            assert_eq!(ds.api_key_env, "DEEPSEEK_API_KEY");
        });
    }

    #[test]
    fn load_missing_file_returns_error() {
        with_model_env(|| {
            let result = ConfigLoader::load_from_path(Path::new("/nonexistent/config.toml"));
            assert!(result.is_err());
        });
    }

    #[test]
    fn load_or_default_missing_file() {
        with_model_env(|| {
            let config = ConfigLoader::load_or_default(Path::new("/nonexistent/config.toml"));
            assert_eq!(config.models.default, "openai:gpt-4o");
            assert_eq!(config.runtime.python_path, "python3");
        });
    }

    #[test]
    fn backward_compat_reads_python_written_config() {
        with_model_env(|| {
            // This fixture must be identical to what Python's toml.dump produces
            let f = write_temp_toml(
                r#"
[runtime]
python_path = "python3"
uv_path = "uv"

[models]
default = "ollama:qwen2.5-coder:7b"

[models.providers.ollama]
base_url = "http://localhost:11434/v1"
api = "ollama"
"#,
            );
            let config = ConfigLoader::load_from_path(f.path()).unwrap();
            assert_eq!(config.models.default, "ollama:qwen2.5-coder:7b");
            let ollama = &config.models.providers["ollama"];
            assert_eq!(ollama.api, ProviderApiType::Ollama);
        });
    }

    #[test]
    fn load_from_str_roundtrip() {
        with_model_env(|| {
            // ModelConfig::default() now includes 6 built-in providers.
            // After serialization + load_from_str, the same providers are present
            // (apply_builtin_providers merges via or_insert, so no duplicates).
            let original = PlatformConfig::default();
            let toml_str = toml::to_string(&original).unwrap();
            let loaded = ConfigLoader::load_from_str(&toml_str).unwrap();
            assert_eq!(original.runtime, loaded.runtime);
            assert_eq!(original.models.default, loaded.models.default);
            // Both original and loaded should have all 6 providers
            for key in &["openai", "anthropic", "deepseek", "minimax", "qwen", "ollama"] {
                assert!(loaded.models.providers.contains_key(*key));
            }
        });
    }

    #[test]
    fn builtin_providers_merged_for_minimal_config() {
        with_model_env(|| {
            // Loading an empty config should still have all six built-in providers
            let config = ConfigLoader::load_from_str("").unwrap();
            assert!(config.models.providers.contains_key("openai"));
            assert!(config.models.providers.contains_key("anthropic"));
            assert!(config.models.providers.contains_key("ollama"));
            assert!(config.models.providers.contains_key("deepseek"));
            assert!(config.models.providers.contains_key("minimax"));
            assert!(config.models.providers.contains_key("qwen"));
            let openai = &config.models.providers["openai"];
            assert_eq!(openai.api_key_env, "OPENAI_API_KEY");
        });
    }

    #[test]
    fn custom_provider_preserved_and_builtins_merged() {
        with_model_env(|| {
            // A config with only a custom provider should also get built-in providers
            let f = write_temp_toml(
                r#"
[models]
default = "custom:my-model"

[models.providers.custom]
base_url = "https://custom.example.com/v1"
api_key_env = "CUSTOM_API_KEY"
api = "openai-compatible"
"#,
            );
            let config = ConfigLoader::load_from_path(f.path()).unwrap();
            // Custom provider preserved
            assert!(config.models.providers.contains_key("custom"));
            assert_eq!(
                config.models.providers["custom"].base_url,
                "https://custom.example.com/v1"
            );
            // Built-in providers also present
            assert!(config.models.providers.contains_key("openai"));
            assert!(config.models.providers.contains_key("anthropic"));
            assert!(config.models.providers.contains_key("ollama"));
            assert!(config.models.providers.contains_key("deepseek"));
            assert!(config.models.providers.contains_key("minimax"));
            assert!(config.models.providers.contains_key("qwen"));
        });
    }

    #[test]
    fn user_provider_overrides_builtin_default() {
        with_model_env(|| {
            // A user-configured provider with the same name should NOT be overwritten
            let f = write_temp_toml(
                r#"
[models.providers.openai]
base_url = "https://custom-openai-proxy.example.com/v1"
api_key_env = "MY_OPENAI_KEY"
api = "openai-compatible"
"#,
            );
            let config = ConfigLoader::load_from_path(f.path()).unwrap();
            let openai = &config.models.providers["openai"];
            assert_eq!(openai.base_url, "https://custom-openai-proxy.example.com/v1");
            assert_eq!(openai.api_key_env, "MY_OPENAI_KEY");
        });
    }

    #[test]
    fn env_override_agent_model_takes_priority() {
        with_model_env(|| {
            std::env::set_var("AGENT_MODEL", "anthropic:claude-sonnet-4-20250514");
            std::env::set_var("DEFAULT_MODEL", "openai:gpt-4o-mini");
            let config = ConfigLoader::load_from_str("").unwrap();
            assert_eq!(config.models.default, "anthropic:claude-sonnet-4-20250514");
        });
    }

    #[test]
    fn env_override_default_model_fallback() {
        with_model_env(|| {
            std::env::set_var("DEFAULT_MODEL", "ollama:qwen2.5-coder:7b");
            let config = ConfigLoader::load_from_str("").unwrap();
            assert_eq!(config.models.default, "ollama:qwen2.5-coder:7b");
        });
    }

    #[test]
    fn env_override_empty_string_ignored() {
        with_model_env(|| {
            // Empty AGENT_MODEL should not override
            std::env::set_var("AGENT_MODEL", "");
            std::env::set_var("DEFAULT_MODEL", "deepseek:deepseek-chat");
            let config = ConfigLoader::load_from_str("").unwrap();
            // AGENT_MODEL is empty so it should fall through to DEFAULT_MODEL
            assert_eq!(config.models.default, "deepseek:deepseek-chat");
        });
    }
}
