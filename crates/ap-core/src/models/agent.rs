//! Agent system models: AgentManifest, AgentType, RunMode, AgentRole, ModelTier.

use serde::{Deserialize, Serialize};

use super::hooks::HookDefinition;
use super::permission::{PermissionConfig, PermissionMode};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentType {
    Atomic,
    Composite,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RunMode {
    Mcp,
    Local,
    Cli,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AgentRole {
    Explore,
    Plan,
    Worker,
    Verification,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ModelTier {
    Lightweight,
    Standard,
    Powerful,
    Premium,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentModelConfig {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub recommended: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub fallback: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct McpServerConfig {
    #[serde(default = "default_stdio")]
    pub transport: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command: Option<String>,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub url: Option<String>,
}

fn default_stdio() -> String {
    "stdio".to_string()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct AgentDependencies {
    #[serde(default)]
    pub atomic_agents: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentManifest {
    pub name: String,
    pub version: String,
    #[serde(rename = "type")]
    pub agent_type: AgentType,
    pub description: String,
    #[serde(default)]
    pub capabilities: Vec<String>,
    #[serde(alias = "model_config", skip_serializing_if = "Option::is_none")]
    pub model_preferences: Option<AgentModelConfig>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub role: Option<AgentRole>,
    #[serde(default)]
    pub dependencies: AgentDependencies,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub permissions: Option<PermissionConfig>,
    #[serde(default)]
    pub tools: Vec<String>,
    #[serde(default)]
    pub denied_tools: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub permission_mode: Option<PermissionMode>,
    #[serde(default)]
    pub skills: Vec<String>,
    #[serde(default)]
    pub hooks: std::collections::HashMap<String, Vec<HookDefinition>>,
    #[serde(default)]
    pub mcp_servers: std::collections::HashMap<String, McpServerConfig>,
    #[serde(default)]
    pub pip_dependencies: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub effort: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub max_turns: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub memory_scope: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub isolation: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub color: Option<String>,
    #[serde(default)]
    pub background: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub initial_prompt: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_minimal_manifest() {
        let yaml = r#"
name: code-reviewer
type: atomic
version: "0.1.0"
description: Code review agent
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.name, "code-reviewer");
        assert_eq!(manifest.agent_type, AgentType::Atomic);
        assert!(manifest.capabilities.is_empty());
        assert!(manifest.tools.is_empty());
    }

    #[test]
    fn parse_full_manifest() {
        let yaml = r#"
name: feature-delivery
type: composite
version: "1.0.0"
description: Feature delivery pipeline
capabilities: [code-gen, testing]
model_config:
  recommended: standard
  fallback: lightweight
permissions:
  mode: default
  allowed_tools: [file_read, grep]
  denied_tools: [bash]
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        assert_eq!(manifest.agent_type, AgentType::Composite);
        assert_eq!(manifest.capabilities.len(), 2);
        assert!(manifest.model_preferences.is_some());
        assert!(manifest.permissions.is_some());
    }

    #[test]
    fn manifest_uses_model_config_alias() {
        let yaml = r#"
name: test-agent
type: atomic
version: "0.1.0"
description: test
model_config:
  recommended: powerful
"#;
        let manifest: AgentManifest = serde_yaml::from_str(yaml).unwrap();
        let prefs = manifest.model_preferences.unwrap();
        assert_eq!(prefs.recommended.as_deref(), Some("powerful"));
    }
}
