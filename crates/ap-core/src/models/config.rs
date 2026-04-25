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
