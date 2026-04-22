//! Configuration models: ProviderConfig, ModelConfig, RuntimeConfig, PlatformConfig.

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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ModelConfig {
    #[serde(default = "default_model")]
    pub default: String,
    #[serde(default)]
    pub providers: std::collections::HashMap<String, ProviderConfig>,
}

fn default_model() -> String {
    "openai:gpt-4o".to_string()
}

impl Default for ModelConfig {
    fn default() -> Self {
        Self {
            default: default_model(),
            providers: std::collections::HashMap::new(),
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

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PlatformConfig {
    #[serde(default)]
    pub runtime: RuntimeConfig,
    #[serde(default)]
    pub models: ModelConfig,
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
    }
}
