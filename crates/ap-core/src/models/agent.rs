//! Agent system models: `AgentManifest`, `AgentType`, `RunMode`, `AgentRole`, `ModelTier`.

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

impl McpServerConfig {
    /// Validate: stdio transport requires command, sse transport requires url.
    /// Python source: models/agent.py:68-83
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn validate(&self) -> Result<(), String> {
        match self.transport.as_str() {
            "stdio" if self.command.as_ref().is_none_or(|c| c.trim().is_empty()) => {
                Err("McpServerConfig with transport='stdio' requires 'command'".into())
            }
            "sse" if self.url.as_ref().is_none_or(|u| u.trim().is_empty()) => {
                Err("McpServerConfig with transport='sse' requires 'url'".into())
            }
            "stdio" | "sse" => Ok(()),
            _ => Err(format!(
                "McpServerConfig: unknown transport '{}'. Supported: 'stdio', 'sse'",
                self.transport
            )),
        }
    }
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

impl AgentManifest {
    /// Validate permission consistency.
    /// Python source: models/agent.py:125-139
    ///
    /// # Errors
    /// Returns an error if the underlying operation fails.
    pub fn validate(&self) -> Result<(), String> {
        // Check: if permission_mode is set and permissions.mode is also set, they must match
        if let (Some(mode), Some(permissions)) = (&self.permission_mode, &self.permissions) {
            if permissions.mode != *mode {
                return Err(format!(
                    "permission_mode ({:?}) conflicts with permissions.mode ({:?})",
                    mode, permissions.mode
                ));
            }
        }
        // Check: denied_tools cannot overlap with tools
        let denied_set: std::collections::HashSet<_> = self.denied_tools.iter().collect();
        for tool in &self.tools {
            if denied_set.contains(tool) {
                return Err(format!("Tool '{tool}' is in both 'tools' and 'denied_tools'"));
            }
        }
        Ok(())
    }
}

/// A SKILL.md file parsed into structured form.
///
/// Python source: models/agent.py:142-158 `SkillDefinition`
/// Superset of Python fields: includes both Python fields (agent_type, triggers,
/// compatible_agents, capabilities, body, resources) and legacy Rust fields
/// (triggers_alt, inputs, outputs, examples) via serde aliases.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SkillDefinition {
    pub name: String,
    #[serde(default)]
    pub description: String,
    /// Python: `agent_type: AgentType`. Optional in Rust for backward compat.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_type: Option<AgentType>,
    /// Python: `triggers: list[str]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub triggers: Option<Vec<String>>,
    /// Python: `compatible_agents: list[str]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compatible_agents: Option<Vec<String>>,
    /// Python: `capabilities: list[str]`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub capabilities: Option<Vec<String>>,
    /// Python: `body: str | None`. Alias `content` for legacy Rust format.
    #[serde(default, alias = "content", skip_serializing_if = "Option::is_none")]
    pub body: Option<String>,
    /// Python: `resources: str | None`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resources: Option<String>,
    // Legacy Rust-only fields (kept for backward compatibility)
    /// Legacy Rust field, alias `trigger` for old format.
    #[serde(default, alias = "trigger", skip_serializing_if = "Option::is_none")]
    pub triggers_alt: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inputs: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outputs: Option<Vec<String>>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub examples: Option<Vec<String>>,
}

/// A slash command or tool definition.
///
/// Python source: models/agent.py:161-166 `CommandDef`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CommandDef {
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub template: Option<String>,
}

/// Sub-agent definition used by Composite Agents.
///
/// Python source: models/agent.py:169-176 `AgentDefinition`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentDefinition {
    pub name: String,
    #[serde(default)]
    pub agent_ref: Option<String>,
    #[serde(default)]
    pub role: Option<String>,
    #[serde(default)]
    pub task_template: Option<String>,
}

/// Agent Package = Plugin aggregation container.
///
/// Python source: models/agent.py:183-195 `AgentPackage`
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AgentPackage {
    pub name: String,
    pub version: String,
    #[serde(rename = "type")]
    pub agent_type: AgentType,
    pub description: String,
    #[serde(default)]
    pub manifest: Option<AgentManifest>,
    #[serde(default)]
    pub skills: Vec<SkillDefinition>,
    #[serde(default)]
    pub commands: Vec<CommandDef>,
    #[serde(default)]
    pub agents: Vec<AgentDefinition>,
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
        let manifest: AgentManifest = serde_yml::from_str(yaml).unwrap();
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
        let manifest: AgentManifest = serde_yml::from_str(yaml).unwrap();
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
        let manifest: AgentManifest = serde_yml::from_str(yaml).unwrap();
        let prefs = manifest.model_preferences.unwrap();
        assert_eq!(prefs.recommended.as_deref(), Some("powerful"));
    }

    #[test]
    fn mcp_server_config_stdio_requires_command() {
        let config = McpServerConfig {
            transport: "stdio".to_string(),
            command: None,
            args: vec![],
            url: None,
        };
        assert!(config.validate().is_err());
    }

    #[test]
    fn mcp_server_config_sse_requires_url() {
        let config = McpServerConfig {
            transport: "sse".to_string(),
            command: None,
            args: vec![],
            url: None,
        };
        assert!(config.validate().is_err());
    }

    #[test]
    fn mcp_server_config_stdio_with_command_ok() {
        let config = McpServerConfig {
            transport: "stdio".to_string(),
            command: Some("python".to_string()),
            args: vec![],
            url: None,
        };
        assert!(config.validate().is_ok());
    }

    #[test]
    fn manifest_tool_deny_overlap_rejected() {
        let manifest = AgentManifest {
            name: "test".into(),
            version: "0.1.0".into(),
            agent_type: AgentType::Atomic,
            description: "test".into(),
            capabilities: vec![],
            model_preferences: None,
            role: None,
            dependencies: AgentDependencies::default(),
            permissions: None,
            tools: vec!["bash".into()],
            denied_tools: vec!["bash".into()],
            permission_mode: None,
            skills: vec![],
            hooks: std::collections::HashMap::new(),
            mcp_servers: std::collections::HashMap::new(),
            pip_dependencies: vec![],
            effort: None,
            max_turns: None,
            memory_scope: None,
            isolation: None,
            color: None,
            background: false,
            initial_prompt: None,
        };
        assert!(manifest.validate().is_err());
    }

    #[test]
    fn manifest_permission_mode_conflict_rejected() {
        let manifest = AgentManifest {
            name: "test".into(),
            version: "0.1.0".into(),
            agent_type: AgentType::Atomic,
            description: "test".into(),
            capabilities: vec![],
            model_preferences: None,
            role: None,
            dependencies: AgentDependencies::default(),
            permissions: Some(super::super::permission::PermissionConfig {
                mode: super::super::permission::PermissionMode::Plan,
                ..Default::default()
            }),
            tools: vec![],
            denied_tools: vec![],
            permission_mode: Some(super::super::permission::PermissionMode::FullAuto),
            skills: vec![],
            hooks: std::collections::HashMap::new(),
            mcp_servers: std::collections::HashMap::new(),
            pip_dependencies: vec![],
            effort: None,
            max_turns: None,
            memory_scope: None,
            isolation: None,
            color: None,
            background: false,
            initial_prompt: None,
        };
        assert!(manifest.validate().is_err());
    }
}
