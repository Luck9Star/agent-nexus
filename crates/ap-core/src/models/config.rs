//! Configuration models: `ProviderConfig`, `ModelConfig`, `RuntimeConfig`, `PlatformConfig`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "kebab-case")]
pub enum ProviderApiType {
    #[default]
    OpenaiCompatible,
    AnthropicMessages,
    Ollama,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ProviderConfig {
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub api_key_env: String,
    #[serde(default)]
    pub api: ProviderApiType,
}

/// A source entry for agent package distribution.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SourceEntry {
    pub name: String,
    #[serde(default = "default_source_type")]
    pub r#type: String,
    #[serde(default)]
    pub url: String,
    #[serde(default = "default_branch")]
    pub branch: String,
}

fn default_source_type() -> String {
    "git".to_string()
}
fn default_branch() -> String {
    "main".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ModelConfig {
    #[serde(default = "default_model")]
    pub default: String,
    #[serde(default)]
    pub providers: std::collections::HashMap<String, ProviderConfig>,
    #[serde(default)]
    pub stages: std::collections::HashMap<String, String>,
}

fn default_model() -> String {
    "openai:gpt-4o".to_string()
}

/// Built-in provider defaults shared between [`ModelConfig::default`] and [`crate::config::loader::apply_builtin_providers`].
///
/// Single source of truth — add new providers here only.
pub(crate) fn default_providers() -> std::collections::HashMap<String, ProviderConfig> {
    let mut providers = std::collections::HashMap::new();
    providers.insert("openai".into(), ProviderConfig {
        base_url: String::new(),
        api_key_env: "OPENAI_API_KEY".into(),
        api: ProviderApiType::OpenaiCompatible,
    });
    providers.insert("anthropic".into(), ProviderConfig {
        base_url: String::new(),
        api_key_env: "ANTHROPIC_API_KEY".into(),
        api: ProviderApiType::AnthropicMessages,
    });
    providers.insert("deepseek".into(), ProviderConfig {
        base_url: "https://api.deepseek.com/v1".into(),
        api_key_env: "DEEPSEEK_API_KEY".into(),
        api: ProviderApiType::OpenaiCompatible,
    });
    providers.insert("minimax".into(), ProviderConfig {
        base_url: String::new(),
        api_key_env: "MINIMAX_API_KEY".into(),
        api: ProviderApiType::OpenaiCompatible,
    });
    providers.insert("qwen".into(), ProviderConfig {
        base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1".into(),
        api_key_env: "DASHSCOPE_API_KEY".into(),
        api: ProviderApiType::OpenaiCompatible,
    });
    providers.insert("ollama".into(), ProviderConfig {
        base_url: "http://localhost:11434/v1".into(),
        api_key_env: String::new(),
        api: ProviderApiType::Ollama,
    });
    providers
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            default: default_model(),
            providers: default_providers(),
            stages: std::collections::HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RuntimeConfig {
    #[serde(default = "default_python")]
    pub python_path: String,
    #[serde(default = "default_uv")]
    pub uv_path: String,
}

fn default_python() -> String { "python3".to_string() }
fn default_uv() -> String { "uv".to_string() }

impl Default for RuntimeConfig {
    fn default() -> Self {
        Self {
            python_path: default_python(),
            uv_path: default_uv(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PlatformConfig {
    #[serde(default = "default_schema_version")]
    pub schema_version: String,
    #[serde(default)]
    pub runtime: RuntimeConfig,
    #[serde(default)]
    pub models: ModelConfig,
    #[serde(default)]
    pub sources: Vec<SourceEntry>,
}

fn default_schema_version() -> String {
    "1.0".to_string()
}

impl Default for PlatformConfig {
    fn default() -> Self {
        Self {
            schema_version: default_schema_version(),
            runtime: RuntimeConfig::default(),
            models: ModelConfig::default(),
            sources: Vec::new(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_config_toml() {
        let toml_str = r#"
[runtime]
python_path = "python3.12"

[models]
default = "deepseek:deepseek-chat"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
"#;
        let config: PlatformConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.runtime.python_path, "python3.12");
        assert_eq!(config.models.default, "deepseek:deepseek-chat");
        assert!(config.models.providers.contains_key("deepseek"));
    }

    #[test]
    fn default_config() {
        let config = PlatformConfig::default();
        assert_eq!(config.models.default, "openai:gpt-4o");
        assert_eq!(config.runtime.python_path, "python3");
        assert_eq!(config.schema_version, "1.0");
        assert!(config.sources.is_empty());
    }

    #[test]
    fn parse_sources_from_toml() {
        let toml_str = r#"
[[sources]]
name = "official"
type = "git"
url = "https://github.com/official/repo.git"
branch = "main"

[[sources]]
name = "private"
type = "git"
url = "https://git.example.com/private.git"
"#;
        let config: PlatformConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.sources.len(), 2);
        assert_eq!(config.sources[0].name, "official");
        assert_eq!(config.sources[0].url, "https://github.com/official/repo.git");
        assert_eq!(config.sources[1].name, "private");
    }

    #[test]
    fn parse_stages_from_toml() {
        let toml_str = r#"
[models.stages]
planning = "anthropic:claude-opus-4-20250116"
execution = "anthropic:claude-sonnet-4-20250514"
"#;
        let config: PlatformConfig = toml::from_str(toml_str).unwrap();
        assert_eq!(config.models.stages.len(), 2);
        assert_eq!(config.models.stages["planning"], "anthropic:claude-opus-4-20250116");
        assert_eq!(config.models.stages["execution"], "anthropic:claude-sonnet-4-20250514");
    }
}
