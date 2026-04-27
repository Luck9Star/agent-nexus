//! `ConfigLoader`: load `config.toml` and return a `PlatformConfig`.
//!
//! Mirrors the Python `ConfigLoader.load_config()` behaviour:
//! missing fields fall back to built-in defaults via serde defaults.

use std::path::Path;

use crate::models::config::PlatformConfig;

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
        let agent_model = std::env::var("AGENT_MODEL").ok();
        let default_model = std::env::var("DEFAULT_MODEL").ok();
        Self::load_from_str_with_env(content, agent_model.as_deref(), default_model.as_deref())
    }

    /// Parse a TOML string with explicit environment overrides.
    ///
    /// Like [`load_from_str`], but takes explicit `agent_model` and `default_model`
    /// parameters instead of reading from `std::env`. Use this in tests or contexts
    /// where environment state should not be read implicitly.
    ///
    /// Priority: `agent_model` > `default_model` (both must be non-empty).
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn load_from_str_with_env(
        content: &str,
        agent_model: Option<&str>,
        default_model: Option<&str>,
    ) -> Result<PlatformConfig, ConfigError> {
        let mut config: PlatformConfig = toml::from_str(content)?;
        apply_builtin_providers(&mut config);
        apply_env_overrides_explicit(&mut config, agent_model, default_model);
        Ok(config)
    }

    /// Load from `path`, returning full defaults on any error (missing file,
    /// parse error, etc.).
    #[must_use]
    pub fn load_or_default(path: &Path) -> PlatformConfig {
        match Self::load_from_path(path) {
            Ok(config) => config,
            Err(e) => {
                tracing::warn!("Failed to load config from {}: {e}, using defaults", path.display());
                PlatformConfig::default()
            }
        }
    }

    /// Load an optional project-level `agent-nexus.toml` from `project_dir`.
    ///
    /// Returns `None` when the file is missing or unreadable.
    ///
    /// TODO: Wire into CLI commands that load config (install, run, status, env).
    ///       Each command uses `find_project_root` + `root.join("config.toml")`;
    ///       replacing that with `load_merged_config(global_path, project_dir)`
    ///       would pick up project-level overrides automatically.
    pub fn load_project_config(project_dir: &Path) -> Option<PlatformConfig> {
        let project_config_path = project_dir.join("agent-nexus.toml");
        if !project_config_path.exists() {
            return None;
        }
        match Self::load_from_path(&project_config_path) {
            Ok(config) => Some(config),
            Err(e) => {
                tracing::warn!("Failed to load project config from {}: {e}", project_config_path.display());
                None
            }
        }
    }

    /// Load global config merged with optional project-level overrides.
    ///
    /// Project config values win where non-empty. Priority:
    /// env vars > project `agent-nexus.toml` > global `config.toml` > defaults.
    ///
    /// TODO: Wire into CLI commands that load config (install, run, status, env).
    ///       Each command uses `find_project_root` + `root.join("config.toml")`;
    ///       replacing that with `load_merged_config(global_path, project_dir)`
    ///       would pick up project-level overrides automatically.
    pub fn load_merged_config(global_path: &Path, project_dir: &Path) -> PlatformConfig {
        let global = Self::load_or_default(global_path);
        let Some(project) = Self::load_project_config(project_dir) else {
            return global;
        };

        // Merge: project wins where non-empty
        let merged_default = if project.models.default.is_empty() {
            global.models.default.clone()
        } else {
            project.models.default.clone()
        };

        let mut merged_providers = global.models.providers.clone();
        merged_providers.extend(project.models.providers);

        let mut merged_stages = global.models.stages.clone();
        merged_stages.extend(project.models.stages);

        // Merge sources: start with global, add project sources that don't exist yet
        let mut merged_sources = global.sources.clone();
        for ps in &project.sources {
            if let Some(existing) = merged_sources.iter_mut().find(|s| s.name == ps.name) {
                // Project overrides global source with same name
                *existing = ps.clone();
            } else {
                merged_sources.push(ps.clone());
            }
        }

        PlatformConfig {
            schema_version: global.schema_version.clone(),
            // Runtime config is global-only (python_path/uv_path don't vary per project)
            runtime: global.runtime.clone(),
            models: crate::models::config::ModelConfig {
                default: merged_default,
                providers: merged_providers,
                stages: merged_stages,
            },
            sources: merged_sources,
        }
    }
}

// ---------------------------------------------------------------------------
// Post-load processing
// ---------------------------------------------------------------------------

/// Merge built-in provider defaults for any providers not already configured.
///
/// Uses the single source of truth from [`crate::models::config::default_providers`].
fn apply_builtin_providers(config: &mut PlatformConfig) {
    for (name, provider) in crate::models::config::default_providers() {
        config
            .models
            .providers
            .entry(name)
            .or_insert(provider);
    }
}

/// Apply explicit environment variable overrides for the default model.
///
/// Priority: `agent_model` > `default_model` (both must be non-empty).
fn apply_env_overrides_explicit(
    config: &mut PlatformConfig,
    agent_model: Option<&str>,
    default_model: Option<&str>,
) {
    if let Some(model) = agent_model {
        if !model.is_empty() {
            config.models.default = model.to_string();
            return;
        }
    }
    if let Some(model) = default_model {
        if !model.is_empty() {
            config.models.default = model.to_string();
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
            let config = ConfigLoader::load_from_str_with_env(
                "",
                Some("anthropic:claude-sonnet-4-20250514"),
                Some("openai:gpt-4o-mini"),
            ).unwrap();
            assert_eq!(config.models.default, "anthropic:claude-sonnet-4-20250514");
        });
    }

    #[test]
    fn env_override_default_model_fallback() {
        with_model_env(|| {
            let config = ConfigLoader::load_from_str_with_env(
                "",
                None,
                Some("ollama:qwen2.5-coder:7b"),
            ).unwrap();
            assert_eq!(config.models.default, "ollama:qwen2.5-coder:7b");
        });
    }

    #[test]
    fn env_override_empty_string_ignored() {
        with_model_env(|| {
            // Empty agent_model should not override
            let config = ConfigLoader::load_from_str_with_env(
                "",
                Some(""),
                Some("deepseek:deepseek-chat"),
            ).unwrap();
            // agent_model is empty so it should fall through to default_model
            assert_eq!(config.models.default, "deepseek:deepseek-chat");
        });
    }
}
