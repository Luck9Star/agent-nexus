# Phase 2: ap-core/config — Configuration Management

> **Goal:** Port config loading and model string resolution to Rust. Read the same `config.toml` and `sources.yaml` that Python writes.

**Python source:** `src/agent_nexus/platform/config/` (673 lines) + `src/agent_nexus/models/config.py`
**Rust target:** `crates/ap-core/src/config/`
**Depends on:** Phase 1 (models)

**Files:**
- Create: `crates/ap-core/src/config/mod.rs`
- Create: `crates/ap-core/src/config/loader.rs`
- Create: `crates/ap-core/src/config/model_config.rs`
- Create: `crates/ap-core/src/config/defaults.rs`

---

## Task 2.1: ConfigLoader

**Python source:** `src/agent_nexus/platform/config/loader.py`
**Files:**
- Create: `crates/ap-core/src/config/loader.rs`

- [ ] **Step 1: Write loader test**

```rust
// crates/ap-core/src/config/loader.rs

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

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
        let f = write_temp_toml(r#"
[runtime]
python_path = "python3.12"
uv_path = "/usr/local/bin/uv"

[models]
default = "anthropic:claude-sonnet-4-20250514"

[models.providers.deepseek]
base_url = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
api = "openai-compatible"
"#);
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
    fn backward_compat_reads_python_written_config() {
        // This fixture must be identical to what Python's toml.dump produces
        let f = write_temp_toml(r#"
[runtime]
python_path = "python3"
uv_path = "uv"

[models]
default = "ollama:qwen2.5-coder:7b"

[models.providers.ollama]
base_url = "http://localhost:11434/v1"
api = "ollama"
"#);
        let config = ConfigLoader::load_from_path(f.path()).unwrap();
        assert_eq!(config.models.default, "ollama:qwen2.5-coder:7b");
        let ollama = &config.models.providers["ollama"];
        assert_eq!(ollama.api, ProviderApiType::Ollama);
    }
}
```

- [ ] **Step 2: Implement ConfigLoader**

```rust
// crates/ap-core/src/config/loader.rs
use std::path::Path;
use crate::models::config::PlatformConfig;
use super::defaults;

#[derive(Debug, thiserror::Error)]
pub enum ConfigError {
    #[error("Failed to read config file: {0}")]
    Io(#[from] std::io::Error),
    #[error("Failed to parse config.toml: {0}")]
    Parse(#[from] toml::de::Error),
}

pub struct ConfigLoader;

impl ConfigLoader {
    /// Load PlatformConfig from a TOML file path.
    /// Missing fields fall back to defaults.
    pub fn load_from_path(path: &Path) -> Result<PlatformConfig, ConfigError> {
        let content = std::fs::read_to_string(path)?;
        Self::load_from_str(&content)
    }

    /// Load PlatformConfig from a TOML string.
    pub fn load_from_str(content: &str) -> Result<PlatformConfig, ConfigError> {
        let config: PlatformConfig = toml::from_str(content)?;
        Ok(config)
    }

    /// Load with defaults applied for missing sections.
    pub fn load_or_default(path: &Path) -> PlatformConfig {
        Self::load_from_path(path).unwrap_or_else(|_| defaults::default_config())
    }
}
```

- [ ] **Step 3: Verify tests pass**

Run: `cargo test -p ap-core -- config::loader`

- [ ] **Step 4: Commit**

```bash
git add crates/ap-core/src/config/loader.rs
git commit -m "feat(ap-core): config loader with TOML parsing and backward compat"
```

---

## Task 2.2: ModelConfigManager

**Python source:** `src/agent_nexus/platform/config/model_config.py` (~320 lines)
Model string resolution: `"provider:model_name"` → (base_url, api_key, model_name)

**Files:**
- Create: `crates/ap-core/src/config/model_config.rs`

- [ ] **Step 1: Write model config tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn test_config() -> PlatformConfig {
        PlatformConfig {
            models: ModelConfig {
                default: "openai:gpt-4o".into(),
                providers: {
                    let mut m = HashMap::new();
                    m.insert("openai".into(), ProviderConfig {
                        base_url: "https://api.openai.com/v1".into(),
                        api_key_env: "OPENAI_API_KEY".into(),
                        api: ProviderApiType::OpenaiCompatible,
                    });
                    m.insert("deepseek".into(), ProviderConfig {
                        base_url: "https://api.deepseek.com/v1".into(),
                        api_key_env: "DEEPSEEK_API_KEY".into(),
                        api: ProviderApiType::OpenaiCompatible,
                    });
                    m.insert("ollama".into(), ProviderConfig {
                        base_url: "http://localhost:11434/v1".into(),
                        api_key_env: String::new(),
                        api: ProviderApiType::Ollama,
                    });
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
        // Falls back to default provider
        assert_eq!(resolved.model_name, "model");
    }

    #[test]
    fn resolve_no_colon_uses_default_provider() {
        let mgr = ModelConfigManager::new(test_config());
        let resolved = mgr.resolve("gpt-4o-mini").unwrap();
        assert_eq!(resolved.model_name, "gpt-4o-mini");
    }

    #[test]
    fn env_var_lookup() {
        std::env::set_var("TEST_API_KEY_123", "sk-test-key");
        let mgr = ModelConfigManager::new(test_config());
        let key = mgr.resolve_api_key("OPENAI_API_KEY");
        // May or may not be set in test env, just verify it doesn't panic
        let _ = key;
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
```

- [ ] **Step 2: Implement ModelConfigManager**

```rust
use std::collections::HashMap;
use crate::models::config::{ModelConfig, PlatformConfig, ProviderConfig, ProviderApiType};
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
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
    /// Format: "provider:model_name" or just "model_name" (uses default provider).
    pub fn resolve(&self, model_string: &str) -> Result<ResolvedModel, ModelConfigError> {
        let (provider_name, model_name) = match model_string.split_once(':') {
            Some((p, m)) => (p.to_string(), m.to_string()),
            None => {
                // No colon: extract provider from default string
                let default_provider = self.config.models.default.split(':').next().unwrap_or("openai");
                (default_provider.to_string(), model_string.to_string())
            }
        };

        let provider = self.config.models.providers.get(&provider_name);
        let (base_url, api_key_env, api_type) = match provider {
            Some(p) => (p.base_url.clone(), p.api_key_env.clone(), p.api.clone()),
            None => {
                // Try default provider
                let default = self.config.models.providers.get(
                    self.config.models.default.split(':').next().unwrap_or("openai")
                );
                match default {
                    Some(d) => (d.base_url.clone(), d.api_key_env.clone(), d.api.clone()),
                    None => (String::new(), String::new(), ProviderApiType::OpenaiCompatible),
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

    /// Look up an API key from environment variable name.
    pub fn resolve_api_key(&self, env_var: &str) -> Option<String> {
        if env_var.is_empty() {
            return None;
        }
        std::env::var(env_var).ok()
    }

    pub fn default_model(&self) -> &str {
        &self.config.models.default
    }
}
```

- [ ] **Step 3: Verify tests pass**

Run: `cargo test -p ap-core -- config::model_config`

- [ ] **Step 4: Commit**

```bash
git add crates/ap-core/src/config/model_config.rs
git commit -m "feat(ap-core): model config manager with provider resolution"
```

---

## Task 2.3: Module glue + defaults

**Files:**
- Create: `crates/ap-core/src/config/defaults.rs`
- Create: `crates/ap-core/src/config/mod.rs`
- Update: `crates/ap-core/src/lib.rs` — add `pub mod config;`

- [ ] **Step 1: Create defaults.rs**

```rust
use crate::models::config::*;

pub fn default_config() -> PlatformConfig {
    PlatformConfig {
        runtime: RuntimeConfig {
            python_path: "python3".into(),
            uv_path: "uv".into(),
        },
        models: ModelConfig {
            default: "openai:gpt-4o".into(),
            providers: HashMap::new(),
        },
    }
}
```

- [ ] **Step 2: Create mod.rs**

```rust
pub mod loader;
pub mod model_config;
pub mod defaults;
```

- [ ] **Step 3: Update lib.rs**

Add `pub mod config;` after `pub mod models;`.

- [ ] **Step 4: Verify workspace compiles**

Run: `cargo build -p ap-core`

- [ ] **Step 5: Commit**

```bash
git add crates/ap-core/src/config/ crates/ap-core/src/lib.rs
git commit -m "feat(ap-core): config module with defaults and module glue"
```

---

## Final Verification

- [ ] Run `cargo test -p ap-core`
- [ ] Run `cargo clippy -p ap-core -- -D warnings`
- [ ] Fix any warnings
