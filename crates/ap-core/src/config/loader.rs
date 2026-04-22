//! ConfigLoader: load `config.toml` and return a `PlatformConfig`.
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
    pub fn load_from_path(path: &Path) -> Result<PlatformConfig, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        Self::load_from_str(&content)
    }

    /// Parse a TOML string into `PlatformConfig`.
    pub fn load_from_str(content: &str) -> Result<PlatformConfig, ConfigError> {
        let config: PlatformConfig = toml::from_str(content)?;
        Ok(config)
    }

    /// Load from `path`, returning full defaults on any error (missing file,
    /// parse error, etc.).
    pub fn load_or_default(path: &Path) -> PlatformConfig {
        Self::load_from_path(path).unwrap_or_default()
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

    #[test]
    fn load_minimal_config() {
        let f = write_temp_toml("");
        let config = ConfigLoader::load_from_path(f.path()).unwrap();
        assert_eq!(config.models.default, "openai:gpt-4o");
        assert_eq!(config.runtime.python_path, "python3");
        assert_eq!(config.runtime.uv_path, "uv");
    }

    #[test]
    fn load_full_config() {
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
    }

    #[test]
    fn load_missing_file_returns_error() {
        let result = ConfigLoader::load_from_path(Path::new("/nonexistent/config.toml"));
        assert!(result.is_err());
    }

    #[test]
    fn load_or_default_missing_file() {
        let config = ConfigLoader::load_or_default(Path::new("/nonexistent/config.toml"));
        assert_eq!(config.models.default, "openai:gpt-4o");
        assert_eq!(config.runtime.python_path, "python3");
    }

    #[test]
    fn backward_compat_reads_python_written_config() {
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
    }

    #[test]
    fn load_from_str_roundtrip() {
        let original = PlatformConfig::default();
        let toml_str = toml::to_string(&original).unwrap();
        let loaded = ConfigLoader::load_from_str(&toml_str).unwrap();
        assert_eq!(original, loaded);
    }
}
