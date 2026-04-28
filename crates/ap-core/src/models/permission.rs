//! Permission models: `PermissionMode`, `PermissionConfig`, `PathRule`, `PermissionDecision`.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum PermissionMode {
    #[default]
    Default,
    Plan,
    FullAuto,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "kebab-case")]
pub enum PathAccess {
    #[default]
    Read,
    Write,
    ReadWrite,
    Deny,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PathRule {
    pub pattern: String,
    #[serde(default)]
    pub access: PathAccess,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Default)]
pub struct PermissionConfig {
    #[serde(default)]
    pub mode: PermissionMode,
    #[serde(default)]
    pub allowed_tools: Vec<String>,
    #[serde(default)]
    pub denied_tools: Vec<String>,
    #[serde(default)]
    pub path_rules: Vec<PathRule>,
    #[serde(default)]
    pub denied_commands: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PermissionDecision {
    pub allowed: bool,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub requires_confirmation: bool,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_permission_config() {
        let yaml = r#"
mode: default
allowed_tools: [file_read, grep]
denied_tools: [bash]
path_rules:
  - pattern: "*.docx"
    access: read-write
  - pattern: "*.env"
    access: deny
"#;
        let config: PermissionConfig = serde_yml::from_str(yaml).unwrap();
        assert_eq!(config.mode, PermissionMode::Default);
        assert_eq!(config.allowed_tools.len(), 2);
        assert_eq!(config.denied_tools, vec!["bash"]);
        assert_eq!(config.path_rules.len(), 2);
        assert_eq!(config.path_rules[1].access, PathAccess::Deny);
    }
}
